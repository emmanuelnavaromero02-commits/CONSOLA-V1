from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.b1_source import B1Client
from app.services.b1_queries import arrow_schema, plan_from_config
from app.services.b1_reader import (
    BATCH_SIZE,
    WATERMARK_BUFFER_MINUTES,
    effective_mode,
    read_entity,
)
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_service import get_watermark, update_watermark

logger = logging.getLogger(__name__)

CARTRIDGE_ID = "sap_b1"

__all__ = ["BATCH_SIZE", "WATERMARK_BUFFER_MINUTES", "run_entity"]


def run_entity(
    config: dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    plan = plan_from_config(config)
    entity = plan.entity
    mode = effective_mode(config, plan, from_date, to_date)
    if mode == "historical" and not plan.date_field:
        raise ValueError(f"{entity}: historical mode needs a date_field")

    security_context = config.get("security_context")
    serialized_security_context = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
    expected_columns = list(plan.output_columns)
    schema = arrow_schema(plan)

    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=entity,
        run_type=mode,
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    try:
        client = B1Client(security_context=serialized_security_context)
        client.require_configured()

        delivered = {"batches": 0, "storage_uri": ""}

        def _write_batch(records: list[dict[str, Any]]) -> None:
            batch_run_id = run_id if delivered["batches"] == 0 else f"{run_id}-b{delivered['batches']}"
            delivered["storage_uri"] = write_parquet_and_upload(
                entity=entity,
                rows=records,
                run_id=batch_run_id,
                load_type=mode,
                watermark_field=plan.watermark_field,
                expected_columns=expected_columns,
                security_context=security_context,
                arrow_schema=schema,
            )
            delivered["batches"] += 1

        def _update_watermark(key: str, value: str) -> None:
            update_watermark(
                entity_name=key,
                watermark_field=plan.watermark_field or "",
                last_watermark_value=value,
                last_run_id=run_id,
            )

        with client.connection() as connection:
            result = read_entity(
                plan,
                connection,
                client.companies,
                mode=mode,
                get_watermark=get_watermark,
                update_watermark=_update_watermark,
                write_batch=_write_batch,
                from_date=from_date,
                to_date=to_date,
                batch_size=BATCH_SIZE,
                buffer_minutes=WATERMARK_BUFFER_MINUTES,
            )

        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=result.total_records,
            storage_uri=delivered["storage_uri"],
            finished_at=datetime.now(timezone.utc),
        )

        return {
            "run_id": run_id,
            "entity": entity,
            "mode": mode,
            "record_count": result.total_records,
            "storage_uri": delivered["storage_uri"],
            "batches": result.batches,
            "companies": result.companies,
            "status": "success",
        }

    except Exception as exc:
        fail_run(
            run_id=run_id,
            error_message=str(exc)[:4000],
            finished_at=datetime.now(timezone.utc),
        )
        raise
