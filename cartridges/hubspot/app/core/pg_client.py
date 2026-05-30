from sqlalchemy import create_engine, text
import psycopg2
from app.core.config import settings


def get_connection():
    dsn = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(dsn)


engine = create_engine(settings.database_url, future=True)


def execute(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def fetch_one(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        row = result.mappings().first()
        return dict(row) if row else None


def fetch_all(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(r) for r in result.mappings().all()]
