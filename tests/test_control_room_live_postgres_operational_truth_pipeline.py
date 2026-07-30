"""Live two-workspace proof for Gold, Intelligence, replay, and projection."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.services import auth, control_room_service
from app.services.intelligence import history, persistence
from app.services.intelligence.engine import run_intelligence
from app.services.intelligence.gold_fetcher import (
    clear_gold_row_cache,
    query_gold_dataset_rows,
)
from app.services.monitor_alert_policy import monitor_should_alert
from refinement.app.duckdb_engine import DuckDBEngine
from tests.test_gold_native_rls_contract import (
    GOLD_ROLE_PASSWORD,
    POSTGRES_PASSWORD as GOLD_POSTGRES_PASSWORD,
    postgres_gold_with_native_rls,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


class _TestGoldEngine(DuckDBEngine):
    """Keep the real SQL/PostgreSQL materializer; replace external object I/O."""

    def _copy_scoped_gold_table_snapshot(
        self, _con, table, _path, _tenant, _workspace, _context
    ):
        return f"postgres_gold:{table}"

    def _write_lineage(self, **_kwargs):
        return None

    def _update_catalog(self, **_kwargs):
        return None

    def _prune_snapshots(self, *_args, **_kwargs):
        return None


def _user(scope: dict[str, str]) -> dict:
    return {
        "id": scope["user_id"],
        "email": scope["email"],
        "role": "admin",
        "workspace_role": "workspace_admin",
        "active_tenant_id": scope["tenant_id"],
        "active_workspace_id": scope["workspace_id"],
        "allowed_cartridges": ["replicon"],
    }


async def _seed_scope(conn: asyncpg.Connection, label: str) -> dict[str, str]:
    tenant_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    email = f"operational-truth-{label}-{uuid.uuid4().hex[:8]}@example.test"
    await conn.execute(
        "INSERT INTO tenants(id, name, slug, status) VALUES ($1, $2, $3, 'active')",
        tenant_id,
        f"Operational Truth {label} {tenant_id[:8]}",
        f"operational-truth-{label}-{tenant_id[:8]}",
    )
    await conn.execute(
        "INSERT INTO workspaces(id, tenant_id, name) VALUES ($1, $2, $3)",
        workspace_id,
        tenant_id,
        f"Runtime {label}",
    )
    user_id = await conn.fetchval(
        """
        INSERT INTO users(email, name, password_hash, role, tenant_id)
        VALUES ($1, $2, 'not-a-login-secret', 'admin', $3)
        RETURNING id
        """,
        email,
        f"Runtime {label}",
        tenant_id,
    )
    await conn.execute(
        """
        INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
        SELECT $1, $2, id FROM roles WHERE name = 'workspace_admin'
        """,
        user_id,
        workspace_id,
    )
    await conn.execute(
        """
        INSERT INTO cartridge_installations (
            id, tenant_id, workspace_id, cartridge_id, product_id, status,
            current_step, install_fingerprint, created_by_id, ready_at
        )
        VALUES ($1, $2, $3, 'replicon', 'replicon', 'ready',
                'activated', $4, $5, NOW())
        """,
        f"operational-truth-{workspace_id}",
        tenant_id,
        workspace_id,
        f"operational-truth:{workspace_id}",
        user_id,
    )
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": int(user_id),
        "email": email,
    }


def _dataset(sql_rows: str) -> dict:
    return {
        "name": "pnl_mensual",
        "layer": "gold",
        "cartridge": "replicon",
        "sources": [],
        "sql_def": (
            "SELECT proyecto, project_name, mes, margen_bruto_usd, "
            "margen_bruto_pct, wip_usd, revenue_usd, revenue_manager "
            f"FROM (VALUES {sql_rows}) AS extracted"
            "(proyecto, project_name, mes, margen_bruto_usd, "
            "margen_bruto_pct, wip_usd, revenue_usd, revenue_manager)"
        ),
    }


def _context(scope: dict[str, str]) -> dict[str, str]:
    return {
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "role": "admin",
    }


@pytest.mark.asyncio
async def test_live_two_workspace_materialize_intelligence_projection_and_replay(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    postgres_gold_with_native_rls: str,
    monkeypatch,
):
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope_a = await _seed_scope(setup, "a")
        scope_b = await _seed_scope(setup, "b")
    finally:
        await setup.close()

    gold_dsn = postgres_gold_with_native_rls.replace(
        f"postgres:{GOLD_POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", gold_dsn)
    monkeypatch.setenv("OMEGA_GOLD_ROW_CACHE_TTL_SECONDS", "0")
    engine = _TestGoldEngine()
    try:
        result_a = engine.materialize(
            _dataset(
                "('project-a', 'Proyecto A', DATE '2026-01-01', 100.0, "
                "100.0, 0.0, 100.0, 'RM A'),"
                "('project-a', 'Proyecto A', DATE '2026-02-01', 100.0, "
                "100.0, 0.0, 100.0, 'RM A'),"
                "('project-a', 'Proyecto A', DATE '2026-03-01', 10.0, "
                "10.0, 0.0, 100.0, 'RM A')"
            ),
            _context(scope_a),
        )
        result_b = engine.materialize(
            _dataset(
                "('project-b', 'Proyecto B', DATE '2026-03-01', 50.0, "
                "50.0, 0.0, 100.0, 'RM B')"
            ),
            _context(scope_b),
        )
    finally:
        if engine._con is not None:
            engine._con.close()

    assert result_a["row_count"] == 3
    assert result_b["row_count"] == 1
    clear_gold_row_cache()
    assert len(await query_gold_dataset_rows("pnl_mensual", _user(scope_a))) == 3
    assert len(await query_gold_dataset_rows("pnl_mensual", _user(scope_b))) == 1

    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    pool_owners = {
        id(module): module
        for module in (
            auth,
            history.auth,
            persistence.auth,
            control_room_service.dashboard.__globals__["auth"],
        )
    }
    previous_factories = [(module, module.pool) for module in pool_owners.values()]

    async def active_pool():
        return pool

    for module in pool_owners.values():
        module.pool = active_pool
    try:
        body_a = {
            "cartridge_id": "replicon",
            "datasets": ["pnl_mensual"],
            "run_mode": "gold_refresh",
            "run_ref": f"operational-truth:{scope_a['workspace_id']}:refresh-1",
        }
        run_a = await run_intelligence(_user(scope_a), body_a, persist=True)
        replay_a = await run_intelligence(_user(scope_a), body_a, persist=True)
        run_b = await run_intelligence(
            _user(scope_b),
            {
                "cartridge_id": "replicon",
                "datasets": ["pnl_mensual"],
                "run_mode": "gold_refresh",
                "run_ref": f"operational-truth:{scope_b['workspace_id']}:refresh-1",
            },
            persist=True,
        )
        projected_a = await control_room_service.dashboard(
            _user(scope_a), fetcher=query_gold_dataset_rows
        )
        projected_b = await control_room_service.dashboard(
            _user(scope_b), fetcher=query_gold_dataset_rows
        )
    finally:
        for module, previous_factory in previous_factories:
            module.pool = previous_factory
        await pool.close()

    assert run_a["signals"]
    assert replay_a["idempotent"] is True
    assert replay_a["signals"] == []
    assert any(
        item.get("source_dataset") == "pnl_mensual" for item in projected_a["items"]
    )
    assert not any(
        item.get("source_dataset") == "pnl_mensual" for item in projected_b["items"]
    )
    assert run_b["signals"] == []
    assert run_b["skipped_counts"].get("insufficient_history", 0) > 0
    assert not monitor_should_alert(
        {"threshold": {"blockers_present": True}},
        {
            "status": "insufficient_data",
            "data_sufficient": False,
            "signals": run_b["signals"],
            "blockers": run_b["skipped"],
        },
    )
