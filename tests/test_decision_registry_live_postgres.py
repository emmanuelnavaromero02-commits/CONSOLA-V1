from __future__ import annotations

import importlib
import json
import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.control_room.business_council_actors import SYSTEM_MAKER
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_repository import (
    approve_control_room_decision,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from tests.control_room_action_authority_live import _new_user
from tests.test_control_room_live_postgres_implicit_approval import _workspace
from tests.test_control_room_live_postgres_workflows import AsyncNoop, _rows
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)
from tests.test_workspace_decision_idempotency_live import _load_console_main


CARTRIDGE = "sap_successfactors"
DATASET = "sap_successfactors_talent_signals"
MARKER = "Decision creada desde Sala de Control"


def _endpoint(module, method: str, path: str):
    return next(
        route.endpoint
        for route in module.app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or ())
    )


def _signal(item_id: str, tenant_id: str, workspace_id: str) -> dict:
    observation = {
        "id": item_id,
        "kind": "intelligence_signal",
        "metric_type": "scalar",
        "observed_value": 3,
        "observation_date": "2026-07-05",
    }
    return {
        **observation,
        "cartridge": CARTRIDGE,
        "source_system": CARTRIDGE,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "domain": "Personas",
        "source_dataset": DATASET,
        "title": "Movilidad interna",
        "entity_kind": "cohort",
        "entity_id": item_id,
        "entity_label": "Cohorte",
        "description": "Movilidad interna medida",
        "recommendation": "Revisar movilidad interna",
        "severity": "high",
        "data_status": "gold_ready",
        "owner_user_id": None,
        **runtime_row_evidence_fields(
            source_dataset=DATASET,
            source_system=CARTRIDGE,
            cartridge=CARTRIDGE,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_row={"signal_id": item_id},
            locator_field="signal_id",
            observed_at="2026-07-05",
            business_observation=observation,
        ),
    }


async def _approved_signal_decision(conn, admin: dict, item_id: str) -> int:
    tenant_id = admin["active_tenant_id"]
    workspace_id = admin["active_workspace_id"]
    item = _signal(item_id, tenant_id, workspace_id)
    await persist_item_rows(
        conn,
        _rows([item], tenant_id, workspace_id, owner=None),
        owner_scope_id=None,
        workspace_wide=True,
    )
    decision = await create_and_link_decision(
        conn,
        user=admin,
        item=item,
        workspace_id=workspace_id,
        ensure_item_row=AsyncNoop(),
        record_item_event=AsyncNoop(),
        system_maker=SYSTEM_MAKER,
    )
    linked = {**item, "decision_id": int(decision["id"]), "status": "decision_created"}
    await approve_control_room_decision(
        conn,
        workspace_id=workspace_id,
        item_id=item_id,
        decision_id=int(decision["id"]),
        owner_user_id=None,
        item=linked,
        lessons=[],
    )
    return int(decision["id"])


async def _orphan(conn, workspace_id: str, title: str, *, item_id: str | None) -> int:
    decision_id = int(
        await conn.fetchval(
            """INSERT INTO decisions(title, workspace_id, kpis, created_by)
               VALUES ($1, $2, $3::jsonb, 'legacy') RETURNING id""",
            title,
            workspace_id,
            json.dumps([{"source": "control_room"}]),
        )
    )
    await conn.execute(
        """INSERT INTO decision_actions(decision_id, action_text, actor)
           VALUES ($1, $2, 'legacy')""",
        decision_id,
        MARKER,
    )
    if item_id is not None:
        tenant_id = await conn.fetchval(
            "SELECT tenant_id::text FROM workspaces WHERE id = $1", workspace_id
        )
        await conn.execute(
            """INSERT INTO control_room_items(
                   tenant_id, workspace_id, item_id, cartridge_id, domain,
                   source_dataset, item_kind, title, severity, status,
                   decision_id, metadata)
               VALUES ($1, $2, $3, $4, 'Personas', $5, 'intelligence_signal',
                       'Sin evidencia', 'high', 'decision_created', $6, $7::jsonb)""",
            tenant_id,
            workspace_id,
            item_id,
            CARTRIDGE,
            DATASET,
            decision_id,
            json.dumps({"data_status": "invalid_scope"}),
        )
    return decision_id


async def _seed(dsn: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id, workspace_id, suffix = await _workspace(conn)
        _other_tenant, other_workspace, _ = await _workspace(conn)
        admin = await _new_user(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_role="workspace_admin",
            suffix=f"registry-admin-{suffix}",
        )
        writer = await _new_user(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_role="analyst",
            suffix=f"registry-writer-{suffix}",
        )
        outsider = await _new_user(
            conn,
            tenant_id=_other_tenant,
            workspace_id=other_workspace,
            workspace_role="workspace_admin",
            suffix=f"registry-outsider-{suffix}",
        )
        signed = await _approved_signal_decision(conn, admin, f"talent-{suffix}")
        missing = await _orphan(conn, workspace_id, "Huérfana sin ítem", item_id=None)
        ineligible = await _orphan(
            conn, workspace_id, "Huérfana sin evidencia", item_id=f"bad-{suffix}"
        )
        await conn.execute(
            "UPDATE decisions SET assignee_id = $1 WHERE id = ANY($2::bigint[])",
            writer["id"],
            [missing, ineligible],
        )
        return {
            "admin": admin,
            "writer": writer,
            "outsider": outsider,
            "signed": signed,
            "orphans": {missing, ineligible},
        }
    finally:
        await conn.close()


async def _pool(dsn: str) -> asyncpg.Pool:
    async def _init(conn):
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    return await asyncpg.create_pool(dsn, min_size=1, max_size=4, init=_init)


async def _audits(dsn: str, decision_id: int) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """SELECT user_id, action, status, metadata
                 FROM audit_events
                WHERE resource_type = 'decision' AND resource_id = $1
                ORDER BY id""",
            str(decision_id),
        )
        return [
            {**dict(row), "metadata": json.loads(row["metadata"])} for row in rows
        ]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_registry_shows_signed_signal_decisions_and_admin_only_orphans(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch,
) -> None:
    seed = await _seed(postgres_with_real_init_schema)
    main = _load_console_main(monkeypatch)
    summary = importlib.import_module(
        "app.services.control_room.business_decision_summary"
    )
    pool = await _pool(omega_console_live_dsn)
    monkeypatch.setattr(main, "_DEC_POOL", pool)
    try:
        admin = await _endpoint(main, "GET", "/api/decisions")(
            status="open", overdue="", user=seed["admin"]
        )
        writer = await _endpoint(main, "GET", "/api/decisions")(
            status="open", overdue="", user=seed["writer"]
        )
        outsider = await _endpoint(main, "GET", "/api/decisions")(
            status="", overdue="", user=seed["outsider"]
        )
        admin_count = await summary.count_open_business_decisions(pool, seed["admin"])
        writer_count = await summary.count_open_business_decisions(pool, seed["writer"])
        with pytest.raises(HTTPException) as hidden:
            await _endpoint(main, "GET", "/api/decisions/{decision_id}")(min(seed["orphans"]), user=seed["writer"])
    finally:
        await pool.close()
        main._DEC_POOL = None

    flags = {row["id"]: row["orphaned"] for row in admin["decisions"]}
    assert flags == {seed["signed"]: False, **{i: True for i in seed["orphans"]}}
    assert all(row["protected"] for row in admin["decisions"])
    assert writer["decisions"] == []
    assert outsider["decisions"] == []
    assert admin_count == 1
    assert writer_count == 0
    assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_live_admin_closes_orphan_through_the_audited_registry_flow(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch,
) -> None:
    seed = await _seed(postgres_with_real_init_schema)
    orphan = min(seed["orphans"])
    main = _load_console_main(monkeypatch)
    pool = await _pool(omega_console_live_dsn)
    monkeypatch.setattr(main, "_DEC_POOL", pool)
    try:
        with pytest.raises(HTTPException) as writer_close:
            await _endpoint(main, "PATCH", "/api/decisions/{decision_id}")(
                orphan, {"status": "closed"}, user=seed["writer"]
            )
        with pytest.raises(HTTPException) as edit:
            await _endpoint(main, "PATCH", "/api/decisions/{decision_id}")(
                orphan, {"title": "Otro"}, user=seed["admin"]
            )
        with pytest.raises(HTTPException) as delete:
            await _endpoint(main, "DELETE", "/api/decisions/{decision_id}")(orphan, user=seed["admin"])
        closed = await _endpoint(main, "PATCH", "/api/decisions/{decision_id}")(
            orphan,
            {"status": "closed", "outcome": "not_achieved"},
            user=seed["admin"],
        )
        closed_list = await _endpoint(main, "GET", "/api/decisions")(
            status="closed", overdue="", user=seed["admin"]
        )
        open_list = await _endpoint(main, "GET", "/api/decisions")(
            status="open", overdue="", user=seed["admin"]
        )
    finally:
        await pool.close()
        main._DEC_POOL = None

    assert writer_close.value.status_code == 404
    assert edit.value.status_code == 409
    assert delete.value.status_code == 409
    assert closed["status"] == "closed"
    assert closed["outcome"] == "not_achieved"
    assert closed["closed_at"] is not None
    assert [row["id"] for row in closed_list["decisions"]] == [orphan]
    assert orphan not in {row["id"] for row in open_list["decisions"]}
    audits = await _audits(postgres_with_real_init_schema, orphan)
    assert [(a["user_id"], a["action"], a["status"]) for a in audits] == [
        (seed["admin"]["id"], "decision.close", "success")
    ]
    assert audits[0]["metadata"]["control_room_orphan"] is True
    assert audits[0]["metadata"]["from_status"] == "open"
    assert audits[0]["metadata"]["to_status"] == "closed"
