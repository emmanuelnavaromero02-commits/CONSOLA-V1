from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.salesforce_client import SalesforceClient
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_service import get_watermark, update_watermark

# Flush a parquet file every BATCH_SIZE rows. Buffer is drained after every
# OData page is appended, so memory stays bounded regardless of total volume —
# important for S/4HANA entities like JournalEntryItem (millions of rows).
BATCH_SIZE = 10_000
WATERMARK_BUFFER_MINUTES = 5
CARTRIDGE_ID = "salesforce"


def _max_watermark(rows: list[dict[str, Any]], watermark_field: str | None) -> str | None:
    if not rows or not watermark_field:
        return None
    values = [r[watermark_field] for r in rows if r.get(watermark_field) is not None]
    return max(values) if values else None


def _apply_watermark_filter(
    rows: list[dict[str, Any]],
    watermark_field: str,
    watermark_value: str,
) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get(watermark_field, "")) > watermark_value]


def _apply_date_range_filter(
    rows: list[dict[str, Any]],
    date_field: str | None,
    from_date: str | None,
    to_date: str | None,
) -> list[dict[str, Any]]:
    if not date_field or (not from_date and not to_date):
        return rows
    out = rows
    if from_date:
        out = [r for r in out if str(r.get(date_field, "")) >= from_date]
    if to_date:
        out = [r for r in out if str(r.get(date_field, "")) <= to_date]
    return out


def run_entity(
    config: dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    entity = config["entity"]
    watermark_field = config.get("watermark_field")
    page_size = config.get("page_size", 200)
    raw_select_fields = config.get("select_fields", [])
    if isinstance(raw_select_fields, (list, tuple)):
        select_fields = list(raw_select_fields)
    elif isinstance(raw_select_fields, str) and raw_select_fields:
        select_fields = [raw_select_fields]
    else:
        select_fields = []
    date_field = config.get("date_field")

    if from_date or to_date:
        mode = "historical"
    else:
        mode = config.get("mode", "full")
    expected_columns = list(dict.fromkeys([
        *(select_fields or []),
        *([watermark_field] if watermark_field else []),
        *([date_field] if date_field else []),
    ]))

    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=entity,
        run_type=mode,
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    try:
        client = SalesforceClient()

        watermark: str | None = None
        if mode == "incremental" and watermark_field:
            watermark = get_watermark(entity)

        # Streaming buffer — flushed every BATCH_SIZE rows.
        buffer: list[dict[str, Any]] = []
        offset = 0
        batch_num = 0
        storage_uri = ""
        total_records = 0
        max_wm: str | None = None

        def _flush_buffer(allow_empty: bool = False) -> None:
            nonlocal buffer, batch_num, storage_uri
            if not buffer and not allow_empty:
                return
            batch_run_id = run_id if batch_num == 0 else f"{run_id}-b{batch_num}"
            storage_uri = write_parquet_and_upload(
                entity=entity,
                rows=buffer if buffer else [],
                run_id=batch_run_id,
                load_type=mode,
                watermark_field=watermark_field,
                expected_columns=expected_columns,
            )
            batch_num += 1
            buffer = []

        while True:
            filter_expr = None
            if mode == "incremental" and watermark and watermark_field:
                filter_expr = f"{watermark_field} gt '{watermark}'"

            page = client.fetch_entity(
                entity=config.get("odata_entity", entity),
                select=select_fields,
                page_size=page_size,
                skip=offset,
                filter_expr=filter_expr,
            )
            if not page:
                break

            # Belt-and-suspenders client-side filters (the OData server
            # MIGHT have ignored $filter — re-apply locally).
            if mode == "incremental" and watermark and watermark_field:
                page = _apply_watermark_filter(page, watermark_field, watermark)
            page = _apply_date_range_filter(page, date_field, from_date, to_date)

            page_wm = _max_watermark(page, watermark_field)
            if page_wm and (max_wm is None or page_wm > max_wm):
                max_wm = page_wm

            buffer.extend(page)
            total_records += len(page)
            offset += page_size

            if len(buffer) >= BATCH_SIZE:
                _flush_buffer()

        # Drain any remainder. If we never received any rows, write an empty
        # parquet so consumers can still observe a (zero-row) Bronze artifact.
        if buffer or total_records == 0:
            _flush_buffer(allow_empty=total_records == 0)

        if mode == "incremental" and watermark_field and max_wm:
            safe_watermark = max_wm
            try:
                dt = datetime.fromisoformat(
                    max_wm.replace("Z", "+00:00").replace(" ", "T")
                )
                safe_watermark = (
                    dt - timedelta(minutes=WATERMARK_BUFFER_MINUTES)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass

            update_watermark(
                entity_name=entity,
                watermark_field=watermark_field,
                last_watermark_value=safe_watermark,
                last_run_id=run_id,
            )

        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=total_records,
            storage_uri=storage_uri,
            finished_at=datetime.now(timezone.utc),
        )

        return {
            "run_id": run_id,
            "entity": entity,
            "mode": mode,
            "record_count": total_records,
            "storage_uri": storage_uri,
            "watermark_used": watermark,
            "watermark_updated_to": max_wm,
            "batches": batch_num,
            "status": "success",
        }

    except Exception as exc:
        fail_run(
            run_id=run_id,
            error_message=str(exc),
            finished_at=datetime.now(timezone.utc),
        )
        raise
