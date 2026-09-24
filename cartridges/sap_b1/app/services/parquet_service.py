from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.request_context import scoped_prefix
from app.core.minio_client import upload_file_to_minio
from app.services.protection_service import apply_protection_for_entity


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _normalize_value(v) for k, v in row.items()} for row in rows]


# Values that pyarrow types on its own: NUMERIC(19,6) amounts arrive as
# Decimal and are written as decimal128, dates as date32, datetimes as
# timestamps. Only genuinely mixed columns (str with numbers) fall back to
# text; casting a Decimal column to text would lose the exact amount the
# ledger reconciliation depends on.
_NATIVE_TYPES = (Decimal, date, datetime, bool, int, float)


def _fix_mixed_type_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Pyarrow rejects columns that mix str and float (NaN).
    Cast mixed object columns to string, preserving None for nulls; leave
    columns whose non-null values share one native type alone.
    """
    for col in df.columns:
        if df[col].dtype != object:
            continue
        values = [value for value in df[col].tolist() if value is not None and not (isinstance(value, float) and pd.isna(value))]
        kinds = {type(value) for value in values}
        if kinds and all(issubclass(kind, _NATIVE_TYPES) for kind in kinds) and (
            len(kinds) == 1 or kinds <= {int, float}
        ):
            continue
        df[col] = df[col].where(df[col].isna(), df[col].astype(str))
    return df


def _empty_schema_columns(expected_columns: list[str] | None = None) -> list[str]:
    columns: list[str] = []
    for col in expected_columns or []:
        if isinstance(col, str) and col and col not in columns:
            columns.append(col)
    for col in (
        "_extracted_at",
        "_run_id",
        "_source_entity",
        "_load_type",
        "_watermark_value",
    ):
        if col not in columns:
            columns.append(col)
    return columns


def _coerce_for_schema(rows: list[dict[str, Any]], schema) -> list[dict[str, Any]]:
    """Make driver values fit the declared types: a DATE into a timestamp
    column, an int or float into a decimal column. Strings stay strings."""
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


def write_parquet_and_upload(
    entity: str,
    rows: list[dict[str, Any]],
    run_id: str,
    load_type: str,
    watermark_field: str | None = None,
    expected_columns: list[str] | None = None,
    security_context: dict[str, Any] | None = None,
    arrow_schema=None,
) -> str:
    extracted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    load_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    protected_rows = apply_protection_for_entity(entity, rows)
    normalized_rows = _normalize_rows(protected_rows)

    enriched_rows = []
    for row in normalized_rows:
        enriched = dict(row)
        enriched["_extracted_at"] = extracted_at
        enriched["_run_id"] = run_id
        enriched["_source_entity"] = entity
        enriched["_load_type"] = load_type
        watermark_value = row.get(watermark_field) if watermark_field else None
        enriched["_watermark_value"] = str(watermark_value) if watermark_value is not None else None
        enriched_rows.append(enriched)

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / f"{entity}.parquet"
        if arrow_schema is not None:
            # Every file of an entity carries the same declared schema, so an
            # all-null column or an empty batch never changes the type that
            # readers see across files.
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.Table.from_pylist(_coerce_for_schema(enriched_rows, arrow_schema), schema=arrow_schema)
            pq.write_table(table, local_path, compression="snappy")
        else:
            if enriched_rows:
                df = _fix_mixed_type_columns(pd.DataFrame(enriched_rows))
                for col in _empty_schema_columns(expected_columns):
                    if col not in df.columns:
                        df[col] = None
            else:
                df = pd.DataFrame(columns=_empty_schema_columns(expected_columns))
            df.to_parquet(local_path, index=False, engine="pyarrow", compression="snappy")

        scope = scoped_prefix(security_context)
        object_name = (
            f"raw/sap_b1/{entity}/{scope}"
            f"load_date={load_date}/batch_id={run_id}/{entity}.parquet"
        )
        upload_file_to_minio(local_path=str(local_path), object_name=object_name)

    return f"s3://lakehouse/{object_name}"
