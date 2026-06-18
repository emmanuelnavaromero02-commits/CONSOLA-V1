from sqlalchemy import create_engine, text
import psycopg2
from app.core.config import settings
from app.core.request_context import scope_values


def _apply_rls_scope(conn) -> None:
    tenant_id, workspace_id = scope_values()
    if not (tenant_id and workspace_id):
        return
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cur.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))


def get_connection():
    dsn = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(dsn)
    _apply_rls_scope(conn)
    return conn


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
