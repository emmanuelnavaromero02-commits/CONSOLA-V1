from __future__ import annotations

import duckdb


if duckdb.__version__ != "1.2.2":
    raise SystemExit("unexpected DuckDB version")

connection = duckdb.connect()
try:
    connection.execute("INSTALL httpfs")
finally:
    connection.close()
