from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.sap_client import SapHcmClient
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_filters import (
    normalized_watermark,
    odata_datetime_literal,
    parse_watermark_datetime,
)
from app.services.watermark_service import get_watermark, update_watermark


logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000
WATERMARK_BUFFER_MINUTES = 5
CARTRIDGE_ID = "sap_hcm"


def _max_watermark(rows: list[dict[str, Any]], watermark_field: str | None) -> str | None:
    if not rows or not watermark_field:
        return None
    parsed = [
        parse_watermark_datetime(r.get(watermark_field))
        for r in rows
        if r.get(watermark_field) is not None
    ]
    parsed = [value for value in parsed if value is not None]
    if not parsed:
        return None
    return max(parsed).strftime("%Y-%m-%dT%H:%M:%SZ")


def _apply_watermark_filter(
    rows: list[dict[str, Any]],
    watermark_field: str,
    watermark_value: str,
) -> list[dict[str, Any]]:
    boundary = parse_watermark_datetime(watermark_value)
    if boundary is None:
        return rows
    kept: list[dict[str, Any]] = []
    for row in rows:
        value = parse_watermark_datetime(row.get(watermark_field))
        if value is None or value > boundary:
            kept.append(row)
    return kept


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
    odata_filter = config.get("odata_filter")

    if from_date or to_date:
        mode = "historical"
    else:
        mode = config.get("mode", "full")
    security_context = config.get("security_context")
    serialized_security_context = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
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
        client = SapHcmClient(security_context=serialized_security_context)

        watermark: str | None = None
        if mode == "incremental" and watermark_field:
            watermark = get_watermark(entity)

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
                security_context=security_context,
            )
            batch_num += 1
            buffer = []

        while True:
            clauses: list[str] = []
            if mode == "incremental" and watermark and watermark_field:
                literal = odata_datetime_literal(watermark)
                if literal is not None:
                    clauses.append(f"{watermark_field} gt {literal}")
            if odata_filter:
                clauses.append(f"({odata_filter})")
            filter_expr = " and ".join(clauses) if clauses else None

            page = client.fetch_entity(
                entity=config.get("odata_entity", entity),
                select=select_fields,
                page_size=page_size,
                skip=offset,
                filter_expr=filter_expr,
            )
            if not page:
                break

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

        if buffer or total_records == 0:
            _flush_buffer(allow_empty=total_records == 0)

        if mode == "incremental" and watermark_field and max_wm:
            safe_watermark = normalized_watermark(
                max_wm, backoff_minutes=WATERMARK_BUFFER_MINUTES
            )
            if safe_watermark is None:
                logger.warning(
                    "watermark no parseable para %s (%r): se conserva el anterior",
                    entity,
                    max_wm,
                )
        if mode == "incremental" and watermark_field and max_wm and safe_watermark:
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
