from __future__ import annotations

import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.request_context import scoped_prefix
from app.core.minio_client import upload_file_to_minio
from app.services.bronze_parquet import (
    bronze_object_name,
    coerce_for_schema,
    enrich_rows,
    normalize_rows,
    stamp_now,
    write_bronze_file,
)
from app.services.protection_service import apply_protection_for_entity


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
    load_date, extracted_at = stamp_now()

    protected_rows = apply_protection_for_entity(entity, rows)
    enriched_rows = enrich_rows(
        normalize_rows(protected_rows),
        entity=entity,
        run_id=run_id,
        load_type=load_type,
        watermark_field=watermark_field,
        extracted_at=extracted_at,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / f"{entity}.parquet"
        if arrow_schema is not None:
            # Every file of an entity carries the same declared schema, so an
            # all-null column or an empty batch never changes the type that
            # readers see across files. Same table builder and writer call as
            # the Windows push agent (app.services.bronze_parquet).
            import pyarrow as pa

            table = pa.Table.from_pylist(coerce_for_schema(enriched_rows, arrow_schema), schema=arrow_schema)
            write_bronze_file(table, local_path)
        else:
            if enriched_rows:
                df = _fix_mixed_type_columns(pd.DataFrame(enriched_rows))
                for col in _empty_schema_columns(expected_columns):
                    if col not in df.columns:
                        df[col] = None
            else:
                df = pd.DataFrame(columns=_empty_schema_columns(expected_columns))
            df.to_parquet(local_path, index=False, engine="pyarrow", compression="snappy")

        object_name = bronze_object_name(entity, scoped_prefix(security_context), load_date, run_id)
        upload_file_to_minio(local_path=str(local_path), object_name=object_name)

    return f"s3://lakehouse/{object_name}"
