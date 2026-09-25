from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

PARTITION_SET_DIRECTORY = "_partitioned"
MANIFEST_FORMAT = "omega.partitioned-parquet/v1"
MANIFEST_METADATA_KEY = b"omega.partition_manifest"
MAX_PARTITIONS = 1000
PARTITION_HEADER = "-- partition_by:"
HEADER_LINES = 20

_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
PARTITION_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_NAME_RE = re.compile(r"^([0-9a-f]{64})\.parquet$")
_PART_NAME_RE = re.compile(r"^(\d{5})-([0-9a-f]{64})\.parquet$")


def partition_column_from_sql(sql: str | None) -> str | None:
    column: str | None = None
    for line in (sql or "").splitlines()[:HEADER_LINES]:
        stripped = line.strip()
        if not stripped.startswith(PARTITION_HEADER):
            continue
        declared = stripped.removeprefix(PARTITION_HEADER).strip()
        if not _COLUMN_RE.fullmatch(declared):
            raise ValueError("partition_by header must name exactly one column")
        if column is not None and column != declared:
            raise ValueError("partition_by header is declared more than once")
        column = declared
    return column


def is_manifest_key(key: str) -> bool:
    parts = str(key or "").split("/")
    return (
        len(parts) >= 2
        and parts[-2] == PARTITION_SET_DIRECTORY
        and _MANIFEST_NAME_RE.fullmatch(parts[-1]) is not None
    )


def part_key(set_key: str, column: str, value: str, index: int, digest: str) -> str:
    return f"{set_key}/{column}={value}/{index:05d}-{digest}.parquet"


def local_partition_files(root: Path, column: str) -> list[tuple[str, Path]]:
    if not root.exists():
        return []
    files: list[tuple[str, Path]] = []
    values = 0
    for directory in sorted(root.iterdir()):
        prefix = f"{column}="
        if not directory.is_dir() or not directory.name.startswith(prefix):
            raise ValueError("partitioned output has an unexpected layout")
        value = directory.name[len(prefix) :]
        if not PARTITION_VALUE_RE.fullmatch(value):
            raise ValueError(
                f"partition value of {column} is not storage-safe; "
                "use values such as 'YYYY-MM'"
            )
        values += 1
        if values > MAX_PARTITIONS:
            raise ValueError(f"{column} produces more than {MAX_PARTITIONS} partitions")
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix != ".parquet":
                raise ValueError("partitioned output has an unexpected layout")
            files.append((value, path))
    return files


def write_manifest(
    path: Path, schema: pa.Schema, column: str, parts: list[dict[str, Any]]
) -> None:
    payload = {
        "format": MANIFEST_FORMAT,
        "partition_by": column,
        "row_count": sum(int(part["rows"]) for part in parts),
        "parts": parts,
    }
    metadata = dict(schema.metadata or {})
    metadata[MANIFEST_METADATA_KEY] = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    pq.write_table(schema.with_metadata(metadata).empty_table(), path)


def manifest_payload(parquet: pq.ParquetFile) -> dict[str, Any] | None:
    raw = (parquet.metadata.metadata or {}).get(MANIFEST_METADATA_KEY)
    if raw is None:
        return None
    return json.loads(raw)


def read_manifest(raw: bytes, manifest_key: str) -> dict[str, Any]:
    if not is_manifest_key(manifest_key):
        raise ValueError("partition manifest key is invalid")
    name = manifest_key.rsplit("/", 1)[1]
    if hashlib.sha256(raw).hexdigest() != _MANIFEST_NAME_RE.fullmatch(name).group(1):
        raise ValueError("partition manifest checksum mismatch")
    parquet = pq.ParquetFile(io.BytesIO(raw))
    payload = manifest_payload(parquet)
    if not isinstance(payload, dict) or payload.get("format") != MANIFEST_FORMAT:
        raise ValueError("partition manifest format is invalid")
    column = str(payload.get("partition_by") or "")
    parts = payload.get("parts")
    if not _COLUMN_RE.fullmatch(column) or not isinstance(parts, list):
        raise ValueError("partition manifest is invalid")
    if parquet.metadata.num_rows != 0:
        raise ValueError("partition manifest must not carry rows")
    set_key = manifest_key.rsplit("/", 1)[0]
    total = 0
    values: set[str] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            raise ValueError("partition manifest part is invalid")
        value = str(part.get("value") or "")
        digest = str(part.get("checksum") or "")
        rows = part.get("rows")
        if (
            not PARTITION_VALUE_RE.fullmatch(value)
            or not _DIGEST_RE.fullmatch(digest)
            or type(rows) is not int
            or rows < 0
            or not isinstance(part.get("version"), str)
            or part.get("key") != part_key(set_key, column, value, index, digest)
        ):
            raise ValueError("partition manifest part is invalid")
        values.add(value)
        total += rows
    if len(values) > MAX_PARTITIONS or payload.get("row_count") != total:
        raise ValueError("partition manifest totals are invalid")
    return {
        "partition_by": column,
        "row_count": total,
        "parts": parts,
        "schema": parquet.schema_arrow,
    }


def schema_fields(schema: pa.Schema) -> list[dict[str, str]]:
    return [{"name": field.name, "type": str(field.type)} for field in schema]
