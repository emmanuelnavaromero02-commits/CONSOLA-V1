from __future__ import annotations

import asyncio
import importlib.util
import os
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.routers import dashboard
from app.services import db_scope, proactive_service, seed_packaged_apps


_LIVE = importlib.util.spec_from_file_location(
    "_console_live_postgres", Path(__file__).with_name("test_cross_tenant_api_isolation.py")
)
assert _LIVE and _LIVE.loader
live = importlib.util.module_from_spec(_LIVE)
_LIVE.loader.exec_module(live)

CONSOLE_ROLE = "omega_console"
CONSOLE_PASSWORD = "test_omega_console_password"
FUTURE_PLATFORM_CONTEXT = """
CREATE OR REPLACE FUNCTION omega_20b_platform_audit_context()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.workspace_id', true), '') IS NULL
       AND current_setting('app.platform_admin', true) = 'true'
$$;
"""
CURRENT_PLATFORM_CONTEXT = """
CREATE OR REPLACE FUNCTION omega_20b_platform_audit_context()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.workspace_id', true), '') IS NULL
$$;
"""


@pytest.fixture(scope="module")
def postgres_dsn():
    live._ensure_postgres_image()
    container_id = live._docker(
        "run",
        "--pull=never",
        "-d",
        "--rm",
        "--name",
        f"consola-dashboard-scope-{uuid.uuid4().hex[:12]}",
        "-e",
        f"POSTGRES_DB={live.POSTGRES_DB}",
        "-e",
        f"POSTGRES_USER={live.POSTGRES_SUPERUSER}",
        "-e",
        f"POSTGRES_PASSWORD={live.POSTGRES_PASSWORD}",
        "-e",
        f"PGOPTIONS={live._init_pgoptions()}",
        "-v",
        f"{live.REPO_ROOT / 'infra' / 'init'}:/docker-entrypoint-initdb.d:ro",
        "-P",
        live.POSTGRES_IMAGE,
    ).stdout.strip()
    try:
        port = live._mapped_postgres_port(container_id)
        dsn = (
            f"postgresql://{live.POSTGRES_SUPERUSER}:{live.POSTGRES_PASSWORD}"
            f"@127.0.0.1:{port}/{live.POSTGRES_DB}"
        )
        asyncio.run(_wait_for_init(dsn, container_id))
        yield dsn
    finally:
        live._docker("rm", "-f", "-v", container_id, check=False)


async def _wait_for_init(dsn: str, container_id: str) -> None:
    # The full infra/init chain runs before the server accepts TCP connections.
    deadline = time.monotonic() + float(os.getenv("DASHBOARD_SCOPE_INIT_TIMEOUT", "900"))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        state = live._docker("inspect", "-f", "{{.State.Status}}", container_id, check=False)
        if state.returncode != 0 or state.stdout.strip() in {"exited", "dead"}:
            logs = live._docker("logs", "--tail=100", container_id, check=False)
            raise RuntimeError(f"postgres init container stopped:\n{logs.stdout}\n{logs.stderr}")
        try:
            conn = await asyncpg.connect(dsn)
            try:
                if await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL"):
                    return
            finally:
                await conn.close()
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(2)
    raise RuntimeError(f"postgres init did not finish in time: {last_error!r}")


def _console_dsn(dsn: str) -> str:
    return dsn.replace(
        f"{live.POSTGRES_SUPERUSER}:{live.POSTGRES_PASSWORD}", f"{CONSOLE_ROLE}:{CONSOLE_PASSWORD}"
    )


async def _seed(dsn: str) -> dict[str, str]:
    conn = await asyncpg.connect(dsn)
    try:
        suffix = uuid.uuid4().hex[:10]
        ids: dict[str, str] = {}
        for label in ("a", "b"):
            tenant = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id", f"dash-{label}-{suffix}"
            )
            workspace = await conn.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
                tenant,
                f"Dash {label} {suffix}",
            )
            ids[f"tenant_{label}"], ids[f"workspace_{label}"] = str(tenant), str(workspace)
        runs = [("a", "success"), ("a", "failed"), ("b", "success"), ("b", "failed"), ("b", "failed")]
        for index, (label, status) in enumerate(runs):
            await conn.execute(
                """
                INSERT INTO pipeline_runs
                    (run_id, dag_id, cartridge_id, entity, status, started_at, finished_at,
                     record_count, tenant_id, workspace_id)
                VALUES ($1, 'sap_successfactors_extract', 'sap_successfactors', 'PerPerson', $2,
                        NOW() - INTERVAL '5 minutes', NOW() - INTERVAL '1 minute', 10, $3, $4)
                """,
                f"dash-{suffix}-{index}",
                status,
                uuid.UUID(ids[f"tenant_{label}"]),
                uuid.UUID(ids[f"workspace_{label}"]),
            )
        for index in range(4):
            await conn.execute(
                """
                INSERT INTO extraction_runs (run_id, cartridge_id, entity_name, status, started_at, finished_at)
                VALUES ($1, 'sap_successfactors', 'PerPerson', 'failed', NOW(), NOW())
                """,
                f"legacy-{suffix}-{index}",
            )
        await conn.execute(
            """
            INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility,
                                       datasets_used, tenant_id, workspace_id, scope_status)
            VALUES ($1, 'Plantilla', '<html></html>', '', 'hubspot', 'shared', '{}'::text[],
                    NULL, NULL, 'platform_template')
            """,
            f"template_{suffix}",
        )
        ids["template"] = f"template_{suffix}"
        return ids
    finally:
        await conn.close()


def _admin(ids: dict[str, str], label: str = "a", role: str = "super_admin") -> dict:
    return {
        "id": 1,
        "email": "platform@example.com",
        "role": role,
        "active_tenant_id": ids[f"tenant_{label}"],
        "active_workspace_id": ids[f"workspace_{label}"],
    }


async def _dashboard_for(dsn: str, user: dict) -> dict:
    pool = await asyncpg.create_pool(_console_dsn(dsn), min_size=1, max_size=2)
    try:
        with (
            patch.object(dashboard.auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                dashboard, "_active_scoped_cartridges", new=AsyncMock(return_value=("sap_successfactors",))
            ),
        ):
            return await dashboard.dashboard_kpis(user=user)
    finally:
        await pool.close()


def test_platform_admin_dashboard_counts_only_the_active_workspace(postgres_dsn):
    ids = asyncio.run(_seed(postgres_dsn))

    in_a = asyncio.run(_dashboard_for(postgres_dsn, _admin(ids, "a")))
    in_b = asyncio.run(_dashboard_for(postgres_dsn, _admin(ids, "b", role="admin")))

    assert in_a["extractions"]["today"] == 2
    assert in_a["extractions"]["productive_failures_today"] == 1
    assert in_a["extractions"]["unscope_noise_failures_today"] == 0
    assert in_b["extractions"]["today"] == 3
    assert in_b["extractions"]["productive_failures_today"] == 2
    assert in_a["data_freshness"]["sap_successfactors"]["status"] == "fresh"


def test_platform_admin_without_an_active_workspace_is_refused(postgres_dsn):
    user = {"id": 1, "email": "platform@example.com", "role": "owner"}
    with pytest.raises(HTTPException) as error:
        asyncio.run(_dashboard_for(postgres_dsn, user))
    assert error.value.status_code == 403


async def _briefing(dsn: str, user: dict) -> list[dict]:
    pool = await asyncpg.create_pool(_console_dsn(dsn), min_size=1, max_size=2)
    try:
        with patch.object(proactive_service.auth, "pool", new=AsyncMock(return_value=pool)):
            return await proactive_service.analyze_extraction_failures(user_context=user)
    finally:
        await pool.close()


def test_briefing_failures_are_workspace_scoped(postgres_dsn):
    ids = asyncio.run(_seed(postgres_dsn))
    highlights = asyncio.run(_briefing(postgres_dsn, _admin(ids, "b")))
    counts = {item["cartridge"]: item for item in highlights}
    assert "2 extracciones fallidas" in counts["sap_successfactors"]["title"]
    assert asyncio.run(_briefing(postgres_dsn, {"id": 1, "role": "super_admin"})) == []


async def _platform_reads(dsn: str, template: str, *, future: bool) -> dict[str, int]:
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(FUTURE_PLATFORM_CONTEXT if future else CURRENT_PLATFORM_CONTEXT)
    finally:
        await admin.close()
    pool = await asyncpg.create_pool(_console_dsn(dsn), min_size=1, max_size=2)
    query = "SELECT COUNT(*) FROM analytic_apps WHERE name = $1 AND scope_status = 'platform_template'"
    write = "UPDATE analytic_apps SET description = 'platform' WHERE name = $1"
    try:
        async with db_scope.platform_admin_db(pool, {"role": "owner"}) as conn:
            platform_writes = await conn.execute(write, template)
        async with pool.acquire() as conn:
            async with conn.transaction():
                bare_writes = await conn.execute(write, template)
        async with db_scope.scoped_db(pool, None, str(uuid.uuid4())) as conn:
            scoped_writes = await conn.execute(write, template)
            scoped_reads = await conn.fetchval(query, template)
        return {
            "platform_writes": int(platform_writes.split()[-1]),
            "bare_writes": int(bare_writes.split()[-1]),
            "scoped_writes": int(scoped_writes.split()[-1]),
            "scoped_reads": scoped_reads,
        }
    finally:
        await pool.close()


@pytest.mark.parametrize("future", [False, True], ids=["current-context", "context-requiring-marker"])
def test_platform_db_scope_works_under_both_platform_context_definitions(postgres_dsn, future):
    ids = asyncio.run(_seed(postgres_dsn))
    try:
        result = asyncio.run(_platform_reads(postgres_dsn, ids["template"], future=future))
    finally:
        asyncio.run(_platform_reads(postgres_dsn, ids["template"], future=False))
    assert result["platform_writes"] == 1
    assert result["scoped_writes"] == 0, "a workspace scope must not manage platform templates"
    assert result["scoped_reads"] == 1, "templates stay readable to every workspace"
    assert result["bare_writes"] == (0 if future else 1)


async def _seed_apps(dsn: str, registry: Path) -> int:
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(FUTURE_PLATFORM_CONTEXT)
    finally:
        await admin.close()
    pool = await asyncpg.create_pool(_console_dsn(dsn), min_size=1, max_size=2)
    try:
        with patch.object(seed_packaged_apps, "_REGISTRY", registry):
            await seed_packaged_apps.seed_packaged_apps(pool)
    finally:
        await pool.close()
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(CURRENT_PLATFORM_CONTEXT)
        return await admin.fetchval(
            "SELECT COUNT(*) FROM analytic_apps WHERE name = 'live_seed_probe' AND scope_status = 'platform_template'"
        )
    finally:
        await admin.close()


def test_packaged_app_seeder_writes_under_the_marker_requiring_context(postgres_dsn, tmp_path):
    apps = tmp_path / "live_probe_cartridge" / "apps"
    apps.mkdir(parents=True)
    (apps / "live_seed_probe.html").write_text("<html>/api/data/deals</html>", encoding="utf-8")
    assert asyncio.run(_seed_apps(postgres_dsn, tmp_path)) == 1
