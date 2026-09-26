from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.b1_source import CARTRIDGE_ID
from app.core.minio_client import object_exists_with_prefix
from app.core.request_context import scoped_prefix
from app.services.bronze_parquet import BRONZE_PREFIX
from app.services.finance_runs_mapping import (
    COLUMNS,
    ENTITY,
    arrow_schema,
    finance_records,
    parse_finance_run,
    summary,
)
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run


def _write(records: list[dict[str, Any]], run_id: str, security_context: dict[str, Any] | None) -> str:
    return write_parquet_and_upload(
        entity=ENTITY,
        rows=records,
        run_id=run_id,
        load_type="full",
        watermark_field=None,
        expected_columns=[*COLUMNS, "_company", "_source_updated_at"],
        security_context=security_context,
        arrow_schema=arrow_schema(),
    )


def ensure_baselines(security_context: dict[str, Any] | None = None) -> list[str]:
    """Write a zero-row file for inputs that arrive later (Finance's run, the agent's counts) so their datasets can build."""
    from app.services import source_counts_mapping

    written: list[str] = []
    for entity, columns, schema in (
        (ENTITY, COLUMNS, arrow_schema()),
        (source_counts_mapping.ENTITY, source_counts_mapping.COLUMNS, source_counts_mapping.arrow_schema()),
    ):
        if object_exists_with_prefix(f"{BRONZE_PREFIX}{entity}/{scoped_prefix(security_context)}"):
            continue
        write_parquet_and_upload(
            entity=entity,
            rows=[],
            run_id="baseline",
            load_type="full",
            watermark_field=None,
            expected_columns=[*columns, "_company", "_source_updated_at"],
            security_context=security_context,
            arrow_schema=schema,
        )
        written.append(entity)
    return written


def load_finance_run(text: str, security_context: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = parse_finance_run(text)
    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=ENTITY,
        run_type="full",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    try:
        records = finance_records(rows)
        storage_uri = _write(records, run_id, security_context)
        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=len(records),
            storage_uri=storage_uri,
            finished_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        fail_run(run_id=run_id, error_message=str(exc)[:4000], finished_at=datetime.now(timezone.utc))
        raise
    return {
        "run_id": run_id,
        "entity": ENTITY,
        "record_count": len(records),
        "storage_uri": storage_uri,
        "status": "success",
        **summary(rows),
    }
