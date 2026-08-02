from __future__ import annotations

import duckdb


DUCKDB_VERSION = "1.2.2"
REQUIRED_EXTENSIONS = ("httpfs",)


def require_loaded_extensions(connection: duckdb.DuckDBPyConnection) -> None:
    loaded = {
        str(row[0])
        for row in connection.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    }
    if any(name not in loaded for name in REQUIRED_EXTENSIONS):
        raise RuntimeError("DuckDB required extensions are unavailable")


def connect_duckdb_runtime() -> duckdb.DuckDBPyConnection:
    if duckdb.__version__ != DUCKDB_VERSION:
        raise RuntimeError("DuckDB runtime version is incompatible")
    connection = duckdb.connect()
    try:
        connection.execute("SET autoinstall_known_extensions=false")
        connection.execute("SET autoload_known_extensions=false")
        connection.execute("LOAD httpfs")
        require_loaded_extensions(connection)
        return connection
    except Exception as exc:
        connection.close()
        raise RuntimeError("DuckDB required extensions are unavailable") from exc
