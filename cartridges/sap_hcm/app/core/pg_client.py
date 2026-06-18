"""
Lazy PostgreSQL connection helpers.

The engine is built on first use so that an empty ``DATABASE_URL`` (e.g. in
unit tests) does not crash at module import time.
"""
from __future__ import annotations

from urllib.parse import urlparse

import psycopg2
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.request_context import scope_values

_engine: Engine | None = None


def _apply_rls_scope(conn) -> None:
    tenant_id, workspace_id = scope_values()
    if not (tenant_id and workspace_id):
        return
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cur.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        _engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
    return _engine


def get_connection():
    """Return a raw psycopg2 connection parsed from settings.database_url."""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    parsed = urlparse(
        settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
    )
    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "/").lstrip("/"),
        user=parsed.username,
        password=parsed.password,
    )
    _apply_rls_scope(conn)
    return conn


def execute(sql: str, params: dict | None = None) -> None:
    with _get_engine().begin() as conn:
        conn.execute(text(sql), params or {})


def fetch_one(sql: str, params: dict | None = None):
    with _get_engine().begin() as conn:
        row = conn.execute(text(sql), params or {}).mappings().first()
        return dict(row) if row else None


def fetch_all(sql: str, params: dict | None = None):
    with _get_engine().begin() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
