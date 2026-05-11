from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.minio_client import upload_file_to_minio
from app.services.protection_service import apply_protection_for_entity


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _normalize_value(v) for k, v in row.items()} for row in rows]


def _fix_mixed_type_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Pyarrow rejects columns that mix str and float (NaN).
    Cast every object column to string, preserving None for nulls.
    """
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].where(df[col].isna(), df[col].astype(str))
    return df


def write_parquet_and_upload(
    entity: str,
    rows: list[dict[str, Any]],
    run_id: str,
    load_type: str,
    watermark_field: str | None = None,
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
        enriched["_watermark_value"] = row.get(watermark_field) if watermark_field else None
        enriched_rows.append(enriched)

    df = _fix_mixed_type_columns(pd.DataFrame(enriched_rows))

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / f"{entity}.parquet"
        df.to_parquet(local_path, index=False, engine="pyarrow", compression="snappy")

        object_name = (
            f"raw/replicon/{entity}/load_date={load_date}/"
            f"batch_id={run_id}/{entity}.parquet"
        )
        upload_file_to_minio(local_path=str(local_path), object_name=object_name)

    return f"s3://lakehouse/{object_name}"
