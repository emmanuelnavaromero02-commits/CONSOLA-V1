from sqlalchemy import create_engine, text
import psycopg2
from app.core.config import settings


def get_connection():
    # Sprint v1.40.2: route every psycopg2 connection through
    # ``settings.database_url`` so the DATABASE_URL env override
    # applies here too. Previously this used pg_host / pg_user /
    # pg_password directly and silently fell back to the
    # ``postgres:postgres`` defaults when the env wasn't read by
    # pydantic, which is exactly the auth-loop the cartridge hit
    # after v1.40 wired it as ``omega_cartridge_replicon``.
    # psycopg2.connect() accepts a libpq DSN; strip the SQLAlchemy
    # ``+psycopg2`` driver hint since libpq doesn't parse it.
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
