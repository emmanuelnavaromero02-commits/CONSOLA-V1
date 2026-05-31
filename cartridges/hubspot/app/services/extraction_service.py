from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.hubspot_client import HubSpotClient
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_service import get_watermark, update_watermark

# Flush a parquet file every BATCH_SIZE rows. The cursor loop drains the buffer
# as it pages, so memory stays bounded regardless of total volume.
BATCH_SIZE = 10_000

# Safety buffer subtracted from the max watermark before persisting — guards
# against clock skew / late-arriving records. The small overlap is harmless
# because silver dedups by id keeping the latest hs_lastmodifieddate.
WATERMARK_BUFFER_MINUTES = 5
CARTRIDGE_ID = "hubspot"


def _max_watermark(rows: list[dict[str, Any]], watermark_field: str | None) -> str | None:
    if not rows or not watermark_field:
        return None
    values = [r[watermark_field] for r in rows if r.get(watermark_field) is not None]
    return max((str(v) for v in values), default=None) if values else None


def _apply_watermark_filter(
    rows: list[dict[str, Any]],
    watermark_field: str,
    watermark_value: str,
) -> list[dict[str, Any]]:
    """Client-side incremental filter. HubSpot list endpoints don't accept a
    server-side updatedAt filter, so we page the full object and filter here —
    the same approach Replicon uses for its async export."""
    return [r for r in rows if str(r.get(watermark_field, "")) > watermark_value]


def run_entity(
    config: dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """
    Extract one HubSpot object and write it to Bronze (MinIO Parquet).

    Modes:
      full        — full snapshot of the object
      incremental — client-side filter on watermark_field > last watermark
      historical  — from_date/to_date range filter on date_field (or watermark)
    """
    entity = config["entity"]
    watermark_field = config.get("watermark_field")
    date_field = config.get("date_field") or watermark_field
    security_context = config.get("security_context")

    if from_date or to_date:
        mode = "historical"
    else:
        mode = config.get("mode", "full")

    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=entity,
        run_type=mode,
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    try:
        client = HubSpotClient()

        watermark: str | None = None
        if mode == "incremental" and watermark_field:
            watermark = get_watermark(entity)

        buffer: list[dict[str, Any]] = []
        after: str | None = None
        batch_num = 0
        storage_uri = ""
        total_records = 0
        max_wm: str | None = None

        def _flush(allow_empty: bool = False) -> None:
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
                security_context=security_context,
            )
            batch_num += 1
            buffer = []

        while True:
            page, after = client.fetch_page(config, after)

            if page:
                if mode == "incremental" and watermark and watermark_field:
                    page = _apply_watermark_filter(page, watermark_field, watermark)
                if date_field and from_date:
                    page = [r for r in page if str(r.get(date_field, "")) >= from_date]
                if date_field and to_date:
                    page = [r for r in page if str(r.get(date_field, "")) <= to_date]

                page_wm = _max_watermark(page, watermark_field)
                if page_wm and (max_wm is None or page_wm > max_wm):
                    max_wm = page_wm

                buffer.extend(page)
                total_records += len(page)

                if len(buffer) >= BATCH_SIZE:
                    _flush()

            if not after:
                break

        # Drain remainder. If we never received any rows, still write an empty
        # parquet so downstream can observe a (zero-row) Bronze artifact.
        if buffer or total_records == 0:
            _flush(allow_empty=total_records == 0)

        if mode == "incremental" and watermark_field and max_wm:
            safe_watermark = max_wm
            try:
                dt = datetime.fromisoformat(
                    str(max_wm).replace("Z", "+00:00").replace(" ", "T")
                )
                safe_watermark = (
                    dt - timedelta(minutes=WATERMARK_BUFFER_MINUTES)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass  # use raw value if parsing fails

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
