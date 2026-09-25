from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.duckdb_runtime import connect_duckdb_runtime, require_loaded_extensions


assert duckdb.__version__ == "1.2.2"
connection = connect_duckdb_runtime()
try:
    require_loaded_extensions(connection)
    installed = dict(
        connection.execute(
            "SELECT extension_name, installed FROM duckdb_extensions() "
            "WHERE extension_name IN ('httpfs','aws')"
        ).fetchall()
    )
    assert installed == {"aws": True, "httpfs": True}
    assert "aws" not in {
        str(row[0])
        for row in connection.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    }
    settings = dict(
        connection.execute(
            "SELECT name, value FROM duckdb_settings() "
            "WHERE name IN ('autoinstall_known_extensions','autoload_known_extensions')"
        ).fetchall()
    )
    assert settings == {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
    }
finally:
    connection.close()
