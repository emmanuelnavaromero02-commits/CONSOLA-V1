from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import duckdb

DUCKDB_VERSION = "1.2.2"
REQUIRED_EXTENSIONS = {"httpfs": "httpfs", "postgres": "postgres_scanner"}
RUNTIME_CONFIG = {
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}
DUCKDB_MEMORY_LIMIT_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE
)
DUCKDB_TEMP_DIRECTORY_RE = re.compile(r"^/[^\0'\"\n\r]*$")
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
INVALID_RESOURCE_LIMITS = "DuckDB resource limits are invalid"
UNCREATABLE_TEMP_DIRECTORY = "DUCKDB_TEMP_DIRECTORY is not creatable"


@dataclass(frozen=True)
class DuckDBResourceLimits:
    memory_limit: str
    threads: int | None = None
    temp_directory: str = ""
    max_temp_directory_size: str = ""


def _sql_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def memory_limit_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_MEMORY_LIMIT_RE.fullmatch(value):
        raise ValueError("DUCKDB_MEMORY_LIMIT must look like 512MB, 1GB or 1024MiB")
    return value


def size_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", (value or "").strip())
    unit = match.group(2).lower() if match else ""
    if unit not in DUCKDB_SIZE_UNITS:
        raise ValueError("DuckDB sizes must look like 512MB, 1GB or 1024MiB")
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
        candidates.append((size_bytes(configured), configured))
    if container_bytes:
        mib = max(1, int(container_bytes * DUCKDB_CONTAINER_MEMORY_FRACTION) // 1024**2)
        candidates.append((mib * 1024**2, f"{mib}MiB"))
    if not candidates:
        return DUCKDB_FALLBACK_MEMORY_LIMIT
    return min(candidates, key=lambda item: item[0])[1]


def temp_directory_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_TEMP_DIRECTORY_RE.fullmatch(value):
        raise ValueError("DUCKDB_TEMP_DIRECTORY must be an absolute path")
    return value


def max_temp_directory_size_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_MEMORY_LIMIT_RE.fullmatch(value):
        raise ValueError(
            "DUCKDB_MAX_TEMP_DIRECTORY_SIZE must look like 512MB, 1GB or 1024MiB"
        )
    return value


def threads_from_env(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("DUCKDB_THREADS must be an integer") from exc
    if parsed < 1 or parsed > 64:
        raise ValueError("DUCKDB_THREADS must be between 1 and 64")
    return parsed


def resource_limits_from_env(
    environ: Mapping[str, str] | None = None,
) -> DuckDBResourceLimits:
    env = os.environ if environ is None else environ
    return DuckDBResourceLimits(
        memory_limit=effective_memory_limit(
            memory_limit_from_env(env.get("DUCKDB_MEMORY_LIMIT")),
            container_memory_limit_bytes(),
        ),
        threads=threads_from_env(env.get("DUCKDB_THREADS")),
        temp_directory=temp_directory_from_env(env.get("DUCKDB_TEMP_DIRECTORY")),
        max_temp_directory_size=max_temp_directory_size_from_env(
            env.get("DUCKDB_MAX_TEMP_DIRECTORY_SIZE")
        ),
    )


def _checked(limits: DuckDBResourceLimits) -> DuckDBResourceLimits:
    if not DUCKDB_MEMORY_LIMIT_RE.fullmatch(limits.memory_limit or ""):
        raise ValueError("memory_limit")
    threads = limits.threads
    if threads is not None and (
        isinstance(threads, bool)
        or not isinstance(threads, int)
        or not 1 <= threads <= 64
    ):
        raise ValueError("threads")
    if limits.temp_directory:
        temp_directory_from_env(limits.temp_directory)
    if limits.max_temp_directory_size:
        max_temp_directory_size_from_env(limits.max_temp_directory_size)
    return limits


def resource_statements(limits: DuckDBResourceLimits) -> list[str]:
    statements = [
        f"SET memory_limit={_sql_quote(limits.memory_limit)};",
        "SET preserve_insertion_order=false;",
    ]
    if limits.threads is not None:
        statements.append(f"SET threads={int(limits.threads)};")
    if limits.temp_directory:
        statements.append(f"SET temp_directory={_sql_quote(limits.temp_directory)};")
    if limits.max_temp_directory_size:
        statements.append(
            "SET max_temp_directory_size="
            f"{_sql_quote(limits.max_temp_directory_size)};"
        )
    return statements


def connect_duckdb_runtime(
    limits: DuckDBResourceLimits | None = None,
    *,
    prepare_temp_directory: bool = False,
) -> duckdb.DuckDBPyConnection:
    if duckdb.__version__ != DUCKDB_VERSION:
        raise RuntimeError("DuckDB runtime version is incompatible")
    try:
        resolved = _checked(
            limits if limits is not None else resource_limits_from_env()
        )
    except ValueError:
        raise RuntimeError(INVALID_RESOURCE_LIMITS) from None
    if prepare_temp_directory and resolved.temp_directory:
        try:
            os.makedirs(resolved.temp_directory, exist_ok=True)
        except OSError:
            raise RuntimeError(UNCREATABLE_TEMP_DIRECTORY) from None
    connection = duckdb.connect()
    try:
        for setting, value in RUNTIME_CONFIG.items():
            connection.execute(f"SET {setting}={value};")
        for extension in REQUIRED_EXTENSIONS:
            connection.execute(f"LOAD {extension};")
    except Exception:
        connection.close()
        raise RuntimeError("DuckDB required extensions are unavailable") from None
    try:
        for statement in resource_statements(resolved):
            connection.execute(statement)
    except Exception:
        connection.close()
        raise RuntimeError(INVALID_RESOURCE_LIMITS) from None
    return connection


def require_loaded_extensions(connection: duckdb.DuckDBPyConnection) -> None:
    rows = connection.execute(
        "SELECT extension_name, installed, loaded FROM duckdb_extensions() "
        "WHERE extension_name IN ('httpfs','postgres_scanner')"
    ).fetchall()
    state = {
        str(name): (bool(installed), bool(loaded)) for name, installed, loaded in rows
    }
    expected = {name: (True, True) for name in REQUIRED_EXTENSIONS.values()}
    if state != expected:
        raise RuntimeError("DuckDB required extensions are unavailable")
