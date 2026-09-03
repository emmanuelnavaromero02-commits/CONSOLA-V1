#!/usr/bin/env python3
from __future__ import annotations

import duckdb

VERSION = "1.2.2"
EXTENSIONS = ("httpfs", "postgres", "aws")


def main() -> None:
    if duckdb.__version__ != VERSION:
        raise SystemExit(f"DuckDB version mismatch: expected {VERSION}")
    connection = duckdb.connect()
    try:
        for extension in EXTENSIONS:
            connection.execute(f"INSTALL {extension};")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
