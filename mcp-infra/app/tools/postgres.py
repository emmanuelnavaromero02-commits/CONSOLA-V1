"""
PostgreSQL MCP tools — schema discovery, read queries, DDL execution.
Works against both main DB (modecissions) and gold DB (modecissions_gold).
"""
from __future__ import annotations

import re

import psycopg2
import psycopg2.extras

from app.config import settings
from app.registry import tool


_IDENT = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[^"\x00]+")'
_QUALIFIED_IDENT = rf"{_IDENT}(?:\s*\.\s*{_IDENT})?"
_COLUMN_LIST = rf"{_IDENT}(?:\s*,\s*{_IDENT})*"
_SQL_COMMENT_RE = re.compile(r"(--|/\*|\*/)")
_DOLLAR_QUOTE_RE = re.compile(r"\$\$|\$[A-Za-z_][A-Za-z0-9_]*\$")
_CREATE_TABLE_RE = re.compile(
    rf"^CREATE\s+(?:TEMP(?:ORARY)?\s+|UNLOGGED\s+)?TABLE\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?{_QUALIFIED_IDENT}\s*\(",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_VIEW_RE = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+{_QUALIFIED_IDENT}\s+AS\s+SELECT\b",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_INDEX_RE = re.compile(
    rf"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?{_IDENT}\s+ON\s+{_QUALIFIED_IDENT}"
    rf"\s*\(\s*{_COLUMN_LIST}\s*\)$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_SCHEMA_RE = re.compile(
    rf"^CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?{_IDENT}$",
    re.IGNORECASE | re.DOTALL,
)
_FORBIDDEN_DDL_TOKENS_RE = re.compile(
    r"\b("
    r"alter\s+system|attach|copy|create\s+extension|create\s+function|"
    r"create\s+procedure|create\s+server|delete|dblink|do|drop|execute|"
    r"fdw|foreign\s+data\s+wrapper|foreign\s+server|foreign\s+table|grant|"
    r"insert|merge|revoke|truncate|update|with"
    r")\b",
    re.IGNORECASE,
)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)


def _conn(gold: bool = False):
    if gold:
        return psycopg2.connect(
            host=settings.pg_gold_host,
            port=settings.pg_gold_port,
            dbname=settings.pg_gold_db,
            user=settings.pg_user,
            password=settings.pg_password,
        )
    return psycopg2.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db,
        user=settings.pg_user,
        password=settings.pg_password,
    )


def _validate_non_destructive_sql(sql: str) -> str:
    sql = (sql or "").strip()
    if not sql:
        raise ValueError("SQL is required")
    if "\x00" in sql:
        raise ValueError("Invalid SQL")

    if _SQL_COMMENT_RE.search(sql):
        raise ValueError("SQL comments are not allowed")
    if _DOLLAR_QUOTE_RE.search(sql):
        raise ValueError("Dollar quoting is not allowed")

    if sql.endswith(";"):
        sql = sql[:-1].strip()
    if ";" in sql:
        raise ValueError("Only one SQL statement is allowed")

    normalized = re.sub(r"\s+", " ", sql).strip()
    if not normalized:
        raise ValueError("SQL is required")
    if _FORBIDDEN_DDL_TOKENS_RE.search(normalized):
        raise ValueError("SQL construct rejected by postgres_execute_ddl")

    if _CREATE_TABLE_RE.match(normalized):
        if re.search(r"\bAS\s+SELECT\b", normalized, re.IGNORECASE) or _SELECT_RE.search(normalized):
            raise ValueError("CREATE TABLE AS SELECT is not allowed")
        return sql

    if _CREATE_VIEW_RE.match(normalized):
        return sql

    if _CREATE_INDEX_RE.fullmatch(normalized):
        return sql

    if _CREATE_SCHEMA_RE.fullmatch(normalized):
        return sql

    raise ValueError("Only CREATE TABLE, CREATE VIEW, CREATE OR REPLACE VIEW, CREATE INDEX and CREATE SCHEMA are allowed")


# ── Schema discovery ───────────────────────────────────────────────────────────

@tool(
    name="postgres_list_schemas",
    description="List all schemas in the PostgreSQL database.",
    input_schema={
        "type": "object",
        "properties": {
            "gold": {"type": "boolean", "description": "Query gold DB instead of main (default false)"},
        },
        "required": [],
    },
)
def postgres_list_schemas(gold: bool = False) -> dict:
    conn = _conn(gold)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
            "ORDER BY schema_name"
        )
        schemas = [r[0] for r in cur.fetchall()]
    conn.close()
    return {"schemas": schemas, "database": "gold" if gold else "main"}


@tool(
    name="postgres_list_tables",
    description="List all tables (and views) in a schema.",
    input_schema={
        "type": "object",
        "properties": {
            "schema": {"type": "string", "description": "Schema name (default: public)"},
            "gold":   {"type": "boolean"},
        },
        "required": [],
    },
)
def postgres_list_tables(schema: str = "public", gold: bool = False) -> dict:
    conn = _conn(gold)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema=%s ORDER BY table_name",
            (schema,),
        )
        tables = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
    conn.close()
    return {"tables": tables, "schema": schema, "database": "gold" if gold else "main"}


@tool(
    name="postgres_get_table_schema",
    description="Get column definitions (name, type, nullable, default) for a table.",
    input_schema={
        "type": "object",
        "properties": {
            "table":  {"type": "string"},
            "schema": {"type": "string", "description": "Schema (default: public)"},
            "gold":   {"type": "boolean"},
        },
        "required": ["table"],
    },
)
def postgres_get_table_schema(table: str, schema: str = "public", gold: bool = False) -> dict:
    conn = _conn(gold)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name, data_type, is_nullable, column_default
               FROM information_schema.columns
               WHERE table_schema = %s AND table_name = %s
               ORDER BY ordinal_position""",
            (schema, table),
        )
        columns = [
            {"column": r[0], "type": r[1], "nullable": r[2] == "YES", "default": r[3]}
            for r in cur.fetchall()
        ]
    conn.close()
    return {"table": f"{schema}.{table}", "columns": columns}


# ── Query execution ────────────────────────────────────────────────────────────

@tool(
    name="postgres_execute_query",
    description=(
        "Execute a read-only SELECT query. "
        "Returns rows as list of dicts. Max 200 rows."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql":   {"type": "string", "description": "SELECT statement"},
            "limit": {"type": "integer", "description": "Max rows (default 50)"},
            "gold":  {"type": "boolean"},
        },
        "required": ["sql"],
    },
)
def postgres_execute_query(sql: str, limit: int = 50, gold: bool = False) -> dict:
    clean = sql.strip().upper()
    if not clean.startswith("SELECT") and not clean.startswith("WITH"):
        return {"error": "Only SELECT / WITH queries allowed via this tool"}
    limit = min(limit, 200)
    if "limit" not in sql.lower():
        sql = f"{sql.rstrip(';')} LIMIT {limit}"
    conn = _conn(gold)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        cols = [d.name for d in cur.description] if cur.description else []
    conn.close()
    return {"rows": rows, "count": len(rows), "columns": cols}


@tool(
    name="postgres_execute_ddl",
    description=(
        "Execute non-destructive DDL statements such as CREATE TABLE, CREATE VIEW, "
        "CREATE INDEX or CREATE SCHEMA. "
        "Used by Studio to deploy Silver/Gold schema definitions. "
        "Destructive and DML statements are rejected."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql":  {"type": "string", "description": "Single allowlisted DDL statement to execute"},
            "gold": {"type": "boolean", "description": "Run against gold DB"},
        },
        "required": ["sql"],
    },
)
def postgres_execute_ddl(sql: str, gold: bool = False) -> dict:
    sql = _validate_non_destructive_sql(sql)
    conn = _conn(gold)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.close()
    return {"executed": True, "preview": sql[:300]}


@tool(
    name="postgres_get_sample",
    description="Get a sample of rows from a table (no custom SQL needed).",
    input_schema={
        "type": "object",
        "properties": {
            "table":  {"type": "string"},
            "schema": {"type": "string", "description": "Schema (default: public)"},
            "n":      {"type": "integer", "description": "Rows (default 10)"},
            "gold":   {"type": "boolean"},
        },
        "required": ["table"],
    },
)
def postgres_get_sample(table: str, schema: str = "public", n: int = 10, gold: bool = False) -> dict:
    n    = min(n, 100)
    sql  = f'SELECT * FROM "{schema}"."{table}" LIMIT {n}'
    conn = _conn(gold)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        cols = [d.name for d in cur.description] if cur.description else []
    conn.close()
    return {"rows": rows, "columns": cols, "count": len(rows)}
