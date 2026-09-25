from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

from . import schema as b1
from .generator import Dataset

# Business One on SQL Server keeps every company in its own database, tables under dbo.
_TYPE_RULES = (
    (re.compile(r"^INTEGER\b", re.IGNORECASE), "INT"),
    (re.compile(r"^SMALLINT\b", re.IGNORECASE), "SMALLINT"),
    (re.compile(r"^BIGINT\b", re.IGNORECASE), "BIGINT"),
    (re.compile(r"^TIMESTAMP\(0\)", re.IGNORECASE), "DATETIME2(0)"),
    (re.compile(r"^TIMESTAMP\b", re.IGNORECASE), "DATETIME2"),
    (re.compile(r"^DATE\b", re.IGNORECASE), "DATE"),
    (re.compile(r"^NUMERIC\((\d+)\s*,\s*(\d+)\)", re.IGNORECASE), r"NUMERIC(\1,\2)"),
    (re.compile(r"^DECIMAL\((\d+)\s*,\s*(\d+)\)", re.IGNORECASE), r"DECIMAL(\1,\2)"),
    (re.compile(r"^VARCHAR\((\d+)\)", re.IGNORECASE), r"NVARCHAR(\1)"),
    (re.compile(r"^CHAR\((\d+)\)", re.IGNORECASE), r"NCHAR(\1)"),
    (re.compile(r"^TEXT\b", re.IGNORECASE), "NVARCHAR(MAX)"),
)
_CONSTRAINTS = re.compile(r"^(\s+NOT\s+NULL|\s+NULL)?\s*$", re.IGNORECASE)
_DATABASE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,127}$")


def translate_type(ddl_type: str) -> str:
    text = " ".join(str(ddl_type).split())
    for pattern, replacement in _TYPE_RULES:
        match = pattern.match(text)
        if match:
            rest = text[match.end():]
            if not _CONSTRAINTS.fullmatch(rest):
                raise ValueError(f"unsupported column constraint in {ddl_type!r}")
            return match.expand(replacement) + (" NOT NULL" if "NOT NULL" in rest.upper() else "")
    raise ValueError(f"no SQL Server type for {ddl_type!r}")


def quote_database(name: str) -> str:
    if not _DATABASE_RE.fullmatch(name or ""):
        raise ValueError(f"invalid database name: {name!r}")
    return f'"{name}"'


def render_tables() -> List[str]:
    statements: List[str] = []
    primary_keys = getattr(b1, "PRIMARY_KEYS", {})
    for table in b1.TABLES:
        body = [f"    {b1.quote(name)} {translate_type(ctype)}" for name, ctype in b1.TABLES[table]]
        key = primary_keys.get(table)
        if key:
            body.append(
                f"    CONSTRAINT {b1.quote(f'{table}_PRIMARY')} PRIMARY KEY ("
                + ", ".join(b1.quote(column) for column in key)
                + ")"
            )
        statements.append(f"CREATE TABLE \"dbo\".{b1.quote(table)} (\n" + ",\n".join(body) + "\n)")
    return statements


def _sql_text(value: str) -> str:
    return "N'" + str(value).replace("'", "''") + "'"


def load(
    connect: Callable[[], Any],
    dataset: Dataset,
    *,
    reader: tuple[str, str] | None = None,
    batch_size: int = 2000,
) -> Dict[str, Dict[str, int]]:
    """Create one database per company and bulk-load the fake world; `connect` opens an admin connection."""
    loaded: Dict[str, Dict[str, int]] = {}
    conn = connect()
    try:
        conn.autocommit = True
        cursor = conn.cursor()
        if reader is not None:
            login, password = reader
            cursor.execute(
                f"IF SUSER_ID({_sql_text(login)}) IS NULL "
                f"CREATE LOGIN {b1.quote(login)} WITH PASSWORD = {_sql_text(password)}, CHECK_POLICY = OFF"
            )
        for company in dataset.companies:
            database = quote_database(company.schema)
            cursor.execute(f"IF DB_ID({_sql_text(company.schema)}) IS NOT NULL DROP DATABASE {database}")
            cursor.execute(f"CREATE DATABASE {database}")
            cursor.execute(f"USE {database}")
            for statement in render_tables():
                cursor.execute(statement)
            counts: Dict[str, int] = {}
            rows = dataset.tables[company.alias]
            for table in b1.TABLES:
                values = rows.get(table) or []
                if values:
                    columns = b1.columns(table)
                    insert = (
                        f"INSERT INTO \"dbo\".{b1.quote(table)} ({', '.join(b1.quote(c) for c in columns)}) "
                        f"VALUES ({', '.join('?' for _ in columns)})"
                    )
                    cursor.fast_executemany = True
                    for start in range(0, len(values), batch_size):
                        cursor.executemany(insert, [tuple(row) for row in values[start : start + batch_size]])
                counts[table] = len(values)
            if reader is not None:
                login = reader[0]
                cursor.execute(f"CREATE USER {b1.quote(login)} FOR LOGIN {b1.quote(login)}")
                cursor.execute(f"ALTER ROLE db_datareader ADD MEMBER {b1.quote(login)}")
            loaded[company.alias] = counts
        cursor.execute("USE master")
        cursor.close()
    finally:
        conn.close()
    return loaded


def scalar(connect: Callable[[], Any], sql: str, params: tuple = ()) -> Any:
    conn = connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    finally:
        conn.close()
    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(f"expected one value, got {len(rows)} rows")
    return rows[0][0]


def execute(connect: Callable[[], Any], sql: str, params: tuple = ()) -> None:
    conn = connect()
    try:
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(sql, params)
        cursor.close()
    finally:
        conn.close()
