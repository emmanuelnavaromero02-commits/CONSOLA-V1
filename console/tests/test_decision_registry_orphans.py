from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import app.main as console_main
from app.domains.decisions import orphans
from app.domains.decisions.business_visibility import (
    count_business_decisions,
    fetch_business_decisions,
    filter_decision_rows,
)
from app.services import audit_service
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
OTHER_WORKSPACE = "workspace-b"
CARTRIDGE = "sap_successfactors"
DATASET = "sap_successfactors_talent_signals"
MARKER = "Decision creada desde Sala de Control"
LIST_SQL = "SELECT * FROM decisions WHERE workspace_id = $1 ORDER BY created_at DESC, id DESC"


def _endpoint(module, method: str, path: str):
    return next(
        route.endpoint
        for route in module.app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or ())
    )


def _evidence(item_id: str, workspace_id: str) -> dict:
    return runtime_row_evidence_fields(
        source_dataset=DATASET,
        source_system=CARTRIDGE,
        cartridge=CARTRIDGE,
        tenant_id=TENANT,
        workspace_id=workspace_id,
        source_row={"signal_id": item_id},
        locator_field="signal_id",
        observed_at="2026-07-05",
        business_observation={
            "id": item_id,
            "kind": "intelligence_signal",
            "metric_type": "scalar",
            "observed_value": 3,
            "observation_date": "2026-07-05",
        },
    )


def _signal_row(
    decision_id: int,
    item_id: str,
    *,
    evidence_workspace: str | None = WORKSPACE,
) -> dict:
    observation = {
        "source_system": CARTRIDGE,
        "source_dataset": DATASET,
        "data_status": "gold_ready",
        "metric_type": "scalar",
        "observed_value": 3,
        "observation_date": "2026-07-05",
    }
    row = {
        "decision_id": decision_id,
        "item_id": item_id,
        "item_kind": "intelligence_signal",
        "cartridge_id": CARTRIDGE,
        "source_dataset": DATASET,
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "owner_user_id": None,
        "status": "approved",
        "metadata": {**observation, **_evidence(item_id, WORKSPACE)},
    }
    provenance = workflow_eligibility_provenance(
        normalize_persisted_business_item(row),
        stage=WorkflowStage.APPROVED,
        workspace_id=WORKSPACE,
        decision_id=decision_id,
    )
    stored = dict(observation)
    if evidence_workspace is not None:
        stored |= _evidence(item_id, evidence_workspace)
    return {**row, "metadata": {**stored, DECISION_PROVENANCE_KEY: provenance}}


def _decision(decision_id: int, **overrides) -> dict:
    return {
        "id": decision_id,
        "title": f"Decision {decision_id}",
        "description": "",
        "status": "open",
        "outcome": None,
        "workspace_id": WORKSPACE,
        "created_by_id": None,
        "created_by": None,
        "assignee_id": None,
        "kpis": [{"source": "control_room"}],
        "created_at": datetime(2026, 7, 1, 12, 0, decision_id % 60, tzinfo=UTC),
        "closed_at": None,
        **overrides,
    }


def _selected_columns(sql: str) -> list[str]:
    head = sql.split("FROM control_room_items", 1)[0]
    return [column.strip() for column in head.split("SELECT", 1)[1].split(",")]


class RegistryDb:
    def __init__(self, decisions: list[dict], items: list[dict], origins=()):
        self.decisions = {row["id"]: dict(row) for row in decisions}
        self.items = [dict(row) for row in items]
        self.origins = set(origins)
        self.linked_columns: list[str] = []
        self.statements: list[str] = []

    def transaction(self):
        return _Transaction()

    async def execute(self, sql: str, *_args):
        self.statements.append(" ".join(sql.split()))
        return "SELECT 1"

    def _visible(self, sql: str, args: tuple) -> list[dict]:
        rows = [row for row in self.decisions.values() if row["workspace_id"] == args[0]]
        owner = re.search(r"created_by_id = \$(\d+)", sql)
        if owner:
            uid = args[int(owner.group(1)) - 1]
            rows = [r for r in rows if uid in (r["created_by_id"], r["assignee_id"])]
        status = re.search(r"AND status = \$(\d+)", sql)
        if status:
            rows = [r for r in rows if r["status"] == args[int(status.group(1)) - 1]]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)

    async def fetch(self, sql: str, *args):
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if "WITH RECURSIVE" in normalized:
            return []
        if "FROM control_room_items" in normalized and "decision_id = ANY" in normalized:
            self.linked_columns = _selected_columns(normalized)
            return [
                {column: row.get(column) for column in self.linked_columns}
                for row in self.items
                if row["workspace_id"] == args[0] and row["decision_id"] in args[1]
            ]
        if "FROM decision_actions action" in normalized:
            return [{"decision_id": d} for d in sorted(self.origins & set(args[1]))]
        if "FROM decisions AS decision" in normalized:
            linked = {row["decision_id"] for row in self.items}
            return [{"id": d} for d in args[1] if d in linked]
        if "decision_page" in normalized or "FROM decision_actions WHERE" in normalized:
            return []
        if normalized.startswith("SELECT * FROM decisions"):
            return self._visible(normalized, args)[: args[-1]]
        raise AssertionError(normalized)

    async def fetchrow(self, sql: str, *args):
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if normalized.startswith("SELECT * FROM decisions WHERE id = $1"):
            row = self.decisions.get(args[0])
            if not row or row["workspace_id"] != args[1]:
                return None
            if len(args) > 2 and args[2] not in (row["created_by_id"], row["assignee_id"]):
                return None
            return dict(row)
        if normalized.startswith("UPDATE decisions SET"):
            row = self.decisions.get(args[-2])
            if not row or row["workspace_id"] != args[-1]:
                return None
            assignments = normalized.split("SET ", 1)[1].split(" WHERE ", 1)[0]
            for field, value in re.findall(r"(\w+) = (\$\d+|COALESCE)", assignments):
                if value == "COALESCE":
                    row[field] = row[field] or datetime.now(UTC)
                else:
                    row[field] = args[int(value[1:]) - 1]
            return dict(row)
        raise AssertionError(normalized)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _registry() -> RegistryDb:
    return RegistryDb(
        decisions=[
            _decision(1, created_by="system:control-room"),
            _decision(2, created_by_id=7),
            _decision(3, created_by_id=7),
            _decision(4, kpis=[]),
            _decision(5, kpis=[{"provenance": {"origin": "manual"}}], created_by_id=7),
            _decision(6, workspace_id=OTHER_WORKSPACE, created_by_id=7),
        ],
        items=[
            _signal_row(1, "talent-signed"),
            _signal_row(2, "talent-unsigned", evidence_workspace=None),
            _signal_row(3, "talent-foreign", evidence_workspace=OTHER_WORKSPACE),
        ],
        origins={4},
    )


def _user(user_id: int, *, admin: bool) -> dict:
    return {
        "id": user_id,
        "email": f"user-{user_id}@example.com",
        "role": "user",
        "workspace_role": "workspace_admin" if admin else "analyst",
        "active_tenant_id": TENANT,
        "active_workspace_id": WORKSPACE,
    }


@pytest.mark.asyncio
async def test_signed_signal_decision_is_visible_and_counted_through_cartridge_column():
    db = _registry()

    visible = await filter_decision_rows(
        db,
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        rows=[db.decisions[i] for i in (1, 2, 3, 4, 5)],
    )
    total = await count_business_decisions(
        db, sql=LIST_SQL, params=[WORKSPACE], workspace_id=WORKSPACE, tenant_id=TENANT
    )

    assert "cartridge_id" in db.linked_columns
    assert [row["id"] for row in visible] == [1, 5]
    assert total == 2


@pytest.mark.asyncio
async def test_without_cartridge_column_the_signed_signal_fails_closed():
    db = _registry()
    for row in db.items:
        row.pop("cartridge_id")

    visible = await filter_decision_rows(
        db, workspace_id=WORKSPACE, tenant_id=TENANT, rows=[db.decisions[1]]
    )

    assert visible == []


@pytest.mark.asyncio
async def test_orphans_are_listed_once_and_flagged_but_never_counted():
    db = _registry()

    listed = await fetch_business_decisions(
        db,
        sql=LIST_SQL,
        params=[WORKSPACE],
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        include_orphans=True,
    )
    default = await fetch_business_decisions(
        db, sql=LIST_SQL, params=[WORKSPACE], workspace_id=WORKSPACE, tenant_id=TENANT
    )
    total = await count_business_decisions(
        db, sql=LIST_SQL, params=[WORKSPACE], workspace_id=WORKSPACE, tenant_id=TENANT
    )

    flags = {row["id"]: row["orphaned"] for row in listed}
    assert len(listed) == len(flags) == 5
    assert flags == {1: False, 2: True, 3: True, 4: True, 5: False}
    assert all("_control_room_origin" not in row for row in listed)
    assert sorted(row["id"] for row in default) == [1, 5]
    assert all("orphaned" not in row for row in default)
    assert total == 2


@pytest.mark.asyncio
async def test_admin_registry_shows_orphans_as_protected(monkeypatch):
    db = _registry()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))

    admin = await _endpoint(console_main, "GET", "/api/decisions")(
        status="open", overdue="", user=_user(9, admin=True)
    )
    writer = await _endpoint(console_main, "GET", "/api/decisions")(
        status="open", overdue="", user=_user(7, admin=False)
    )

    by_id = {row["id"]: row for row in admin["decisions"]}
    assert sorted(by_id) == [1, 2, 3, 4, 5]
    assert {i for i, row in by_id.items() if row["orphaned"]} == {2, 3, 4}
    assert all(by_id[i]["protected"] for i in (1, 2, 3, 4))
    assert by_id[5]["protected"] is False
    assert [row["id"] for row in writer["decisions"]] == [5]
    assert writer["decisions"][0]["orphaned"] is False


@pytest.mark.asyncio
async def test_orphan_detail_is_admin_only(monkeypatch):
    db = _registry()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))

    detail = await _endpoint(console_main, "GET", "/api/decisions/{decision_id}")(4, user=_user(9, admin=True))
    with pytest.raises(HTTPException) as hidden:
        await _endpoint(console_main, "GET", "/api/decisions/{decision_id}")(2, user=_user(7, admin=False))
    with pytest.raises(HTTPException) as foreign:
        await _endpoint(console_main, "GET", "/api/decisions/{decision_id}")(6, user=_user(9, admin=True))

    assert detail["orphaned"] is True and detail["protected"] is True
    assert hidden.value.status_code == 404
    assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_closes_orphan_with_audited_status_change(monkeypatch):
    db = _registry()
    audit = AsyncMock()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))
    admin = _user(9, admin=True)

    with patch.object(audit_service, "record_event", audit):
        closed = await _endpoint(console_main, "PATCH", "/api/decisions/{decision_id}")(
            2, {"status": "closed", "outcome": "not_achieved"}, user=admin
        )
        reopened = await _endpoint(console_main, "PATCH", "/api/decisions/{decision_id}")(
            2, {"status": "open", "outcome": None}, user=admin
        )

    assert closed["status"] == "closed" and closed["outcome"] == "not_achieved"
    assert closed["closed_at"] is not None
    assert reopened["status"] == "open"
    actions = [call.kwargs["action"] for call in audit.await_args_list]
    assert actions == ["decision.close", "decision.reopen"]
    first = audit.await_args_list[0].kwargs
    assert first["connection"] is db
    assert first["critical"] is True
    assert first["resource_type"] == "decision" and first["resource_id"] == "2"
    assert first["user_id"] == 9
    assert first["metadata"] == {
        "workspace_id": WORKSPACE,
        "from_status": "open",
        "to_status": "closed",
        "from_outcome": None,
        "outcome": "not_achieved",
        "control_room_orphan": True,
    }


@pytest.mark.asyncio
async def test_orphan_cannot_be_edited_or_deleted(monkeypatch):
    db = _registry()
    audit = AsyncMock()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))
    admin = _user(9, admin=True)

    with patch.object(audit_service, "record_event", audit):
        with pytest.raises(HTTPException) as edit:
            await _endpoint(console_main, "PATCH", "/api/decisions/{decision_id}")(
                3, {"status": "closed", "title": "Nuevo"}, user=admin
            )
        with pytest.raises(HTTPException) as delete:
            await _endpoint(console_main, "DELETE", "/api/decisions/{decision_id}")(3, user=admin)

    assert edit.value.status_code == 409
    assert edit.value.detail == orphans.ORPHAN_EDIT_MESSAGE
    assert delete.value.status_code == 409
    assert db.decisions[3]["status"] == "open"
    assert not any(s.startswith(("UPDATE", "DELETE")) for s in db.statements)
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_author_cannot_reach_orphan(monkeypatch):
    db = _registry()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))

    with pytest.raises(HTTPException) as exc:
        await _endpoint(console_main, "PATCH", "/api/decisions/{decision_id}")(
            2, {"status": "closed"}, user=_user(7, admin=False)
        )

    assert exc.value.status_code == 404
    assert db.decisions[2]["status"] == "open"


@pytest.mark.asyncio
async def test_outcome_only_change_is_audited_and_noop_is_not():
    audit = AsyncMock()
    existing = {"id": 5, "workspace_id": WORKSPACE, "status": "closed", "outcome": None}

    with patch.object(audit_service, "record_event", audit):
        await orphans.record_decision_status_change(
            object(), user={"id": 9}, existing=existing, updated=dict(existing)
        )
        await orphans.record_decision_status_change(
            object(),
            user={"id": 9},
            existing=existing,
            updated={**existing, "outcome": "achieved"},
        )

    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == orphans.OUTCOME_AUDIT_ACTION
    assert audit.await_args.kwargs["metadata"]["control_room_orphan"] is False


@pytest.mark.asyncio
async def test_orphan_closing_date_cannot_be_rewritten(monkeypatch):
    db = _registry()
    audit = AsyncMock()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))
    patch_decision = _endpoint(console_main, "PATCH", "/api/decisions/{decision_id}")
    admin = _user(9, admin=True)

    with patch.object(audit_service, "record_event", audit):
        await patch_decision(2, {"status": "closed"}, user=admin)
        closed_at = db.decisions[2]["closed_at"]
        audit.reset_mock()
        for body in (
            {"closed_at": "1999-01-01T00:00:00+00:00"},
            {"status": "closed", "closed_at": "1999-01-01T00:00:00+00:00"},
        ):
            with pytest.raises(HTTPException) as denied:
                await patch_decision(2, body, user=admin)
            assert denied.value.status_code == 409

    assert db.decisions[2]["closed_at"] == closed_at
    audit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    (
        {"status": "aprobada"},
        {"status": None},
        {"status": ["closed"]},
        {"status": "closed", "outcome": "aprobada"},
        {"outcome": {"value": "achieved"}},
    ),
)
@pytest.mark.parametrize(("decision_id", "user_id"), ((2, 9), (5, 7)))
async def test_decision_status_and_outcome_only_take_known_values(
    monkeypatch, body, decision_id, user_id
):
    db = _registry()
    audit = AsyncMock()
    monkeypatch.setattr(console_main, "_dec_pool", AsyncMock(return_value=db))

    with patch.object(audit_service, "record_event", audit):
        with pytest.raises(HTTPException) as rejected:
            await _endpoint(console_main, "PATCH", "/api/decisions/{decision_id}")(
                decision_id, body, user=_user(user_id, admin=user_id == 9)
            )

    assert rejected.value.status_code == 400
    assert db.decisions[decision_id]["status"] == "open"
    assert db.decisions[decision_id]["outcome"] is None
    assert not any(s.startswith("UPDATE") for s in db.statements)
    audit.assert_not_awaited()


@pytest.mark.parametrize(
    ("text", "reserved"),
    (
        (MARKER, True),
        ("  Created from Control Room  ", True),
        ("Aprobación en el Consejo de Acciones: Revisar", True),
        ("Aprobacion en el Consejo de Acciones: Revisar", True),
        ("Seguimiento operativo Control Room: Revisar", True),
        ("Propuesta descartada en el Consejo: Duplicada", True),
        ("Aprobacion de recomendacion OMEGA: Revisar", True),
        ("Decision creada desde Sala de Control por error", False),
        ("Revisé la aprobación en el Consejo de Acciones", False),
        ("Seguimiento con el equipo", False),
    ),
)
def test_reserved_log_texts_match_what_the_server_reads_as_system_records(text, reserved):
    from app.domains.decisions.council_protection import is_reserved_action_text

    assert is_reserved_action_text(text) is reserved


class _Request:
    headers = {"Idempotency-Key": "note-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    (
        MARKER,
        "Aprobación en el Consejo de Acciones: Revisar",
        "Seguimiento operativo Control Room: Revisar",
    ),
)
async def test_log_rejects_system_marker_texts_before_writing(monkeypatch, text):
    db = _registry()
    pool = AsyncMock(return_value=db)
    monkeypatch.setattr(console_main, "_dec_pool", pool)

    with pytest.raises(HTTPException) as rejected:
        await _endpoint(console_main, "POST", "/api/decisions/{decision_id}/actions")(
            5, {"action_text": text}, _Request(), user=_user(7, admin=False)
        )

    assert rejected.value.status_code == 400
    pool.assert_not_awaited()
    assert db.statements == []
