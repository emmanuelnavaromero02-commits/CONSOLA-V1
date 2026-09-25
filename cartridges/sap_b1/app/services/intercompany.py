"""Intercompany partners: which business-partner codes are group companies."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.b1_source import CARTRIDGE_ID, resolve_config
from app.core.vault_client import get_secret_for_worker
from app.services.intercompany_mapping import (
    COLUMNS,
    ENTITY,
    IntercompanyPartner,
    arrow_schema,
    parse_intercompany,
    partner_records,
    validate_against_companies,
)
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run

logger = logging.getLogger(__name__)

__all__ = [
    "COLUMNS",
    "ENTITY",
    "IntercompanyPartner",
    "arrow_schema",
    "parse_intercompany",
    "partner_records",
    "refresh_intercompany_partners",
    "resolve_intercompany",
    "validate_against_companies",
]


def resolve_intercompany(security_context: str | None = None) -> list[IntercompanyPartner]:
    """The configured mapping, validated against the configured companies."""
    ctx = (security_context or "").strip() or None
    spec = get_secret_for_worker(CARTRIDGE_ID, "SAP_B1_INTERCOMPANY", security_context=ctx)
    partners = parse_intercompany(spec)
    config = resolve_config(ctx)
    validate_against_companies(partners, (company.alias for company in config.companies))
    return partners


def refresh_intercompany_partners(security_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the configured mapping to Bronze as a full snapshot."""
    import json

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
        partners = resolve_intercompany(serialized)
        rows = partner_records(partners)
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
            "companies": sorted({p.company for p in partners}),
            "status": "success",
        }
    except Exception as exc:
        fail_run(run_id=run_id, error_message=str(exc)[:4000], finished_at=datetime.now(timezone.utc))
        raise
