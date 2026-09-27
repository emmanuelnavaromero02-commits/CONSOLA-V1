from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.control_room import business_action_binding_producer as producer
from app.services.control_room import business_direct_action_slots as slots
from app.services.control_room.business_action_binding_slot import (
    binding_handle_from_nonce,
)
from app.services.control_room.business_action_catalog import (
    ENABLED_ACTION_TEMPLATE_IDS_SQL,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_tokens import handle_digest
from control_room_direct_action_fixtures import (
    authorization,
    direct_row,
    exception_item,
    exception_row,
)
from control_room_surface_fixtures import OPERATOR, VIEWER, business_item, snapshot


class SlotStore:
    def __init__(self, templates: set[str], rows: dict[str, dict[str, Any]]) -> None:
        self.templates = templates
        self.rows = rows
        self.slots: dict[tuple[str, str], dict[str, Any]] = {}
        self.statements: list[str] = []
        self.fail_templates: set[str] = set()
        self.concurrent: dict[tuple[str, str], dict[str, Any]] = {}

    @asynccontextmanager
    async def transaction(self):
        yield

    def is_in_transaction(self) -> bool:
        return False

    async def execute(self, query: str, *args: Any) -> str:
        self.statements.append(" ".join(query.split()))
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        statement = " ".join(query.split())
        self.statements.append(statement)
        if query == ENABLED_ACTION_TEMPLATE_IDS_SQL:
            return [
                {
                    "template_id": template_id,
                    "cartridge_id": ACTION_TEMPLATES[template_id]["cartridge_id"],
                    "label": ACTION_TEMPLATES[template_id]["label"],
                    "requires_approval": ACTION_TEMPLATES[template_id][
                        "requires_approval"
                    ],
                }
                for template_id in args[0]
                if template_id in self.templates
            ]
        if "FROM control_room_items AS item" in statement:
            return [self.rows[item_id] for item_id in args[2] if item_id in self.rows]
        if query == slots.EXISTING_SLOTS_SQL:
            return [
                dict(row)
                for (item_id, template_id), row in self.slots.items()
                if item_id in args[3] and template_id in args[4]
            ]
        if query == slots.UPSERT_SLOTS_SQL:
            return self._upsert(args)
        raise AssertionError(f"unexpected fetch: {statement[:80]}")

    def _upsert(self, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        (
            _tenant,
            _workspace,
            _maker,
            access,
            policy,
            issued_at,
            expires_at,
            reuse_after,
            consumed_since,
            digests,
            nonces,
            item_ids,
            template_ids,
            bindings,
            evidences,
            fingerprints,
            contracts,
            targets,
            decisions,
        ) = args
        if set(template_ids) & self.fail_templates:
            raise RuntimeError("slot write failed")
        returned = []
        for index, key in enumerate(zip(item_ids, template_ids)):
            if key in self.concurrent:
                self.slots[key] = self.concurrent.pop(key)
                continue
            existing = self.slots.get(key)
            if existing is not None:
                current = (
                    existing["status"] == "active"
                    and existing["expires_at"] > reuse_after
                    and existing["contract_digest"] == contracts[index]
                )
                if current:
                    continue
            self.slots[key] = {
                "item_id": key[0],
                "template_id": key[1],
                "status": "active",
                "token_digest": digests[index],
                "binding_handle_nonce": nonces[index],
                "binding_digest": bindings[index],
                "evidence_digest": evidences[index],
                "observation_fingerprint": fingerprints[index],
                "contract_digest": contracts[index],
                "target_digest": targets[index],
                "decision_digest": decisions[index],
                "access_revision_digest": access,
                "rbac_policy_digest": policy,
                "intent_id": None,
                "binding_dry_run_action_run_id": None,
                "expires_at": expires_at,
                "consumed_at": None,
            }
            returned.append(
                {
                    "item_id": key[0],
                    "template_id": key[1],
                    "token_digest": digests[index],
                }
            )
        return returned

    def upserts(self) -> list[str]:
        return [
            s
            for s in self.statements
            if s.startswith("INSERT INTO control_room_action_tokens")
        ]


async def _issue(store: SlotStore, items, templates, user=OPERATOR):
    with (
        patch.object(producer.auth, "pool", new=AsyncMock(return_value=store)),
        patch.object(
            producer,
            "capture_authorization_snapshot",
            new=AsyncMock(return_value=authorization(user)),
        ),
    ):
        return await producer.issue_action_bindings(
            user, snapshot(items=tuple(items)), enabled_template_ids=templates
        )


DIRECT = {"approve_exception", "create_decision_proposal", "open_in_studio"}


@pytest.mark.asyncio
async def test_persisted_facts_get_live_handles_and_unpersisted_get_disabled_refresh():
    persisted = business_item("business-1")
    fresh = business_item("business-2")
    store = SlotStore(DIRECT, {"business-1": direct_row(persisted)})

    issued = await _issue(store, (persisted, fresh), DIRECT)

    live = issued["business-1"]
    assert [(a.kind, a.enabled) for a in live] == [
        ("exception_approval", True),
        ("decision_proposal", True),
    ]
    waiting = issued["business-2"]
    assert [(a.kind, a.enabled, a.disabled_reason) for a in waiting] == [
        ("exception_approval", False, "Actualiza los datos antes de continuar."),
        ("decision_proposal", False, "Actualiza los datos antes de continuar."),
    ]
    for action in (*live, *waiting):
        assert len(action.action_handle) == 64
        assert "business" not in action.action_handle
    assert {a.action_handle for a in waiting}.isdisjoint(
        {row["token_digest"].hex() for row in store.slots.values()}
    )
    assert len(store.upserts()) == 2
    assert not any("FOR UPDATE" in s or "pg_advisory" in s for s in store.statements)

    again = await _issue(store, (persisted, fresh), DIRECT)
    assert [a.action_handle for a in again["business-1"]] == [
        a.action_handle for a in live
    ]
    assert [a.action_handle for a in again["business-2"]] == [
        a.action_handle for a in waiting
    ]
    assert len(store.upserts()) == 2


@pytest.mark.asyncio
async def test_slots_close_to_expiry_are_rewritten_in_one_statement():
    items = [business_item(f"business-{index}") for index in range(5)]
    store = SlotStore(
        {"approve_exception"},
        {str(item["id"]): direct_row(item) for item in items},
    )
    first = await _issue(store, items, {"approve_exception"})
    for row in store.slots.values():
        row["expires_at"] = datetime.now(UTC) + timedelta(seconds=30)

    second = await _issue(store, items, {"approve_exception"})

    assert len(store.upserts()) == 2
    for item in items:
        item_id = str(item["id"])
        assert first[item_id][0].action_handle != second[item_id][0].action_handle
        slot = store.slots[(item_id, "approve_exception")]
        assert handle_digest(second[item_id][0].action_handle) == slot["token_digest"]


@pytest.mark.asyncio
async def test_concurrent_writer_wins_and_its_handle_is_reused():
    item = business_item("business-1")
    store = SlotStore({"approve_exception"}, {"business-1": direct_row(item)})
    await _issue(store, (item,), {"approve_exception"})
    winner = dict(store.slots[("business-1", "approve_exception")])
    store.slots.clear()
    nonce = b"\x07" * 32
    winner.update(
        binding_handle_nonce=nonce,
        token_digest=handle_digest(binding_handle_from_nonce(nonce)),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    store.concurrent[("business-1", "approve_exception")] = winner

    issued = await _issue(store, (item,), {"approve_exception"})

    assert issued["business-1"][0].action_handle == binding_handle_from_nonce(nonce)


@pytest.mark.asyncio
async def test_one_template_failure_never_hides_the_others(caplog):
    caplog.set_level(logging.ERROR)
    item = business_item("business-1")
    store = SlotStore(DIRECT, {"business-1": direct_row(item)})
    store.fail_templates = {"create_decision_proposal"}

    issued = await _issue(store, (item,), DIRECT)

    assert [a.kind for a in issued["business-1"]] == ["exception_approval"]
    assert caplog.text.count("control_room_action_binding_operational_failure") == 1


@pytest.mark.asyncio
async def test_readers_and_non_direct_catalogs_issue_nothing():
    item = business_item("business-1")
    store = SlotStore(DIRECT, {"business-1": direct_row(item)})
    assert await _issue(store, (item,), DIRECT, user=VIEWER) == {}
    assert await _issue(store, (item,), {"request_owner_review"}) == {}
    assert store.statements == []


@pytest.mark.asyncio
async def test_disabled_catalog_rows_are_not_issued_even_when_requested():
    item = business_item("business-1")
    store = SlotStore({"approve_exception"}, {"business-1": direct_row(item)})

    issued = await _issue(store, (item,), DIRECT)

    assert [a.kind for a in issued["business-1"]] == ["exception_approval"]


@pytest.mark.asyncio
async def test_reopen_handles_come_from_approval_records_without_the_live_snapshot():
    item = exception_item("business-9")
    store = SlotStore({"reopen_exception"}, {"business-9": exception_row(item)})
    with (
        patch.object(producer.auth, "pool", new=AsyncMock(return_value=store)),
        patch.object(
            producer,
            "capture_authorization_snapshot",
            new=AsyncMock(return_value=authorization()),
        ),
    ):
        issued = await producer.issue_reopen_bindings(
            OPERATOR,
            ["business-9", "missing"],
            enabled_template_ids={"reopen_exception"},
        )
        again = await producer.issue_reopen_bindings(
            OPERATOR, ["business-9"], enabled_template_ids={"reopen_exception"}
        )
        none = await producer.issue_reopen_bindings(
            OPERATOR, ["business-9"], enabled_template_ids=set()
        )
        viewer = await producer.issue_reopen_bindings(
            VIEWER, ["business-9"], enabled_template_ids={"reopen_exception"}
        )

    assert set(issued) == {"business-9"}
    assert issued["business-9"].kind == "exception_reopen"
    assert again["business-9"].action_handle == issued["business-9"].action_handle
    assert none == viewer == {}
    assert len(store.upserts()) == 1


def test_upsert_is_set_based_and_targets_the_binding_slot_index():
    sql = " ".join(slots.UPSERT_SLOTS_SQL.split())
    assert "FROM unnest(" in sql
    assert (
        "ON CONFLICT (tenant_id, workspace_id, subject_user_id, item_id, template_id)"
        " WHERE stage = 'action_binding'"
    ) in sql
    assert "RETURNING item_id, template_id, token_digest" in sql
    assert "FOR UPDATE" not in " ".join(slots.EXISTING_SLOTS_SQL.split())
    assert "create_followup_task" not in sql
