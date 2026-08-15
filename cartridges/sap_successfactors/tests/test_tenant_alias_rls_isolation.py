from __future__ import annotations

import os

import psycopg2
import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

CARTRIDGE_ROLE = "omega_cartridge_sap_sf"
CARTRIDGE_ROLE_PASSWORD = os.environ.get("OMEGA_TEST_SF_CARTRIDGE_PASSWORD", "wm-test-dummy-password")
TABLE = "sap_successfactors_tenant_entity_aliases"
TENANT_A = "aaaaaaaa-0000-4000-8000-000000000001"
WORKSPACE_A = "aaaaaaaa-0000-4000-8000-00000000000a"
TENANT_B = "bbbbbbbb-0000-4000-8000-000000000002"
WORKSPACE_B = "bbbbbbbb-0000-4000-8000-00000000000b"


def _dsn() -> str:
    return (os.environ.get("DATABASE_URL") or "").replace("postgresql+psycopg2://", "postgresql://")


def _scoped_dsn() -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(_dsn())
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{CARTRIDGE_ROLE}:{CARTRIDGE_ROLE_PASSWORD}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


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
def seeded_pg():
    conn = psycopg2.connect(_dsn(), connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (TENANT_A, "wm-scope-test-tenant-a", "wm-scope-test-tenant-a"))
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (TENANT_B, "wm-scope-test-tenant-b", "wm-scope-test-tenant-b"))
        cur.execute("INSERT INTO workspaces (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (WORKSPACE_A, TENANT_A, "wm-scope-test-ws-a"))
        cur.execute("INSERT INTO workspaces (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (WORKSPACE_B, TENANT_B, "wm-scope-test-ws-b"))
        cur.execute(f"DELETE FROM {TABLE} WHERE canonical_entity LIKE 'RlsProbe%%'")
        cur.execute(
            f"INSERT INTO {TABLE} (tenant_id, workspace_id, component, canonical_entity, odata_entity) VALUES (%s, %s, 'talent', 'RlsProbeEntity', 'RlsProbe_TenantA')",
            (TENANT_A, WORKSPACE_A),
        )
        cur.execute(
            f"INSERT INTO {TABLE} (tenant_id, workspace_id, component, canonical_entity, odata_entity) VALUES (%s, %s, 'talent', 'RlsProbeEntity', 'RlsProbe_TenantB')",
            (TENANT_B, WORKSPACE_B),
        )
    yield conn
    conn.close()


def _scoped_select(tenant_id: str, workspace_id: str) -> list[str]:
    conn = psycopg2.connect(_scoped_dsn(), connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
            cur.execute(f"SELECT odata_entity FROM {TABLE} WHERE canonical_entity = 'RlsProbeEntity' ORDER BY odata_entity")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def test_rls_isolates_alias_rows_per_tenant(seeded_pg):
    assert _scoped_select(TENANT_A, WORKSPACE_A) == ["RlsProbe_TenantA"]
    assert _scoped_select(TENANT_B, WORKSPACE_B) == ["RlsProbe_TenantB"]


def test_rls_empty_scope_sees_nothing(seeded_pg):
    assert _scoped_select("", "") == []


def test_preflight_reader_is_scoped_end_to_end(monkeypatch, seeded_pg):
    from app.services import preflight

    monkeypatch.setattr(preflight, "_ALIAS_ENGINE", None)
    monkeypatch.setattr(preflight.settings, "database_url", _scoped_dsn())

    ctx_a = {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A}
    aliases_a = preflight._load_talent_alias_candidates(security_context=ctx_a)
    odata_a = [alias["odata_entity"] for alias in aliases_a.get("talent", [])]
    assert odata_a == ["RlsProbe_TenantA"]

    ctx_b = {"tenant_id": TENANT_B, "workspace_id": WORKSPACE_B}
    aliases_b = preflight._load_talent_alias_candidates(security_context=ctx_b)
    odata_b = [alias["odata_entity"] for alias in aliases_b.get("talent", [])]
    assert odata_b == ["RlsProbe_TenantB"]

    empty = preflight._load_talent_alias_candidates(security_context={})
    assert empty == {}
