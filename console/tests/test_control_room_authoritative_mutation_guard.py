from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    business_observation_fingerprint,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"


def _item(value: int = 3) -> dict:
    item = {
        "id": "business-guard-1",
        "item_id": "business-guard-1",
        "kind": "anomaly",
        "item_kind": "anomaly",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "owner_user_id": 7,
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "source_dataset": "gold_people",
        "metric_type": "scalar",
        "observed_value": value,
        "population_count": 10,
        "observation_date": "2026-07-21",
    }
    return bind_runtime_row_evidence(
        item,
        locator_field="item_id",
        observed_at="2026-07-21T10:00:00Z",
        source_row={"item_id": item["id"], "observed_value": value},
    )


def _persisted(item: dict) -> dict:
    semantic = {
        key: deepcopy(item[key])
        for key in (
            "metric_type",
            "observed_value",
            "population_count",
            "observation_date",
            "evidence_refs",
        )
    }
    semantic.update(
        {
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
            ELIGIBILITY_POLICY_VERSION_KEY: ELIGIBILITY_POLICY_VERSION,
            "source_system": item["source_system"],
        }
    )
    return {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "owner_user_id": 7,
        "item_id": item["id"],
        "cartridge_id": item["cartridge"],
        "source_dataset": item["source_dataset"],
        "item_kind": item["kind"],
        "status": "open",
        "decision_id": None,
        "selected_option_id": None,
        "execution_status": "not_started",
        "metadata": semantic,
    }


class GuardConnection:
    def __init__(self, row: dict) -> None:
        self.row = row
        self.statements: list[str] = []

    async def fetchrow(self, sql: str, *_args):
        self.statements.append(sql)
        return deepcopy(self.row)

    async def execute(self, sql: str, *_args):
        self.statements.append(sql)
        raise AssertionError("mutation ran before authoritative guard")


def _user() -> dict:
    return {
        "id": 7,
        "role": "analyst",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
    }


@pytest.mark.asyncio
async def test_refresh_between_resolve_and_mutation_rejects_before_side_effect() -> (
    None
):
    resolved = _item(3)
    refreshed = _item(4)
    conn = GuardConnection(_persisted(refreshed))
    adapter_calls: list[str] = []

    with pytest.raises(HTTPException) as error:
        await lock_authoritative_business_item(conn, user=_user(), item=resolved)
        adapter_calls.append("called")

    assert error.value.status_code == 409
    assert adapter_calls == []
    assert len(conn.statements) == 1
    assert "FOR UPDATE" in conn.statements[0]
    assert not any(
        keyword in conn.statements[0].upper()
        for keyword in ("INSERT ", "UPDATE ", "DELETE ", "CALL ")
    )


@pytest.mark.asyncio
async def test_matching_authoritative_observation_can_continue() -> None:
    item = _item(3)
    conn = GuardConnection(_persisted(item))

    locked = await lock_authoritative_business_item(conn, user=_user(), item=item)

    assert locked["item_id"] == item["id"]
    assert len(conn.statements) == 1
    assert "FOR UPDATE" in conn.statements[0]


@pytest.mark.asyncio
async def test_item_becoming_source_state_rejects_before_side_effect() -> None:
    item = _item(3)
    row = _persisted(item)
    row["item_kind"] = "source_state"
    row["metadata"] = {**row["metadata"], "data_status": "missing"}
    conn = GuardConnection(row)

    with pytest.raises(HTTPException) as error:
        await lock_authoritative_business_item(conn, user=_user(), item=item)

    assert error.value.status_code == 409
    assert len(conn.statements) == 1


@pytest.mark.asyncio
async def test_decision_linked_after_lookup_rejects_before_side_effect() -> None:
    item = _item(3)
    item["decision_id"] = None
    row = _persisted(item)
    row["decision_id"] = 42
    conn = GuardConnection(row)

    with pytest.raises(HTTPException) as error:
        await lock_authoritative_business_item(conn, user=_user(), item=item)

    assert error.value.status_code == 409
    assert len(conn.statements) == 1


@pytest.mark.asyncio
async def test_terminal_status_changed_after_lookup_rejects() -> None:
    item = {**_item(3), "status": "in_review"}
    row = {**_persisted(item), "status": "dismissed"}
    conn = GuardConnection(row)

    with pytest.raises(HTTPException) as error:
        await lock_authoritative_business_item(conn, user=_user(), item=item)

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_execution_status_changed_after_lookup_rejects() -> None:
    item = {**_item(3), "execution_status": "dry_run_validated"}
    row = {**_persisted(item), "execution_status": "executed"}
    conn = GuardConnection(row)

    with pytest.raises(HTTPException) as error:
        await lock_authoritative_business_item(conn, user=_user(), item=item)

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_unlinked_diagnostic_transition_ignores_projected_option_default() -> (
    None
):
    item = {**_item(3), "selected_option_id": "remediate"}
    row = {
        **_persisted(item),
        "item_kind": "source_state",
        "selected_option_id": None,
        "metadata": {"item_kind": "source_state", "data_status": "missing"},
    }
    conn = GuardConnection(row)

    locked = await lock_authoritative_business_item(
        conn,
        user=_user(),
        item=item,
        allow_diagnostic_transition=True,
    )

    assert locked == row
    assert len(conn.statements) == 1
