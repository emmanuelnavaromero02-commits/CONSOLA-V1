"""Mission 5: an attested monitor alert reaches /control-room, and only that.

These tests mint a real ticket through ``evidence_tickets`` (real HMAC signing
with the test keyring), then push the persisted alert through the same
eligibility and v2 projection the page uses.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.control_room import (
    attested_monitor_alerts,
    evidence_tickets,
    surface_snapshot,
)
from app.services.control_room.attested_monitor_alerts import (
    MonitorAlertSurface,
    project_monitor_alerts,
)
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from app.services.control_room.surface_snapshot import SurfaceScope, SurfaceSnapshot

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER_WORKSPACE = "44444444-4444-4444-8444-444444444444"
AGENT = "33333333-3333-4333-8333-333333333333"
UI_FORBIDDEN = re.compile(r"Aprobar|Ejecutar|Sí, ejecutar")

CONTRACT = {
    "engine": "wisdom_bit",
    "wisdom_bit_id": "WB-FINANZAS",
    "domain": "Finanzas",
    "dataset": "finance_kpis",
    "severity": "medium",
    "dedup_key": "sap_s4hana:finance-monitor:WB-FINANZAS",
}
PAYLOAD = {
    "wisdom_bit_id": "WB-FINANZAS",
    "status": "degraded",
    "signals": {"count": 3, "items": [{}, {}, {}]},
}
USER = {
    "id": 7,
    "email": "ops@example.com",
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "allowed_cartridges": ["sap_s4hana"],
    "role": "admin",
}


class _FakeConn:
    def __init__(
        self,
        *,
        lease_ok: bool = True,
        agent_row: dict | None = None,
        duplicate: bool = False,
    ):
        self.lease_ok = lease_ok
        self.agent_row = agent_row
        self.duplicate = duplicate
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        if "assert_scheduled_effect_authority" in sql and not self.lease_ok:
            raise RuntimeError("scheduled effect authority is stale")
        return "OK"

    async def fetchrow(self, sql: str, *args: object):
        self.executed.append((sql, args))
        return self.agent_row

    async def fetchval(self, sql: str, *args: object):
        self.executed.append((sql, args))
        assert "INSERT INTO control_room_evidence_tickets" in sql
        return None if self.duplicate else args[0]

    def inserts(self) -> list[tuple]:
        return [
            args
            for sql, args in self.executed
            if "INSERT INTO control_room_evidence_tickets" in sql
        ]


def _agent_row(contract: dict | None = None, cartridge_id: str = "sap_s4hana") -> dict:
    return {
        "cartridge_id": cartridge_id,
        "slug": "finance-monitor",
        "extra": json.dumps({"monitor": contract if contract is not None else CONTRACT}),
    }


@pytest.fixture()
def scoped(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str]] = []

    @asynccontextmanager
    async def _scoped_db(pool, tenant_id, workspace_id):
        calls.append((tenant_id, workspace_id))
        yield pool

    monkeypatch.setattr(evidence_tickets, "scoped_db", _scoped_db)
    return calls


async def _mint(
    conn: _FakeConn,
    *,
    source: str = "agent_runner",
    payload=PAYLOAD,
    wisdom_bit_id: str = "WB-FINANZAS",
    cartridge_id: str = "sap_s4hana",
):
    return await evidence_tickets.mint_monitor_evidence_ticket(
        conn,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        agent_id=AGENT,
        agent_run_id=41,
        schedule_run_id=9,
        fencing_token=3,
        internal_service="mcp-infra",
        security_context_source=source,
        requested_wisdom_bit_id=wisdom_bit_id,
        requested_cartridge_id=cartridge_id,
        payload=payload,
    )


async def _minted_reference(scoped) -> tuple[str, dict, _FakeConn]:
    conn = _FakeConn(agent_row=_agent_row())
    handle = await _mint(conn)
    assert handle is not None
    (insert,) = conn.inserts()
    return insert[3], json.loads(insert[20]), conn


def _item_id() -> str:
    identity = evidence_tickets.monitor_alert_identity(
        agent_id=AGENT,
        workspace_id=WORKSPACE,
        cartridge_id="sap_s4hana",
        slug="finance-monitor",
        contract=CONTRACT,
        payload=PAYLOAD,
    )
    assert identity is not None
    return identity.item_id


def _row(item_id: str, *, signal_count: int = 3, **overrides: object) -> dict:
    row = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "item_id": item_id,
        "cartridge_id": "sap_s4hana",
        "domain": "Finanzas",
        "source_dataset": "finance_kpis",
        "title": "Monitor de Finanzas: WB-FINANZAS requiere atencion",
        "severity": "medium",
        "status": "open",
        "entity_label": "WB-FINANZAS",
        "metadata": {
            "source": "agent",
            "agent_id": AGENT,
            "agent_run_id": "41",
            "alert_type": "wisdombit_monitor",
            "analysis_evidence": {
                "analysis_type": "wb-finanzas_monitor",
                "engine": "wisdom_bit",
                "engine_run_id": f"agent:{AGENT}:run:41:wisdombit:WB-FINANZAS",
                "metrics": {"status": "degraded", "signal_count": signal_count},
                "blockers": [],
            },
        },
        "last_seen_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


# ── Mint ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mint_binds_the_ticket_to_the_alert_mcp_infra_will_raise(scoped, caplog):
    caplog.set_level(logging.INFO, logger=evidence_tickets.logger.name)
    item_id, reference, conn = await _minted_reference(scoped)

    assert item_id == _item_id()
    assert scoped == [(TENANT, WORKSPACE)]
    assert reference["source_locator"] == {
        "relation": "finance_kpis",
        "field": "item_id",
        "value": item_id,
    }
    assert reference["business_binding"]["identity"]["value"] == item_id
    assert reference["business_binding"]["observation"]["value"] == "3"
    # A bounded lock wait, then the lease, before anything is read or written.
    assert conn.executed[0][0] == evidence_tickets._LOCK_TIMEOUT_SQL
    assert "assert_scheduled_effect_authority" in conn.executed[1][0]
    (insert,) = conn.inserts()
    assert insert[5] == "41"
    assert "ON CONFLICT (schedule_run_id, fencing_token) DO NOTHING" in (
        evidence_tickets._INSERT_TICKET_SQL
    )
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "control_room.evidence.minted" in logs
    assert reference["server_attestation"] not in logs
    assert reference["source_row_hash"] not in logs


@pytest.mark.asyncio
async def test_mint_returns_only_an_opaque_handle(scoped):
    conn = _FakeConn(agent_row=_agent_row())
    handle = await _mint(conn)

    assert isinstance(handle, str)
    assert re.fullmatch(r"[0-9a-f]{32}", handle)


@pytest.mark.asyncio
async def test_conversational_context_never_mints(scoped):
    conn = _FakeConn(agent_row=_agent_row())

    assert await _mint(conn, source="copilot") is None
    assert conn.executed == []


@pytest.mark.asyncio
async def test_stale_lease_never_mints(scoped):
    conn = _FakeConn(lease_ok=False, agent_row=_agent_row())

    assert await _mint(conn) is None
    assert conn.inserts() == []


@pytest.mark.asyncio
async def test_agent_without_monitor_contract_never_mints(scoped):
    conn = _FakeConn(agent_row=_agent_row(contract={}))

    assert await _mint(conn) is None
    assert conn.inserts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wisdom_bit_id", "cartridge_id"),
    [("WB-OPERACIONES", "sap_s4hana"), ("WB-TALENTO", "sap_s4hana"), ("WB-FINANZAS", "replicon")],
)
async def test_counts_computed_for_another_wisdom_bit_are_never_signed(
    scoped, wisdom_bit_id, cartridge_id
):
    conn = _FakeConn(agent_row=_agent_row())
    before = evidence_tickets.evidence_counters()["identity_invalid"]

    assert await _mint(conn, wisdom_bit_id=wisdom_bit_id, cartridge_id=cartridge_id) is None
    assert conn.inserts() == []
    assert evidence_tickets.evidence_counters()["identity_invalid"] == before + 1


@pytest.mark.asyncio
async def test_hyphenated_cartridge_and_lowercase_bit_are_the_same_request(scoped):
    conn = _FakeConn(agent_row=_agent_row())

    assert await _mint(conn, wisdom_bit_id="wb-finanzas", cartridge_id="sap-s4hana")


@pytest.mark.asyncio
async def test_second_mint_under_the_same_lease_is_refused(scoped):
    conn = _FakeConn(agent_row=_agent_row(), duplicate=True)
    before = evidence_tickets.evidence_counters()["duplicate_lease"]

    assert await _mint(conn) is None
    assert evidence_tickets.evidence_counters()["duplicate_lease"] == before + 1


@pytest.mark.asyncio
async def test_zero_signals_never_mints(scoped):
    conn = _FakeConn(agent_row=_agent_row())
    payload = {**PAYLOAD, "signals": {"count": 0, "items": []}}

    assert await _mint(conn, payload=payload) is None
    assert conn.inserts() == []


@pytest.mark.asyncio
async def test_silent_signing_failure_is_logged_and_counted(
    scoped, monkeypatch: pytest.MonkeyPatch, caplog
):
    monkeypatch.setattr(
        evidence_tickets, "scoped_runtime_evidence_fields", lambda *a, **k: {}
    )
    before = evidence_tickets.evidence_counters()["sign_failed"]
    caplog.set_level(logging.WARNING, logger=evidence_tickets.logger.name)
    conn = _FakeConn(agent_row=_agent_row())

    assert await _mint(conn) is None
    assert conn.inserts() == []
    assert evidence_tickets.evidence_counters()["sign_failed"] == before + 1
    assert any(
        "control_room.evidence.sign_failed" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_database_failure_never_breaks_the_monitor(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def _broken(pool, tenant_id, workspace_id):
        raise RuntimeError("connection refused")
        yield pool  # pragma: no cover

    monkeypatch.setattr(evidence_tickets, "scoped_db", _broken)

    assert await _mint(_FakeConn(agent_row=_agent_row())) is None


# ── Resolve ──────────────────────────────────────────────────────────────────


class _ResolveConn:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.calls.append((sql, args))
        return self.rows


def _alert(item_id: str, agent_run_id: object = "41") -> dict:
    return {"item_id": item_id, "agent_id": AGENT, "agent_run_id": agent_run_id}


@pytest.mark.asyncio
async def test_resolve_uses_only_console_written_ticket_columns(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)
    conn = _ResolveConn(
        [{"item_id": item_id, "handle": "cd" * 16, "reference": json.dumps(reference)}]
    )

    resolved = await evidence_tickets.resolve_monitor_evidence(
        conn, tenant_id=TENANT, workspace_id=WORKSPACE, alerts=[_alert(item_id)]
    )

    assert resolved == {item_id: [reference]}
    sql, args = conn.calls[0]
    assert "t.tenant_id = $1::uuid" in sql
    assert "t.workspace_id = $2::uuid" in sql
    assert "t.expires_at > clock_timestamp()" in sql
    assert "t.agent_id = alert.agent_id" in sql
    assert "PARTITION BY t.item_id" in sql
    assert "IS NOT DISTINCT FROM alert.agent_run_id" in sql
    # Nothing a status transition or mcp-infra can move picks the ticket.
    assert "last_seen_at" not in sql
    assert args == (
        TENANT,
        WORKSPACE,
        [item_id],
        [AGENT],
        ["41"],
        evidence_tickets.MAX_TICKETS_PER_ITEM,
    )


@pytest.mark.asyncio
async def test_resolve_drops_tampered_or_foreign_references(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)
    tampered = json.loads(json.dumps(reference))
    tampered["business_binding"]["observation"]["value"] = "30"
    conn = _ResolveConn(
        [
            {"item_id": item_id, "handle": "01" * 16, "reference": json.dumps(tampered)},
            {"item_id": "agent_alert:" + "0" * 32, "handle": "02" * 16, "reference": json.dumps(reference)},
        ]
    )
    before = evidence_tickets.evidence_counters()["binding_mismatch"]

    resolved = await evidence_tickets.resolve_monitor_evidence(
        conn, tenant_id=TENANT, workspace_id=WORKSPACE, alerts=[_alert(item_id)]
    )
    other_scope = await evidence_tickets.resolve_monitor_evidence(
        _ResolveConn([{"item_id": item_id, "handle": "03" * 16, "reference": json.dumps(reference)}]),
        tenant_id=TENANT,
        workspace_id=OTHER_WORKSPACE,
        alerts=[_alert(item_id)],
    )

    assert resolved == {}
    assert other_scope == {}
    assert evidence_tickets.evidence_counters()["binding_mismatch"] >= before + 3


@pytest.mark.asyncio
async def test_resolve_skips_alerts_without_a_trusted_agent_id():
    conn = _ResolveConn([])
    resolved = await evidence_tickets.resolve_monitor_evidence(
        conn,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        alerts=[{"item_id": "agent_alert:x", "agent_id": "not-a-uuid", "agent_run_id": "1"}],
    )

    assert resolved == {}
    assert conn.calls == []


# ── Projection onto /control-room ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attested_alert_appears_on_control_room_with_its_narrative(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)
    surface = project_monitor_alerts(
        [_row(item_id)], {item_id: [reference]}, user=USER
    )

    assert [item["id"] for item in surface.items] == [item_id]
    snapshot = SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=SurfaceScope(tenant_id=TENANT, workspace_id=WORKSPACE),
        items=surface.items,
        diagnostics=(),
        sources=(),
        installations=(),
        narratives=surface.narratives,
    )
    response = build_business_experience_v2(
        snapshot, user=USER, enabled_template_ids=frozenset(), actions_by_item={}
    )
    facts = [fact for section in response.sections for fact in section.facts]

    assert len(facts) == 1
    fact = facts[0]
    assert fact.kind == "alert"
    assert fact.metric is not None and fact.metric.value == 3
    assert fact.narrative is not None
    assert fact.narrative.confidence_label == "baja"
    assert "sin simulaci" in fact.narrative.basis_note.lower()
    assert fact.narrative.evidence_note is not None
    assert "3 senales" in fact.narrative.evidence_note

    public = response.model_dump_json(exclude_none=True)
    for secret in ("server_attestation", "source_row_hash", reference["server_attestation"], item_id, AGENT):
        assert secret not in public
    assert not UI_FORBIDDEN.search(public)


@pytest.mark.asyncio
async def test_status_transitions_do_not_hide_the_attested_fact(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)
    row = _row(item_id, status="decision_created", last_seen_at=datetime(2030, 1, 1, tzinfo=UTC))

    surface = project_monitor_alerts([row], {item_id: [reference]}, user=USER)

    assert [item["id"] for item in surface.items] == [item_id]


@pytest.mark.asyncio
async def test_alert_whose_persisted_value_differs_from_the_attestation_is_hidden(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)

    surface = project_monitor_alerts(
        [_row(item_id, signal_count=4)], {item_id: [reference]}, user=USER
    )

    assert surface.items == ()
    assert surface.narratives == {}


@pytest.mark.asyncio
async def test_reference_minted_for_another_item_does_not_transfer(scoped):
    _item_id_value, reference, _conn = await _minted_reference(scoped)
    other = "agent_alert:" + "f" * 32

    surface = project_monitor_alerts([_row(other)], {other: [reference]}, user=USER)

    assert surface.items == ()


@pytest.mark.asyncio
async def test_alert_without_a_ticket_is_not_shown_even_with_copied_evidence(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)
    row = _row(item_id)
    row["metadata"]["evidence_refs"] = [reference]

    surface = project_monitor_alerts([row], {}, user=USER)

    assert surface.items == ()


@pytest.mark.asyncio
async def test_cartridge_outside_the_user_scope_is_filtered(scoped):
    item_id, reference, _conn = await _minted_reference(scoped)
    user = {**USER, "allowed_cartridges": ["replicon"]}

    surface = project_monitor_alerts([_row(item_id)], {item_id: [reference]}, user=user)

    assert surface.items == ()
    assert surface.narratives == {}


def test_loader_sql_is_scoped_and_hides_closed_alerts():
    sql = attested_monitor_alerts._ALERTS_SQL
    assert "workspace_id = $1::uuid" in sql
    assert "tenant_id = $2::uuid" in sql
    assert "item_kind = 'agent_alert'" in sql
    assert "owner_user_id" in sql
    assert set(attested_monitor_alerts.VISIBLE_STATUSES).isdisjoint(
        {"dismissed", "resolved"}
    )


@pytest.mark.asyncio
async def test_loader_fails_open(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        attested_monitor_alerts.auth, "pool", AsyncMock(side_effect=RuntimeError("down"))
    )

    surface = await attested_monitor_alerts.load_attested_monitor_alerts(USER)

    assert surface == MonitorAlertSurface()


# ── Snapshot merge ───────────────────────────────────────────────────────────


def _collect(items: list[dict]):
    async def _fake(*_args, **_kwargs):
        return {"items": items, "diagnostics": [], "sources": [], "installations": []}

    return _fake


@pytest.mark.asyncio
async def test_snapshot_merges_attested_alerts_after_live_items(
    monkeypatch: pytest.MonkeyPatch,
):
    alert = {"id": "agent_alert:" + "a" * 32, "tenant_id": TENANT, "workspace_id": WORKSPACE}
    live = {"id": "kpi:1", "tenant_id": TENANT, "workspace_id": WORKSPACE}
    monkeypatch.setattr(
        surface_snapshot.control_room_service, "_collect_items", _collect([live])
    )
    monkeypatch.setattr(
        surface_snapshot,
        "load_attested_monitor_alerts",
        AsyncMock(
            return_value=MonitorAlertSurface(
                items=(alert,), narratives={alert["id"]: {"status": "template"}}
            )
        ),
    )

    snapshot = await surface_snapshot.collect_surface_snapshot(USER)

    assert [item["id"] for item in snapshot.items] == ["kpi:1", alert["id"]]
    assert snapshot.narratives == {alert["id"]: {"status": "template"}}


@pytest.mark.asyncio
async def test_snapshot_keeps_the_live_item_on_id_collision(monkeypatch: pytest.MonkeyPatch):
    shared = {"id": "agent_alert:" + "b" * 32, "tenant_id": TENANT, "workspace_id": WORKSPACE}
    monkeypatch.setattr(
        surface_snapshot.control_room_service, "_collect_items", _collect([shared])
    )
    monkeypatch.setattr(
        surface_snapshot,
        "load_attested_monitor_alerts",
        AsyncMock(
            return_value=MonitorAlertSurface(
                items=({**shared, "title": "monitor"},),
                narratives={shared["id"]: {"status": "template"}},
            )
        ),
    )

    snapshot = await surface_snapshot.collect_surface_snapshot(USER)

    assert list(snapshot.items) == [shared]
    assert snapshot.narratives == {}


@pytest.mark.asyncio
async def test_snapshot_scope_validation_still_covers_monitor_alerts(
    monkeypatch: pytest.MonkeyPatch,
):
    foreign = {"id": "agent_alert:" + "c" * 32, "tenant_id": TENANT, "workspace_id": OTHER_WORKSPACE}
    monkeypatch.setattr(
        surface_snapshot.control_room_service, "_collect_items", _collect([])
    )
    monkeypatch.setattr(
        surface_snapshot,
        "load_attested_monitor_alerts",
        AsyncMock(return_value=MonitorAlertSurface(items=(foreign,))),
    )

    with pytest.raises(HTTPException) as raised:
        await surface_snapshot.collect_surface_snapshot(USER)

    assert raised.value.status_code == 404
