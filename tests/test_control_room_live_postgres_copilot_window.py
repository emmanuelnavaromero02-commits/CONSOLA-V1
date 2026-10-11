from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import copilot_context_authority as authority
from app.services import copilot_context_persistence as persistence
from app.services import copilot_context_service as service
from app.services.db_scope import scoped_db
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@dataclass(frozen=True)
class WindowScope:
    admin_dsn: str
    console_dsn: str
    tenant_id: str
    workspace_a: str
    workspace_b: str
    disabled_entity: str


async def _seed(admin_dsn: str, console_dsn: str) -> WindowScope:
    suffix = uuid.uuid4().hex[:12]
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_id = str(
            await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"copilot-window-{suffix}",
                f"copilot-window-{suffix}",
            )
        )
        workspaces = [
            str(
                await conn.fetchval(
                    "INSERT INTO workspaces (tenant_id, name) "
                    "VALUES ($1::uuid, $2) RETURNING id",
                    tenant_id,
                    f"Copilot window {label} {suffix}",
                )
            )
            for label in ("A", "B")
        ]
        disabled = f"prueba_{suffix}"
        await conn.execute(
            "INSERT INTO entity_config (cartridge_id, entity, enabled) "
            "VALUES ('sap_successfactors', $1, FALSE)",
            disabled,
        )
        rows = [
            ("old-failed", "sap_successfactors_extract_all", "PerPerson", "old", "failed", 10),
            ("r1-emp", "sap_successfactors_extract_all", "EmployeeTime", "r1", "partial", 2),
            ("r1-agg", "sap_successfactors_extract_all", "__extract_all__", "r1", "partial", 2),
            ("r1-off", "sap_successfactors_extract_all", disabled, "r1", "blocked", 2),
            ("c1-ds", "dataset_refresh_chain", "talent_attrition_exposure", "c1", "partial", 2),
            ("c1-agg", "dataset_refresh_chain", "DatasetRefreshChain", "c1", "partial", 2),
            ("r2-agg", "sap_successfactors_extract_all", "__extract_all__", "r2", "failed", 1),
            ("r3-agg", "sap_successfactors_extract_all", "__extract_all__", "r3", "failed", 1),
            ("r3-pos", "sap_successfactors_extract_all", "Position", "r3", "partial", 1),
            ("r1-ok", "sap_successfactors_extract_all", "PerEmail", "r1", "success", 2),
        ]
        for key, dag_id, entity, run, status, days in rows:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, status,
                    started_at, finished_at, tenant_id, workspace_id
                )
                VALUES ($1, $2, 'sap_successfactors', $3, $4, $5,
                        NOW() - make_interval(days => $6::int),
                        NOW() - make_interval(days => $6::int),
                        $7::uuid, $8::uuid)
                """,
                f"{key}-{suffix}",
                dag_id,
                entity,
                f"{run}-{suffix}",
                status,
                days,
                tenant_id,
                workspaces[0],
            )
        mirror_key = f"sf-daily:2026-10-09:{suffix}:PerPerson"
        extra_rows = [
            ("m1-dag", "sap_successfactors_extract_all", "PerPerson", f"m1-{suffix}",
             "partial", {"entity_idempotency_key": mirror_key}),
            (mirror_key, "sap_successfactors_extract", "PerPerson", None, "failed",
             {"source": "extraction_runs_mirror"}),
            ("orphan", "sap_successfactors_extract", "PerJob", None, "failed",
             {"source": "extraction_runs_mirror"}),
            ("c2-agg", "dataset_refresh_chain", "DatasetRefreshChain", f"c2-{suffix}",
             "partial", {}),
            ("c2-mon", "dataset_refresh_chain", "AgentOpsMonitors", f"c2-{suffix}",
             "failed", {}),
        ]
        for key, dag_id, entity, airflow_id, status, extra in extra_rows:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, status,
                    started_at, finished_at, tenant_id, workspace_id, extra
                )
                VALUES ($1, $2, 'sap_successfactors', $3, $4, $5, NOW(), NOW(),
                        $6::uuid, $7::uuid, $8::jsonb)
                """,
                key if key == mirror_key else f"{key}-{suffix}",
                dag_id,
                entity,
                airflow_id,
                status,
                tenant_id,
                workspaces[0],
                json.dumps(extra),
            )
        await conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, status,
                started_at, finished_at, tenant_id, workspace_id
            )
            VALUES ($1, 'sap_successfactors_extract_all', 'sap_successfactors',
                    'PerPerson', $1, 'failed', NOW(), NOW(), $2::uuid, $3::uuid)
            """,
            f"other-{suffix}",
            tenant_id,
            workspaces[1],
        )
    finally:
        await conn.close()
    return WindowScope(
        admin_dsn=admin_dsn,
        console_dsn=console_dsn,
        tenant_id=tenant_id,
        workspace_a=workspaces[0],
        workspace_b=workspaces[1],
        disabled_entity=disabled,
    )


async def _cleanup(scope: WindowScope) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM pipeline_runs WHERE workspace_id = ANY($1::uuid[])",
            [scope.workspace_a, scope.workspace_b],
        )
        await conn.execute(
            "DELETE FROM entity_config WHERE cartridge_id = 'sap_successfactors' "
            "AND entity = $1",
            scope.disabled_entity,
        )
        await conn.execute(
            "DELETE FROM workspaces WHERE id = ANY($1::uuid[])",
            [scope.workspace_a, scope.workspace_b],
        )
        await conn.execute("DELETE FROM tenants WHERE id = $1::uuid", scope.tenant_id)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def window_scope(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> WindowScope:
    scope = asyncio.run(_seed(postgres_with_real_init_schema, omega_console_live_dsn))
    yield scope
    asyncio.run(_cleanup(scope))


def _user(scope: WindowScope) -> dict[str, object]:
    return {
        "id": 303,
        "email": "window-operator@example.com",
        "role": "tenant_admin",
        "active_tenant_id": scope.tenant_id,
        "active_workspace_id": scope.workspace_a,
    }


async def _counts(scope: WindowScope) -> dict:
    pool = await asyncpg.create_pool(scope.console_dsn, min_size=1, max_size=2)
    try:
        async with scoped_db(pool, scope.tenant_id, scope.workspace_a) as conn:
            return await service._load_operational_counts(
                conn, scope.workspace_a, scope.tenant_id
            )
    finally:
        await pool.close()


def test_window_counts_dedupe_rollups_and_skip_disabled_entities(window_scope):
    pipeline = asyncio.run(_counts(window_scope))["pipeline"]
    attention = pipeline["attention"]
    affected = {
        (item["entity"], item["status"]): item["total"] for item in attention["affected"]
    }

    assert pipeline["window_days"] == 7
    assert (attention["failed"], attention["blocked"], attention["partial"]) == (4, 0, 5)
    assert affected == {
        ("EmployeeTime", "partial"): 1,
        ("talent_attrition_exposure", "partial"): 1,
        ("Position", "partial"): 1,
        ("__extract_all__", "failed"): 2,
        ("PerPerson", "partial"): 1,
        ("PerJob", "failed"): 1,
        ("DatasetRefreshChain", "partial"): 1,
        ("AgentOpsMonitors", "failed"): 1,
    }
    assert window_scope.disabled_entity not in {entity for entity, _ in affected}
    assert pipeline["by_status"].get("failed", 0) == 5


def _rec(fingerprint: str, severity: str, body: str, category: str = "pipeline") -> dict:
    return {
        "fingerprint": fingerprint,
        "severity": severity,
        "category": category,
        "title": "Revisar sincronización parcial",
        "body": body,
        "evidence": {},
        "action_kind": "navigate",
        "required_permission": "pipelines.read",
    }


async def _persist(pool, scope: WindowScope, recs: list[dict], retained=()) -> None:
    with patch.object(persistence.audit_service, "record_event", AsyncMock()):
        await persistence.persist_refresh(
            pool,
            _user(scope),
            {
                "tenant_id": scope.tenant_id,
                "workspace_id": scope.workspace_a,
                "status": "ready",
            },
            recs,
            retained_categories=retained,
        )


async def _rows(scope: WindowScope) -> dict[str, tuple[str, str, str]]:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT fingerprint, status, severity, body FROM copilot_recommendations "
            "WHERE workspace_id = $1::uuid",
            scope.workspace_a,
        )
    finally:
        await conn.close()
    return {row["fingerprint"]: (row["status"], row["severity"], row["body"]) for row in rows}


async def _listing(pool, scope: WindowScope, *, include_dismissed: bool = False) -> list[str]:
    with patch.object(authority.auth, "pool", AsyncMock(return_value=pool)):
        payload = await authority.list_recommendations(
            _user(scope), limit=20, include_dismissed=include_dismissed
        )
    return [row["fingerprint"] for row in payload["recommendations"]]


async def _dismiss(scope: WindowScope, fingerprint: str) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        await conn.execute(
            "UPDATE copilot_recommendations SET status = 'dismissed', dismissed_at = NOW() "
            "WHERE workspace_id = $1::uuid AND fingerprint = $2",
            scope.workspace_a,
            fingerprint,
        )
    finally:
        await conn.close()


async def _lifecycle(scope: WindowScope) -> None:
    pool = await asyncpg.create_pool(scope.console_dsn, min_size=1, max_size=2)
    card = "live:pipeline-window"
    healthy = "live:healthy-window"
    other = "live:agentops-window"
    try:
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días: 4 parciales.")])
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días: 2 parciales.")])
        rows = await _rows(scope)
        assert rows[card] == ("active", "warning", "Últimos 7 días: 2 parciales.")
        assert await _listing(pool, scope) == [card]

        await _dismiss(scope, card)
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días: 3 parciales.")])
        assert (await _rows(scope))[card][0] == "dismissed"
        assert await _listing(pool, scope) == []
        assert await _listing(pool, scope, include_dismissed=True) == [card]

        await _persist(pool, scope, [_rec(card, "critical", "Últimos 7 días: 1 fallida.")])
        assert (await _rows(scope))[card][:2] == ("active", "critical")

        await _persist(
            pool,
            scope,
            [_rec(other, "info", "Monitores al día.", category="agentops")],
            retained=("pipeline",),
        )
        assert (await _rows(scope))[card][0] == "active"

        await _persist(pool, scope, [_rec(healthy, "success", "Sin acciones urgentes", "console")])
        rows = await _rows(scope)
        assert rows[card][0] == "superseded"
        assert rows[other][0] == "superseded"
        listed = await _listing(pool, scope, include_dismissed=True)
        assert listed == [healthy]

        await _dismiss(scope, healthy)
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días: 1 parcial.")])
        await _persist(pool, scope, [_rec(healthy, "success", "Sin acciones urgentes", "console")])
        assert (await _rows(scope))[healthy][0] == "active"
    finally:
        await pool.close()


def test_one_card_updates_in_place_and_superseded_cards_are_hidden(window_scope):
    asyncio.run(_lifecycle(window_scope))


async def _briefing_retention(scope: WindowScope) -> None:
    pool = await asyncpg.create_pool(scope.console_dsn, min_size=1, max_size=2)
    card = "live:pipeline-retention"
    briefing = "live:briefing-freshness"
    tick = {
        "sources": [
            {"name": "database.operational_counts", "status": "ready", "data": {}},
            {
                "name": "copilot.proactive_briefing",
                "status": "ready",
                "data": {"highlights": [], "evaluated": False},
            },
        ]
    }
    try:
        fresh = _rec(briefing, "warning", "replicon sin extracciones", "freshness")
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días."), fresh])
        await _dismiss(scope, briefing)
        await _persist(
            pool,
            scope,
            [_rec(card, "warning", "Últimos 7 días.")],
            retained=service.retained_recommendation_categories(tick),
        )
        assert (await _rows(scope))[briefing][0] == "dismissed"
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días."), fresh])
        assert (await _rows(scope))[briefing][0] == "dismissed"
        await _persist(pool, scope, [_rec(card, "warning", "Últimos 7 días.")])
        assert (await _rows(scope))[briefing][0] == "superseded"
    finally:
        await pool.close()


def test_scheduler_tick_without_briefing_keeps_briefing_cards_and_dismissals(window_scope):
    asyncio.run(_briefing_retention(window_scope))


async def _failures(scope: WindowScope) -> list[dict]:
    from app.services import proactive_service

    pool = await asyncpg.create_pool(scope.console_dsn, min_size=1, max_size=2)
    user = {**_user(scope), "allowed_cartridges": ["sap_successfactors"]}
    try:
        with patch.object(proactive_service.auth, "pool", AsyncMock(return_value=pool)):
            return await proactive_service.analyze_extraction_failures(user)
    finally:
        await pool.close()


def test_daily_failure_alert_ignores_mirror_copies_of_dag_attempts(window_scope):
    highlights = asyncio.run(_failures(window_scope))

    assert [(item["id"], item["severity"], item["title"]) for item in highlights] == [
        (
            "failures:sap_successfactors",
            "warning",
            "1 extracción fallida en sap_successfactors",
        )
    ]


async def _tick_workspace(scope: WindowScope) -> str:
    suffix = uuid.uuid4().hex[:12]
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        workspace = str(
            await conn.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1::uuid, $2) RETURNING id",
                scope.tenant_id,
                f"Copilot tick {suffix}",
            )
        )
        await conn.execute(
            "INSERT INTO tenant_entitlements (tenant_id, workspace_id, cartridge_id, product_id, status) "
            "VALUES ($1::uuid, $2::uuid, 'sap_successfactors', 'sap_successfactors', 'active')",
            scope.tenant_id,
            workspace,
        )
        await conn.execute(
            "INSERT INTO cartridge_installations (id, tenant_id, workspace_id, cartridge_id, "
            "product_id, status, current_step, install_fingerprint) "
            "VALUES ($1, $2::uuid, $3::uuid, 'sap_successfactors', 'sap_successfactors', "
            "'ready', 'activated', $1)",
            f"copilot-tick-{suffix}",
            scope.tenant_id,
            workspace,
        )
        rows = [
            ("ok", "sap_successfactors_extract", "sap_successfactors", "PerPerson", "ok", "success"),
            ("x1", "sap_successfactors_extract_all", "sap_successfactors", "__extract_all__", "x1", "failed"),
            ("x2", "sap_successfactors_extract_all", "sap_successfactors", "__extract_all__", "x2", "failed"),
            ("x2-per", "sap_successfactors_extract_all", "sap_successfactors", "PerPerson", "x2", "failed"),
            ("chain", "dataset_refresh_chain", "sap_successfactors", "DatasetRefreshChain", "c1", "failed"),
            ("mon", "dataset_refresh_chain", "sap_successfactors", "AgentOpsMonitors", "c1", "failed"),
            ("daily", "sap_successfactors_daily_extract", "sap_successfactors",
             "SuccessFactorsDailyExtract", "d1", "failed"),
            ("sched", "entity_scheduler", "platform", "EntityScheduler", "s1", "failed"),
            ("runner", "agent_runner", "platform", "AgentRunner", "a1", "failed"),
        ]
        for key, dag_id, cartridge, entity, run, status in rows:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, status,
                    started_at, finished_at, tenant_id, workspace_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW(), $7::uuid, $8::uuid)
                """,
                f"{key}-{suffix}",
                dag_id,
                cartridge,
                entity,
                f"{run}-{suffix}",
                status,
                scope.tenant_id,
                workspace,
            )
    finally:
        await conn.close()
    return workspace


async def _drop_workspace(scope: WindowScope, workspace: str) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        for table in ("copilot_recommendations", "copilot_context_snapshots", "pipeline_runs"):
            await conn.execute(f"DELETE FROM {table} WHERE workspace_id = $1::uuid", workspace)
        await conn.execute("DELETE FROM workspaces WHERE id = $1::uuid", workspace)
    finally:
        await conn.close()


async def _orchestration_rows_are_not_extractions(scope: WindowScope) -> None:
    from app.services import pipeline_attention

    workspace = await _tick_workspace(scope)
    pool = await asyncpg.create_pool(scope.console_dsn, min_size=1, max_size=2)
    admin = {**_user(scope), "role": "admin", "active_workspace_id": workspace}
    try:
        with patch.object(service.proactive_service.auth, "pool", AsyncMock(return_value=pool)):
            highlights = await service.proactive_service.analyze_extraction_failures(admin)
        async with scoped_db(pool, scope.tenant_id, workspace) as conn:
            attention = await pipeline_attention.load_attention(conn, workspace, scope.tenant_id)
    finally:
        await pool.close()
        await _drop_workspace(scope, workspace)

    assert [(item["id"], item["title"]) for item in highlights] == [
        ("failures:sap_successfactors", "2 extracciones fallidas en sap_successfactors")
    ]
    failed = {item["entity"]: item for item in attention["affected"] if item["status"] == "failed"}
    assert failed["AgentOpsMonitors"]["process"] is True
    assert {"DatasetRefreshChain", "SuccessFactorsDailyExtract", "PerPerson"} <= set(failed)


def test_daily_failure_alert_skips_orchestration_but_panel_keeps_the_monitor(window_scope):
    asyncio.run(_orchestration_rows_are_not_extractions(window_scope))


async def _statuses(scope: WindowScope, workspace: str) -> dict[str, tuple[str, str]]:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT fingerprint, status, title FROM copilot_recommendations "
            "WHERE workspace_id = $1::uuid",
            workspace,
        )
    finally:
        await conn.close()
    return {row["fingerprint"]: (row["status"], row["title"]) for row in rows}


async def _hourly_tick_resolves_briefing_cards(scope: WindowScope) -> None:
    workspace = await _tick_workspace(scope)
    pool = await asyncpg.create_pool(scope.console_dsn, min_size=1, max_size=2)
    system = service._system_user(scope.tenant_id, workspace)
    operator = {**_user(scope), "active_workspace_id": workspace}
    stale = service._fingerprint("briefing", "freshness:sap_successfactors")
    failing = service._fingerprint("briefing", "failures:sap_successfactors")
    empty = AsyncMock(return_value={})
    loaders = (
        "ops_summary",
        "agents_ops",
        "sap_successfactors_talent_kpis",
        "sap_successfactors_talent_metadata_readiness",
        "sap_successfactors_talent_overview",
    )
    try:
        with patch.object(service.auth, "pool", AsyncMock(return_value=pool)):
            with patch.object(persistence.audit_service, "record_event", AsyncMock()):
                await persistence.persist_refresh(
                    pool,
                    operator,
                    {"tenant_id": scope.tenant_id, "workspace_id": workspace, "status": "ready"},
                    [
                        {**_rec(stale, "critical", "80 h sin actualizar", "freshness"),
                         "required_permission": "copilot.use"},
                        {**_rec(failing, "warning", "1 extracción fallida", "failures"),
                         "required_permission": "copilot.use"},
                    ],
                )
            conn = await asyncpg.connect(scope.admin_dsn)
            try:
                await conn.execute(
                    "UPDATE copilot_recommendations SET status = 'dismissed', dismissed_at = NOW() "
                    "WHERE workspace_id = $1::uuid AND fingerprint = $2",
                    workspace,
                    failing,
                )
            finally:
                await conn.close()

            patches = [patch.object(service.control_room_service, name, empty) for name in loaders]
            for item in patches:
                item.start()
            try:
                tick = await service.collect_workspace_context(
                    system, generated_by="scheduler", persist=True
                )
                after_tick = await _statuses(scope, workspace)
                broken = AsyncMock(side_effect=RuntimeError("relation missing"))
                with patch.object(
                    service.proactive_service, "analyze_extraction_failures", broken
                ):
                    degraded = await service.collect_workspace_context(
                        system, generated_by="scheduler", persist=True
                    )
                after_degraded = await _statuses(scope, workspace)
            finally:
                for item in patches:
                    item.stop()
    finally:
        await pool.close()
        await _drop_workspace(scope, workspace)

    briefing = next(
        item for item in tick["sources"] if item["name"] == "copilot.proactive_briefing"
    )
    assert briefing["status"] == "ready"
    assert [item["id"] for item in briefing["data"]["highlights"]] == [
        "failures:sap_successfactors"
    ]
    assert after_tick[stale][0] == "superseded"
    assert after_tick[failing] == (
        "dismissed",
        "2 extracciones fallidas en sap_successfactors",
    )
    assert not any("sin extracciones" in title for _status, title in after_tick.values())
    assert "failures" in service.retained_recommendation_categories(degraded)
    assert after_degraded[failing][0] == "dismissed"


def test_hourly_tick_resolves_briefing_cards_whose_condition_is_gone(window_scope):
    asyncio.run(_hourly_tick_resolves_briefing_cards(window_scope))
