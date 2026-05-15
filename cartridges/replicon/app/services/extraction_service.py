from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.replicon_client import RepliconClient
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_service import get_watermark, update_watermark

# Flush to Parquet every N rows — keeps memory bounded for large tables
BATCH_SIZE = 10_000

# Safety buffer subtracted from the max watermark before persisting.
# Guards against Replicon clock skew or late-arriving records.
# The small overlap is handled gracefully by upsert on dedup key.
WATERMARK_BUFFER_MINUTES = 5


def _extract_max_watermark(rows: list[dict[str, Any]], watermark_field: str | None) -> str | None:
    if not rows or not watermark_field:
        return None
    values = [r[watermark_field] for r in rows if r.get(watermark_field) is not None]
    return max(values) if values else None


def _apply_watermark_filter(
    rows: list[dict[str, Any]],
    watermark_field: str,
    watermark_value: str,
) -> list[dict[str, Any]]:
    """
    Client-side watermark filter for incremental loads.

    Replicon's download-type extract does not support server-side date filters,
    so we fetch the full table and filter here. For large tables configure
    the BigQuery target instead and rely on partitioned filters there.
    """
    return [r for r in rows if str(r.get(watermark_field, "")) > watermark_value]


def run_entity(
    config: dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """
    Extract one Replicon entity (table) and write it to Bronze (MinIO Parquet).

    Modes:
      full        — full snapshot of the table
      incremental — client-side filter on watermark_field > last watermark
      historical  — triggered by explicit from_date / to_date (sets mode label)
    """
    entity = config["entity"]
    watermark_field = config.get("watermark_field")

    if from_date or to_date:
        mode = "historical"
    else:
        mode = config.get("mode", "full")

    run_id = create_run(
        cartridge_id="replicon",
        entity_name=entity,
        run_type=mode,
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    try:
        client = RepliconClient()

        # Retrieve last watermark for incremental loads
        watermark: str | None = None
        if mode == "incremental" and watermark_field:
            watermark = get_watermark(entity)

        # ------------------------------------------------------------------
        # Extract full table from Replicon, then split into batches.
        # The API is async (POST /extracts → poll → download CSV) so we get
        # all rows at once; batching happens on the write side.
        # ------------------------------------------------------------------
        all_rows = client.extract_table(entity)

        # Client-side incremental filter
        if mode == "incremental" and watermark and watermark_field:
            all_rows = _apply_watermark_filter(all_rows, watermark_field, watermark)

        # Optional date range filter on a date field (historical mode)
        date_field = config.get("date_field")
        if date_field and from_date:
            all_rows = [r for r in all_rows if str(r.get(date_field, "")) >= from_date]
        if date_field and to_date:
            all_rows = [r for r in all_rows if str(r.get(date_field, "")) <= to_date]

        # ------------------------------------------------------------------
        # Write in batches to avoid large single Parquet files
        # ------------------------------------------------------------------
        storage_uri = ""
        batch_num = 0
        max_watermark: str | None = None

        def _flush(batch: list, num: int) -> str:
            batch_run_id = run_id if num == 0 else f"{run_id}-b{num}"
            return write_parquet_and_upload(
                entity=entity,
                rows=batch,
                run_id=batch_run_id,
                load_type=mode,
                watermark_field=watermark_field,
            )

        for i in range(0, max(len(all_rows), 1), BATCH_SIZE):
            batch = all_rows[i : i + BATCH_SIZE]

            page_wm = _extract_max_watermark(batch, watermark_field)
            if page_wm and (max_watermark is None or page_wm > max_watermark):
                max_watermark = page_wm

            storage_uri = _flush(batch, batch_num)
            batch_num += 1

        total_records = len(all_rows)

        # ------------------------------------------------------------------
        # Update watermark with safety buffer
        # ------------------------------------------------------------------
        if mode == "incremental" and watermark_field and max_watermark:
            safe_watermark = max_watermark
            try:
                dt = datetime.fromisoformat(
                    max_watermark.replace("Z", "+00:00").replace(" ", "T")
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
            "watermark_updated_to": max_watermark,
            "status": "success",
        }

    except Exception as exc:
        fail_run(
            run_id=run_id,
            error_message=str(exc),
            finished_at=datetime.now(timezone.utc),
        )
        raise
