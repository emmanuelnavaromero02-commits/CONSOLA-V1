from __future__ import annotations

import os

import psycopg2
import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

CARTRIDGE_ID = "sap_hcm"
ENTITY = "WmScopeProbe"
TENANT_A = "aaaaaaaa-0000-4000-8000-000000000001"
WORKSPACE_A = "aaaaaaaa-0000-4000-8000-00000000000a"
TENANT_B = "bbbbbbbb-0000-4000-8000-000000000002"
WORKSPACE_B = "bbbbbbbb-0000-4000-8000-00000000000b"
SCOPE_A = f"tenant:{TENANT_A}:workspace:{WORKSPACE_A}"
SCOPE_B = f"tenant:{TENANT_B}:workspace:{WORKSPACE_B}"


def _dsn() -> str:
    return (os.environ.get("DATABASE_URL") or "").replace("postgresql+psycopg2://", "postgresql://")


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(_dsn(), connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="requires a reachable Postgres with the platform schema (DATABASE_URL)",
)


@pytest.fixture()
def pg():
    conn = psycopg2.connect(_dsn(), connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (TENANT_A, "wm-scope-test-tenant-a", "wm-scope-test-tenant-a"))
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (TENANT_B, "wm-scope-test-tenant-b", "wm-scope-test-tenant-b"))
        cur.execute("INSERT INTO workspaces (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (WORKSPACE_A, TENANT_A, "wm-scope-test-ws-a"))
        cur.execute("INSERT INTO workspaces (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (WORKSPACE_B, TENANT_B, "wm-scope-test-ws-b"))
        cur.execute("DELETE FROM entity_watermarks WHERE cartridge_id = %s AND entity_name = %s", (CARTRIDGE_ID, ENTITY))
    yield conn
    conn.close()


def _signed_ctx(tenant_id: str, workspace_id: str) -> dict:
    from app.core import request_context

    return request_context._sign_security_context(
        {"trusted": True, "source": "console", "tenant_id": tenant_id, "workspace_id": workspace_id}
    )


def _rows(pg) -> list[tuple]:
    with pg.cursor() as cur:
        cur.execute(
            "SELECT watermark_scope, last_watermark_value, last_run_id FROM entity_watermarks"
            " WHERE cartridge_id = %s AND entity_name = %s ORDER BY watermark_scope",
            (CARTRIDGE_ID, ENTITY),
        )
        return cur.fetchall()


def test_incremental_watermarks_are_isolated_per_tenant(monkeypatch, pg):
    from app.core import request_context
    from app.services import extraction_service, watermark_service

    current: dict = {"rows": []}

    class _FakeClient:
        def __init__(self, security_context=None):
            pass

        def fetch_entity(self, **_kwargs):
            rows, current["rows"] = current["rows"], []
            return rows

    monkeypatch.setattr(extraction_service, "SapHcmClient", _FakeClient)
    run_ids = iter(["run-tenant-a", "run-tenant-b"])
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: next(run_ids))
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", lambda **_kwargs: "s3://bronze/path")

    seen_watermarks: list = []

    def _spy_get_watermark(entity):
        value = watermark_service.get_watermark(entity)
        seen_watermarks.append(value)
        return value

    monkeypatch.setattr(extraction_service, "get_watermark", _spy_get_watermark)

    config_base = {"entity": ENTITY, "mode": "incremental", "watermark_field": "modifiedAt"}

    current["rows"] = [{"id": "a1", "modifiedAt": "2026-01-15T10:00:00Z"}]
    token = request_context.set_security_context(_signed_ctx(TENANT_A, WORKSPACE_A))
    try:
        result = extraction_service.run_entity({**config_base, "security_context": _signed_ctx(TENANT_A, WORKSPACE_A)})
    finally:
        request_context.reset_security_context(token)
    assert result["status"] == "success", result
    assert seen_watermarks == [None]
    assert [row[0] for row in _rows(pg)] == [SCOPE_A]

    current["rows"] = [{"id": "b1", "modifiedAt": "2026-02-15T10:00:00Z"}]
    token = request_context.set_security_context(_signed_ctx(TENANT_B, WORKSPACE_B))
    try:
        assert watermark_service.get_watermark(ENTITY) is None
        result = extraction_service.run_entity({**config_base, "security_context": _signed_ctx(TENANT_B, WORKSPACE_B)})
    finally:
        request_context.reset_security_context(token)
    assert result["status"] == "success", result
    assert seen_watermarks == [None, None]

    rows = _rows(pg)
    assert [row[0] for row in rows] == sorted([SCOPE_A, SCOPE_B])
    by_scope = {row[0]: row for row in rows}
    assert by_scope[SCOPE_A][1].startswith("2026-01")
    assert by_scope[SCOPE_B][1].startswith("2026-02")
    assert by_scope[SCOPE_A][2] == "run-tenant-a"
    assert by_scope[SCOPE_B][2] == "run-tenant-b"

    token = request_context.set_security_context(_signed_ctx(TENANT_A, WORKSPACE_A))
    try:
        assert watermark_service.get_watermark(ENTITY).startswith("2026-01")
    finally:
        request_context.reset_security_context(token)
