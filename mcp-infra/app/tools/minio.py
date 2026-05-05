"""
MinIO MCP tools — browse the lakehouse, inspect Parquet schemas, upload specs.
"""
from __future__ import annotations

import io

from app.config import settings
from app.registry import tool


def _client():
    from minio import Minio
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


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
    c   = _client()
    bkt = bucket or settings.minio_bucket
    objs = list(c.list_objects(bkt, prefix=prefix, recursive=True))
    return {
        "objects": [
            {
                "name":          o.object_name,
                "size_bytes":    o.size,
                "last_modified": str(o.last_modified),
            }
            for o in objs[:200]
        ],
        "total":  len(objs),
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
    c   = _client()
    bkt = bucket or settings.minio_bucket
    raw = c.get_object(bkt, object_path).read()
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
    c   = _client()
    bkt = bucket or settings.minio_bucket
    raw = c.get_object(bkt, object_path).read()
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
    c   = _client()
    bkt = settings.minio_bucket
    key = f"cartridges/{cartridge_id}/specs/{filename}"
    raw = content.encode("utf-8")
    if not c.bucket_exists(bkt):
        c.make_bucket(bkt)
    c.put_object(bkt, key, io.BytesIO(raw), len(raw), content_type="text/plain")
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
    c      = _client()
    bkt    = settings.minio_bucket
    prefix = f"cartridges/{cartridge_id}/specs/"
    objs   = list(c.list_objects(bkt, prefix=prefix, recursive=True))
    return {
        "cartridge_id": cartridge_id,
        "specs": [
            {"name": o.object_name.replace(prefix, ""), "size_bytes": o.size}
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
    c   = _client()
    bkt = settings.minio_bucket
    key = f"cartridges/{cartridge_id}/specs/{filename}"
    raw = c.get_object(bkt, key).read()
    return {
        "cartridge_id": cartridge_id,
        "filename":     filename,
        "content":      raw.decode("utf-8"),
        "size_bytes":   len(raw),
    }
