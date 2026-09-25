from __future__ import annotations

import logging
from urllib.parse import urlparse

import psycopg2
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import settings

_engine: Engine | None = None
_logger = logging.getLogger(__name__)


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        parsed = urlparse(
            settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
        )
        if parsed.username == "postgres":
            _logger.warning(
                "DATABASE_URL connects as the 'postgres' superuser — "
                "use a least-privilege role (e.g. omega_cartridge_salesforce) in production"
            )
        _engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
    return _engine


def get_connection():
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    parsed = urlparse(
        settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
    )
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "/").lstrip("/"),
        user=parsed.username,
        password=parsed.password,
    )


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
