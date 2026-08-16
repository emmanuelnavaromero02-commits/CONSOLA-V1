from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

SF_ROLE = "omega_cartridge_sap_sf"
S4_ROLE = "omega_cartridge_sap_s4"
ROLE_PASSWORD = os.environ.get("OMEGA_TEST_SF_CARTRIDGE_PASSWORD", "wm-test-dummy-password")
TENANT = "aaaaaaaa-0000-4000-8000-000000000001"
WORKSPACE = "aaaaaaaa-0000-4000-8000-00000000000a"
RUNLOG_TABLES = ("run_logs", "extraction_runs", "jobs", "kb_runs")


def _dsn() -> str:
    return (os.environ.get("DATABASE_URL") or "").replace("postgresql+psycopg2://", "postgresql://")


def _role_dsn(role: str) -> str:
    parts = urlsplit(_dsn())
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{role}:{ROLE_PASSWORD}@{host}{port}", parts.path, parts.query, parts.fragment))


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
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (TENANT, "runlog-scope-tenant", "runlog-scope-tenant"))
        cur.execute("INSERT INTO workspaces (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (WORKSPACE, TENANT, "runlog-scope-ws"))
        for table, key in (("run_logs", "run_id"), ("extraction_runs", "run_id"), ("jobs", "job_id"), ("kb_runs", "run_id"), ("pipeline_runs", "run_id")):
            cur.execute(f"DELETE FROM {table} WHERE {key} LIKE 'runlog-scope-%%'")
    yield conn
    conn.close()


def _scoped_cursor_exec(conn, sql: str, params: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
        cur.execute("SELECT set_config('app.workspace_id', %s, true)", (WORKSPACE,))
        cur.execute(sql, params)
    conn.commit()


_INSERTS = {
    "run_logs": "INSERT INTO run_logs (run_id, cartridge, entity, level, status, message, tenant_id, workspace_id, scope_status) VALUES (%(id)s, %(cart)s, 'PerPerson', 'info', 'running', 'probe', %(t)s, %(w)s, 'scoped')",
    "extraction_runs": "INSERT INTO extraction_runs (run_id, cartridge_id, entity_name, run_type, status, started_at, tenant_id, workspace_id, scope_status) VALUES (%(id)s, %(cart)s, 'PerPerson', 'full', 'running', NOW(), %(t)s, %(w)s, 'scoped')",
    "jobs": "INSERT INTO jobs (job_id, tool, status, tenant_id, workspace_id, scope_status) VALUES (%(id)s, 'run_full_load', 'running', %(t)s, %(w)s, 'scoped')",
    "kb_runs": "INSERT INTO kb_runs (run_id, kb_id, cartridge_id, status, tenant_id, workspace_id, scope_status) VALUES (%(id)s, 'kb1', %(cart)s, 'running', %(t)s, %(w)s, 'scoped')",
}


@pytest.mark.parametrize("role,cartridge", ((SF_ROLE, "sap_successfactors"), (S4_ROLE, "sap_s4hana")))
def test_sap_roles_can_record_runs_on_fresh_install(seeded_pg, role, cartridge):
    conn = psycopg2.connect(_role_dsn(role), connect_timeout=5)
    try:
        for table, sql in _INSERTS.items():
            _scoped_cursor_exec(conn, sql, {"id": f"runlog-scope-{role[-2:]}", "cart": cartridge, "t": TENANT, "w": WORKSPACE})
    finally:
        conn.close()
    with seeded_pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM extraction_runs WHERE run_id = %s", (f"runlog-scope-{role[-2:]}",))
        assert cur.fetchone()[0] == 1


def test_sf_role_can_mirror_into_pipeline_runs(seeded_pg):
    conn = psycopg2.connect(_role_dsn(SF_ROLE), connect_timeout=5)
    try:
        _scoped_cursor_exec(
            conn,
            "INSERT INTO pipeline_runs (run_id, dag_id, cartridge_id, entity, mode, status, tenant_id, workspace_id) VALUES (%(id)s, 'cartridge:sap_successfactors', 'sap_successfactors', 'PerPerson', 'full', 'running', %(t)s, %(w)s)",
            {"id": "runlog-scope-pr", "t": TENANT, "w": WORKSPACE},
        )
    finally:
        conn.close()
    with seeded_pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_runs WHERE run_id = 'runlog-scope-pr'")
        assert cur.fetchone()[0] == 1


def test_runlog_service_records_and_mirrors_end_to_end(monkeypatch, seeded_pg):
    from app.core import request_context
    from app.core.config import settings
    from app.services import runlog_service

    monkeypatch.setattr(settings, "database_url", _role_dsn(SF_ROLE))
    ctx = request_context._sign_security_context(
        {"trusted": True, "source": "console", "tenant_id": TENANT, "workspace_id": WORKSPACE}
    )
    token = request_context.set_security_context(ctx)
    try:
        from datetime import datetime, timezone

        run_id = runlog_service.create_run(
            cartridge_id="sap_successfactors",
            entity_name="PerPerson",
            run_type="full",
            status="running",
            started_at=datetime.now(timezone.utc),
            requested_run_id="runlog-scope-e2e",
        )
        runlog_service.finish_run(
            run_id=run_id,
            status="success",
            records_extracted=1,
            storage_uri="s3://lakehouse/raw/probe",
            finished_at=datetime.now(timezone.utc),
        )
    finally:
        request_context.reset_security_context(token)

    with seeded_pg.cursor() as cur:
        cur.execute("SELECT status FROM extraction_runs WHERE run_id = 'runlog-scope-e2e'")
        assert cur.fetchone()[0] == "success"
        cur.execute("SELECT status FROM pipeline_runs WHERE run_id = 'runlog-scope-e2e'")
        row = cur.fetchone()
        assert row is not None, "the SF mirror must land in pipeline_runs"


def test_migration_policies_cover_both_sap_roles(seeded_pg):
    with seeded_pg.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM pg_policy
                WHERE polrelid::regclass::text = ANY(%s)
                  AND ('omega_cartridge_sap_sf'::regrole = ANY(polroles)
                       AND 'omega_cartridge_sap_s4'::regrole = ANY(polroles))""",
            (list(RUNLOG_TABLES),),
        )
        assert cur.fetchone()[0] == 8, "2 policies per runlog table for the SAP roles"
        cur.execute(
            "SELECT count(*) FROM pg_policy WHERE polrelid = 'pipeline_runs'::regclass AND 'omega_cartridge_sap_sf'::regrole = ANY(polroles)"
        )
        assert cur.fetchone()[0] == 1
