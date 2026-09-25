from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.b1_source import CARTRIDGE_ID
from app.core.vault_client import get_secret_for_worker
from app.services.business_parameters_mapping import (
    COLUMNS,
    ENTITY,
    arrow_schema,
    parameter_records,
    parse_business_parameters,
)
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run


def refresh_business_parameters(security_context: dict[str, Any] | None = None) -> dict[str, Any]:
    serialized = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=ENTITY,
        run_type="full",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    try:
        spec = get_secret_for_worker(CARTRIDGE_ID, "SAP_B1_BUSINESS_PARAMETERS", security_context=serialized)
        rows = parameter_records(parse_business_parameters(spec))
        storage_uri = write_parquet_and_upload(
            entity=ENTITY,
            rows=rows,
            run_id=run_id,
            load_type="full",
            watermark_field=None,
            expected_columns=[*COLUMNS, "_company", "_source_updated_at"],
            security_context=security_context,
            arrow_schema=arrow_schema(),
        )
        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=len(rows),
            storage_uri=storage_uri,
            finished_at=datetime.now(timezone.utc),
        )
        return {
            "run_id": run_id,
            "entity": ENTITY,
            "mode": "full",
            "record_count": len(rows),
            "storage_uri": storage_uri,
            "status": "success",
        }
    except Exception as exc:
        fail_run(run_id=run_id, error_message=str(exc)[:4000], finished_at=datetime.now(timezone.utc))
        raise
