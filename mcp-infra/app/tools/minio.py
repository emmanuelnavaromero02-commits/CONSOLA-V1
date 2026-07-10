"""
MinIO MCP tools — browse the lakehouse, inspect Parquet schemas, upload specs.
"""
from __future__ import annotations

import io
import os
import re

from app.config import settings
from app.registry import tool
from omega_lakehouse import storage_from_env


MAX_PARQUET_READ_BYTES = int(os.environ.get("MINIO_TOOL_MAX_READ_BYTES", str(25 * 1024 * 1024)))
MAX_SPEC_BYTES = int(os.environ.get("MINIO_SPEC_MAX_BYTES", str(2 * 1024 * 1024)))
MAX_SPEC_LIST = int(os.environ.get("MINIO_SPEC_LIST_MAX", "200"))
_SAFE_CARTRIDGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_cartridge_id(cartridge_id: str) -> str:
    value = (cartridge_id or "").strip()
    if not _SAFE_CARTRIDGE_RE.fullmatch(value):
        raise ValueError("invalid cartridge_id")
    return value


def _safe_filename(filename: str) -> str:
    value = (filename or "").strip()
    if not _SAFE_FILENAME_RE.fullmatch(value) or ".." in value or "/" in value or "\\" in value:
        raise ValueError("invalid filename")
    return value


def _spec_key(cartridge_id: str, filename: str) -> str:
    return f"cartridges/{_safe_cartridge_id(cartridge_id)}/specs/{_safe_filename(filename)}"


def _storage(bucket: str | None = None):
    return storage_from_env(bucket=bucket or settings.minio_bucket)


def _read_bounded_object(storage, object_path: str, *, max_bytes: int = MAX_PARQUET_READ_BYTES) -> bytes:
    stat = storage.stat(object_path)
    size = int(getattr(stat, "size", 0) or 0)
    if size > max_bytes:
        raise ValueError(
            f"object too large for MCP preview ({size} bytes > {max_bytes} bytes)"
        )
    raw = storage.get_bytes(object_path)
    if len(raw) > max_bytes:
        raise ValueError(f"object too large for MCP preview (> {max_bytes} bytes)")
    return raw


@tool(
    name="minio_list_objects",
    description="List objects in the lakehouse at a given path prefix.",
    input_schema={
        "type": "object",
        "properties": {
            "prefix": {"type": "string", "description": "Path prefix, e.g. 'raw/replicon/User/'"},
            "bucket": {"type": "string", "description": "Bucket (default: lakehouse)"},
        },
        "required": [],
    },
)
def minio_list_objects(prefix: str = "", bucket: str | None = None) -> dict:
    bkt = bucket or settings.minio_bucket
    storage = _storage(bkt)
    objs = []
    truncated = False
    for obj in storage.iter_list(prefix):
        if len(objs) >= 200:
            truncated = True
            break
        objs.append(obj)
    return {
        "objects": [
            {
                "name":          o.key,
                "size_bytes":    o.size,
                "last_modified": str(o.updated_at),
            }
            for o in objs
        ],
        "count": len(objs),
        "truncated": truncated,
        "bucket": bkt,
        "prefix": prefix,
    }


@tool(
    name="minio_get_parquet_schema",
    description="Get the column names and types of a Parquet file stored in MinIO.",
    input_schema={
        "type": "object",
        "properties": {
            "object_path": {"type": "string", "description": "Full object key in MinIO"},
            "bucket":      {"type": "string"},
        },
        "required": ["object_path"],
    },
)
def minio_get_parquet_schema(object_path: str, bucket: str | None = None) -> dict:
    import pyarrow.parquet as pq
    bkt = bucket or settings.minio_bucket
    raw = _read_bounded_object(_storage(bkt), object_path)
    pf  = pq.ParquetFile(io.BytesIO(raw))
    schema = pf.schema_arrow
    return {
        "object_path": object_path,
        "num_rows":    pf.metadata.num_rows,
        "columns":     [{"name": f.name, "type": str(f.type)} for f in schema],
    }


@tool(
    name="minio_get_sample_rows",
    description="Read the first N rows of a Parquet file from MinIO.",
    input_schema={
        "type": "object",
        "properties": {
            "object_path": {"type": "string"},
            "n":           {"type": "integer", "description": "Rows to return (default 10)"},
            "bucket":      {"type": "string"},
        },
        "required": ["object_path"],
    },
)
def minio_get_sample_rows(object_path: str, n: int = 10, bucket: str | None = None) -> dict:
    import pyarrow.parquet as pq
    bkt = bucket or settings.minio_bucket
    n = min(max(int(n or 10), 1), 100)
    raw = _read_bounded_object(_storage(bkt), object_path)
    df  = pq.read_table(io.BytesIO(raw)).to_pandas().head(n)
    return {
        "rows":    df.to_dict(orient="records"),
        "columns": list(df.columns),
        "count":   len(df),
    }


@tool(
    name="minio_upload_spec",
    description=(
        "Upload a connector spec file (OpenAPI YAML, WSDL, OData $metadata XML, etc.) "
        "to the cartridge's spec folder in MinIO."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string", "description": "Cartridge identifier, e.g. 'replicon'"},
            "filename":     {"type": "string", "description": "File name, e.g. 'openapi.yaml'"},
            "content":      {"type": "string", "description": "File content as plain text"},
        },
        "required": ["cartridge_id", "filename", "content"],
    },
)
def minio_upload_spec(cartridge_id: str, filename: str, content: str) -> dict:
    bkt = settings.minio_bucket
    storage = _storage(bkt)
    cartridge_id = _safe_cartridge_id(cartridge_id)
    key = _spec_key(cartridge_id, filename)
    raw = content.encode("utf-8")
    if len(raw) > MAX_SPEC_BYTES:
        raise ValueError(f"spec too large ({len(raw)} bytes > {MAX_SPEC_BYTES} bytes)")
    storage.put_bytes(key, raw, overwrite=True, metadata={"content-type": "text/plain"})
    return {"uploaded": key, "size_bytes": len(raw), "cartridge_id": cartridge_id}


@tool(
    name="minio_list_cartridge_specs",
    description="List spec files that have been uploaded for a cartridge.",
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
        },
        "required": ["cartridge_id"],
    },
)
def minio_list_cartridge_specs(cartridge_id: str) -> dict:
    bkt    = settings.minio_bucket
    storage = _storage(bkt)
    cartridge_id = _safe_cartridge_id(cartridge_id)
    prefix = f"cartridges/{cartridge_id}/specs/"
    objs = []
    for obj in storage.iter_list(prefix):
        if len(objs) >= MAX_SPEC_LIST:
            break
        objs.append(obj)
    return {
        "cartridge_id": cartridge_id,
        "specs": [
            {"name": o.key.replace(prefix, ""), "size_bytes": o.size}
            for o in objs
        ],
    }


@tool(
    name="minio_read_spec",
    description="Read the content of a previously uploaded spec file.",
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "filename":     {"type": "string"},
        },
        "required": ["cartridge_id", "filename"],
    },
)
def minio_read_spec(cartridge_id: str, filename: str) -> dict:
    bkt = settings.minio_bucket
    storage = _storage(bkt)
    cartridge_id = _safe_cartridge_id(cartridge_id)
    filename = _safe_filename(filename)
    key = _spec_key(cartridge_id, filename)
    stat = storage.stat(key)
    size = int(stat.size or 0)
    if size > MAX_SPEC_BYTES:
        raise ValueError(f"spec too large ({size} bytes > {MAX_SPEC_BYTES} bytes)")
    raw = _read_bounded_object(storage, key, max_bytes=MAX_SPEC_BYTES)
    return {
        "cartridge_id": cartridge_id,
        "filename":     filename,
        "content":      raw.decode("utf-8"),
        "size_bytes":   len(raw),
    }
