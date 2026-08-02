from __future__ import annotations

import duckdb

DUCKDB_VERSION = "1.2.2"
REQUIRED_EXTENSIONS = {"httpfs": "httpfs", "postgres": "postgres_scanner"}
RUNTIME_CONFIG = {
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}


def connect_duckdb_runtime() -> duckdb.DuckDBPyConnection:
    if duckdb.__version__ != DUCKDB_VERSION:
        raise RuntimeError("DuckDB runtime version is incompatible")
    connection = duckdb.connect()
    try:
        for setting, value in RUNTIME_CONFIG.items():
            connection.execute(f"SET {setting}={value};")
        for extension in REQUIRED_EXTENSIONS:
            connection.execute(f"LOAD {extension};")
    except Exception:
        connection.close()
        raise RuntimeError("DuckDB required extensions are unavailable") from None
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
