from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.services.b1_queries import METADATA_COLUMNS

CARTRIDGE_ID = "sap_b1"
BRONZE_PREFIX = f"raw/{CARTRIDGE_ID}/"
EXTRACTED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOAD_DATE_FORMAT = "%Y-%m-%d"
_SAFE_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.:-]+")


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _normalize_value(v) for k, v in row.items()} for row in rows]


def enrich_rows(
    rows: list[dict[str, Any]],
    *,
    entity: str,
    run_id: str,
    load_type: str,
    watermark_field: str | None,
    extracted_at: str,
) -> list[dict[str, Any]]:
    enriched_rows = []
    for row in rows:
        enriched = dict(row)
        enriched["_extracted_at"] = extracted_at
        enriched["_run_id"] = run_id
        enriched["_source_entity"] = entity
        enriched["_load_type"] = load_type
        watermark_value = row.get(watermark_field) if watermark_field else None
        enriched["_watermark_value"] = str(watermark_value) if watermark_value is not None else None
        enriched_rows.append(enriched)
    return enriched_rows


def coerce_for_schema(rows: list[dict[str, Any]], schema) -> list[dict[str, Any]]:
    import pyarrow as pa

    timestamp_columns = {field.name for field in schema if pa.types.is_timestamp(field.type)}
    decimal_columns = {field.name for field in schema if pa.types.is_decimal(field.type)}
    out = []
    for row in rows:
        fixed = dict(row)
        for name in timestamp_columns:
            value = fixed.get(name)
            if isinstance(value, date) and not isinstance(value, datetime):
                fixed[name] = datetime(value.year, value.month, value.day)
        for name in decimal_columns:
            value = fixed.get(name)
            if value is not None and not isinstance(value, Decimal):
                fixed[name] = Decimal(str(value))
        out.append(fixed)
    return out


def bronze_table(
    rows: list[dict[str, Any]],
    *,
    schema,
    entity: str,
    run_id: str,
    load_type: str,
    watermark_field: str | None,
    extracted_at: str,
):
    import pyarrow as pa

    enriched = enrich_rows(
        normalize_rows(rows),
        entity=entity,
        run_id=run_id,
        load_type=load_type,
        watermark_field=watermark_field,
        extracted_at=extracted_at,
    )
    return pa.Table.from_pylist(coerce_for_schema(enriched, schema), schema=schema)


def write_bronze_file(table, path) -> None:
    import pyarrow.parquet as pq

    pq.write_table(table, path, compression="snappy")


def stamp_now(now: datetime | None = None) -> tuple[str, str]:
    moment = now or datetime.now(timezone.utc)
    return moment.strftime(LOAD_DATE_FORMAT), moment.strftime(EXTRACTED_AT_FORMAT)


def scope_prefix(tenant_id: str, workspace_id: str) -> str:
    tenant = str(tenant_id or "").strip()
    workspace = str(workspace_id or "").strip()
    if not (tenant and workspace):
        raise ValueError("tenant_id and workspace_id are required")
    for value in (tenant, workspace):
        if not _SAFE_SCOPE_SEGMENT.fullmatch(value):
            raise ValueError("tenant_id and workspace_id may only contain letters, digits, '_', '.', ':' and '-'")
    return f"tenant_id={tenant}/workspace_id={workspace}/"


def bronze_object_name(entity: str, scope: str, load_date: str, run_id: str) -> str:
    return f"{BRONZE_PREFIX}{entity}/{scope}load_date={load_date}/batch_id={run_id}/{entity}.parquet"


__all__ = [
    "BRONZE_PREFIX",
    "CARTRIDGE_ID",
    "METADATA_COLUMNS",
    "bronze_object_name",
    "bronze_table",
    "coerce_for_schema",
    "enrich_rows",
    "normalize_rows",
    "scope_prefix",
    "stamp_now",
    "write_bronze_file",
]
