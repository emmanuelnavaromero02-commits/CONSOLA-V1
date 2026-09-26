from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import duckdb


DUCKDB_VERSION = "1.2.2"
REQUIRED_EXTENSIONS = ("httpfs",)
DUCKDB_SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
UNBOUNDED_CGROUP_BYTES = 1 << 60
DUCKDB_CONTAINER_MEMORY_FRACTION = 0.7
DUCKDB_FALLBACK_MEMORY_LIMIT = "1GB"
_DUCKDB_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)")
_UNSAFE_PATH_CHARACTERS = "'\"\0\n\r"


def require_loaded_extensions(connection: duckdb.DuckDBPyConnection) -> None:
    loaded = {
        str(row[0])
        for row in connection.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    }
    if any(name not in loaded for name in REQUIRED_EXTENSIONS):
        raise RuntimeError("DuckDB required extensions are unavailable")


def duckdb_size_bytes(value: str) -> int:
    match = _DUCKDB_SIZE_RE.fullmatch((value or "").strip())
    unit = match.group(2).lower() if match else ""
    if unit not in DUCKDB_SIZE_UNITS:
        raise ValueError("DUCKDB_MEMORY_LIMIT must look like 512MB, 1GB or 1024MiB")
    return int(float(match.group(1)) * DUCKDB_SIZE_UNITS[unit])


def container_memory_limit_bytes(
    paths: tuple[str, ...] = CGROUP_MEMORY_LIMIT_FILES,
) -> int | None:
    for path in paths:
        try:
            raw = Path(path).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if raw.isdigit() and 0 < int(raw) < UNBOUNDED_CGROUP_BYTES:
            return int(raw)
        return None
    return None


def effective_memory_limit(configured: str, container_bytes: int | None) -> str:
    candidates: list[tuple[int, str]] = []
    if configured:
        candidates.append((duckdb_size_bytes(configured), configured))
    if container_bytes:
        mib = max(1, int(container_bytes * DUCKDB_CONTAINER_MEMORY_FRACTION) // 1024**2)
        candidates.append((mib * 1024**2, f"{mib}MiB"))
    if not candidates:
        return DUCKDB_FALLBACK_MEMORY_LIMIT
    return min(candidates, key=lambda item: item[0])[1]


def private_spill_directory() -> str:
    base = os.environ.get("DUCKDB_TEMP_DIRECTORY", "").strip() or os.path.join(
        tempfile.gettempdir(), "omega-duckdb-spill"
    )
    if not base.startswith("/") or any(ch in base for ch in _UNSAFE_PATH_CHARACTERS):
        raise ValueError("DUCKDB_TEMP_DIRECTORY must be an absolute path")
    os.makedirs(base, mode=0o700, exist_ok=True)
    return os.path.join(base, os.urandom(16).hex())


def apply_resource_limits(connection: duckdb.DuckDBPyConnection) -> None:
    memory_limit = effective_memory_limit(
        os.environ.get("DUCKDB_MEMORY_LIMIT", "").strip(),
        container_memory_limit_bytes(),
    )
    connection.execute("SET preserve_insertion_order=false")
    connection.execute(f"SET memory_limit='{memory_limit}'")
    connection.execute(f"SET temp_directory='{private_spill_directory()}'")


def connect_duckdb_runtime() -> duckdb.DuckDBPyConnection:
    if duckdb.__version__ != DUCKDB_VERSION:
        raise RuntimeError("DuckDB runtime version is incompatible")
    connection = duckdb.connect()
    try:
        connection.execute("SET autoinstall_known_extensions=false")
        connection.execute("SET autoload_known_extensions=false")
        connection.execute("LOAD httpfs")
        require_loaded_extensions(connection)
    except Exception as exc:
        connection.close()
        raise RuntimeError("DuckDB required extensions are unavailable") from exc
    try:
        apply_resource_limits(connection)
    except Exception:
        connection.close()
        raise RuntimeError("DuckDB resource limits are invalid") from None
    return connection
