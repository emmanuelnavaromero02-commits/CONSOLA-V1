from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from app.schemas.control_room_experience_actions import (
    ControlRoomExperienceV2Response,
    ExperienceAction,
    ExperienceExceptionV2,
    ExperienceFactV2,
)
from app.services.control_room.business_action_authority_policy import (
    BINDING_TEMPLATE_ORDER,
    DIRECT_ACTION_TEMPLATE_IDS,
    EXECUTABLE_TEMPLATE_IDS,
    decision_contract_digest,
)
from app.services.control_room.business_action_binding_slot import issue_binding_slot
from app.services.control_room.business_action_attempt_policy import BindingIssue
from app.services.control_room.business_action_direct_contract import (
    NO_DECISION_DIGEST,
    DirectMatch,
    classify_direct_action,
    direct_contract_from_persisted_row,
    match_direct_action_item,
    match_reopen_item,
    resolution_digest,
    studio_href,
    token_matches_direct_contract,
    user_can_view_studio,
)
from app.services.control_room.business_action_public_projection import public_action
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_template_contract import template_contract
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
)
from control_room_direct_action_fixtures import (
    STUDIO_ADMIN,
    authorization,
    direct_row,
    exception_item,
    exception_row,
)
from control_room_surface_fixtures import (
    OPERATOR,
    WORKSPACE_ID,
    business_item,
)


APPROVE = ACTION_TEMPLATES["approve_exception"]
PROPOSAL = ACTION_TEMPLATES["create_decision_proposal"]
STUDIO = ACTION_TEMPLATES["open_in_studio"]
REOPEN = ACTION_TEMPLATES["reopen_exception"]


def _match(live, row, template, *, user=OPERATOR, auth=None):
    return match_direct_action_item(
        live, row, auth or authorization(user), template, user=user
    )


def test_direct_templates_are_platform_direct_and_not_executable():
    assert DIRECT_ACTION_TEMPLATE_IDS == {
        "approve_exception",
        "open_in_studio",
        "create_decision_proposal",
        "reopen_exception",
    }
    assert EXECUTABLE_TEMPLATE_IDS == frozenset({"create_followup_task"})
    labels = {
        template_id: ACTION_TEMPLATES[template_id]["label"]
        for template_id in DIRECT_ACTION_TEMPLATE_IDS
    }
    assert labels == {
        "approve_exception": "Aprobar Excepción",
        "open_in_studio": "Ajustar en Estudio",
        "create_decision_proposal": "Crear Propuesta de Decisión",
        "reopen_exception": "Reabrir hallazgo",
    }
    for template_id in DIRECT_ACTION_TEMPLATE_IDS:
        template = ACTION_TEMPLATES[template_id]
        assert template["cartridge_id"] == "platform"
        assert template["requires_approval"] is False
        assert template_contract(template)["template_id"] == template_id
    assert BINDING_TEMPLATE_ORDER[-1] == "create_followup_task"


@pytest.mark.parametrize("template", (APPROVE, PROPOSAL))
def test_open_unlinked_finding_binds_a_template_specific_contract(template):
    item = business_item()
    contract = _match(item, direct_row(item), template)

    assert contract is not None
    assert contract.template_id == template["template_id"]
    assert contract.decision_id is None
    assert contract.decision_digest == NO_DECISION_DIGEST
    assert contract.maker_user_id == 9
    replay = direct_contract_from_persisted_row(
        direct_row(item),
        authorization=authorization(),
        template=template,
        user=OPERATOR,
    )
    assert replay is not None
    assert replay.contract_digest == contract.contract_digest


def test_contracts_differ_per_template_so_handles_never_cross():
    item = business_item()
    approve = _match(item, direct_row(item), APPROVE)
    proposal = _match(item, direct_row(item), PROPOSAL)

    assert approve.binding_digest != proposal.binding_digest
    assert approve.target_digest != proposal.target_digest
    token = {
        "template_id": "approve_exception",
        "item_id": approve.item_id,
        "binding_digest": approve.binding_digest,
        "evidence_digest": approve.evidence_digest,
        "observation_fingerprint": approve.observation_fingerprint,
        "contract_digest": approve.contract_digest,
        "target_digest": approve.target_digest,
        "decision_digest": approve.decision_digest,
        "access_revision_digest": approve.access_revision_digest,
        "rbac_policy_digest": approve.rbac_policy_digest,
    }
    assert token_matches_direct_contract(token, approve)
    assert not token_matches_direct_contract(token, proposal)
    assert not token_matches_direct_contract(
        {**token, "access_revision_digest": "c" * 64}, approve
    )


@pytest.mark.parametrize("template", (APPROVE, PROPOSAL))
@pytest.mark.parametrize(
    "row_updates",
    (
        {"status": "decision_created"},
        {"status": "approved"},
        {"status": "dismissed"},
        {"status": "resolved"},
        {"decision_id": 12, "decision_workspace_id": WORKSPACE_ID},
        {"execution_status": "executed"},
        {"selected_option_id": "option-a"},
        {"metadata_updates": {DECISION_PROVENANCE_KEY: {"decision_id": 1}}},
        {"metadata_updates": {CURRENT_ELIGIBILITY_FINGERPRINT_KEY: "f" * 64}},
        {"metadata_updates": {"evidence_refs": []}},
        {"tenant_id": "cccccccc-cccc-cccc-cccc-cccccccccccc"},
        {"workspace_id": "dddddddd-dddd-dddd-dddd-dddddddddddd"},
        {"item_id": "business-2"},
    ),
)
def test_open_actions_fail_closed_on_linked_terminal_or_drifted_rows(
    template, row_updates
):
    item = business_item()
    updates = dict(row_updates)
    status = updates.get("status")
    live = {**item, "status": status} if status else item
    assert _match(live, direct_row(item, **updates), template) is None


@pytest.mark.parametrize(
    "live_updates",
    (
        {"data_status": "stale"},
        {"status": "in_review"},
        {"decision_id": 4},
        {"owner_user_id": 44},
        {"cartridge": "sap_b1"},
    ),
)
def test_live_drift_never_binds(live_updates):
    item = business_item()
    assert _match({**item, **live_updates}, direct_row(item), APPROVE) is None


def test_authority_is_bound_to_permission_actor_and_owner_scope():
    item = business_item()
    row = direct_row(item)

    assert (
        _match(
            item, row, APPROVE, auth=authorization(permission="control_room.approve")
        )
        is None
    )
    assert (
        _match(item, row, APPROVE, user={**OPERATOR, "id": 10}, auth=authorization())
        is None
    )
    restricted = authorization(workspace_wide=False)
    assert _match(item, row, APPROVE, auth=restricted) is not None
    assert _match(item, {**row, "owner_user_id": 44}, APPROVE, auth=restricted) is None
    assert _match(item, row, ACTION_TEMPLATES["create_followup_task"]) is None


def test_reopen_is_bound_to_the_approval_record_not_the_observation():
    item = exception_item()
    contract = match_reopen_item(
        exception_row(item), authorization(), REOPEN, user=OPERATOR
    )

    assert contract is not None
    assert contract.decision_digest == NO_DECISION_DIGEST
    assert contract.observation_fingerprint == resolution_digest(exception_row(item))
    drifted = exception_row(
        item,
        metadata_updates={
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: "f" * 64,
            "data_status": "stale",
            "evidence_refs": [],
        },
    )
    assert (
        match_reopen_item(drifted, authorization(), REOPEN, user=OPERATOR) is not None
    )
    assert _match(item, exception_row(item), APPROVE) is None
    for row in (
        direct_row(item, status="dismissed"),
        exception_row(item, metadata_updates={"resolution": "false_positive"}),
        exception_row(item, metadata_updates={"resolution_at": ""}),
        exception_row(item, metadata_updates={"resolution_actor_id": None}),
        exception_row(item, decision_id=3, decision_workspace_id=WORKSPACE_ID),
        exception_row(item, selected_option_id="option-a"),
        exception_row(item, status="open"),
        exception_row(item, owner_user_id=44),
        exception_row(item, workspace_id="dddddddd-dddd-dddd-dddd-dddddddddddd"),
    ):
        auth = authorization(workspace_wide=False)
        assert match_reopen_item(row, auth, REOPEN, user=OPERATOR) is None
    assert (
        match_reopen_item(exception_row(item), authorization(), APPROVE, user=OPERATOR)
        is None
    )
    assert (
        match_reopen_item(
            exception_row(item),
            authorization(),
            REOPEN,
            user={**OPERATOR, "id": 10},
        )
        is None
    )


def test_resolution_digest_changes_with_the_approval_record():
    item = exception_item()
    base = resolution_digest(exception_row(item))
    assert base is not None
    for updates in (
        {"resolution_at": "2026-09-25T10:00:01+00:00"},
        {"resolution_actor_id": 10},
        {"resolution_observation_fingerprint": "e" * 64},
    ):
        assert resolution_digest(exception_row(item, metadata_updates=updates)) != base


@pytest.mark.parametrize(
    ("row_factory", "expected"),
    (
        (lambda item: None, DirectMatch.NEEDS_REFRESH),
        (
            lambda item: direct_row(
                item, metadata_updates={CURRENT_ELIGIBILITY_FINGERPRINT_KEY: None}
            ),
            DirectMatch.NEEDS_REFRESH,
        ),
        (
            lambda item: direct_row(item, metadata_updates={"evidence_refs": []}),
            DirectMatch.NEEDS_REFRESH,
        ),
        (lambda item: direct_row(item, status="in_review"), DirectMatch.NEEDS_REFRESH),
        (lambda item: direct_row(item, owner_user_id=44), DirectMatch.INELIGIBLE),
        (
            lambda item: direct_row(
                item, metadata_updates={DECISION_PROVENANCE_KEY: {"decision_id": 1}}
            ),
            DirectMatch.INELIGIBLE,
        ),
        (lambda item: direct_row(item), DirectMatch.MATCH),
    ),
)
def test_unpersisted_or_drifted_rows_ask_for_a_refresh(row_factory, expected):
    item = business_item()
    status, contract = classify_direct_action(
        item,
        row_factory(item),
        authorization(workspace_wide=False),
        APPROVE,
        user=OPERATOR,
    )
    assert status is expected
    assert (contract is not None) is (expected is DirectMatch.MATCH)


def test_stale_live_data_asks_for_a_refresh_and_monitor_alerts_stay_advisory():
    item = business_item(data_status="stale")
    status, _ = classify_direct_action(
        item, direct_row(item), authorization(), APPROVE, user=OPERATOR
    )
    assert status is DirectMatch.NEEDS_REFRESH
    alert = business_item(kind="agent_alert")
    for template in (APPROVE, PROPOSAL):
        status, _ = classify_direct_action(
            alert, direct_row(alert), authorization(), template, user=OPERATOR
        )
        assert status is DirectMatch.INELIGIBLE
    status, _ = classify_direct_action(
        alert, direct_row(alert), authorization(), STUDIO, user=STUDIO_ADMIN
    )
    assert status is DirectMatch.INELIGIBLE


def test_studio_requires_studio_visibility_and_cartridge_scope():
    item = business_item()
    row = direct_row(item)
    contract = _match(item, row, STUDIO, user=STUDIO_ADMIN)

    assert contract is not None
    assert contract.cartridge_id == "sap_hcm"
    assert user_can_view_studio(STUDIO_ADMIN)
    assert not user_can_view_studio(OPERATOR)
    assert _match(item, row, STUDIO) is None
    assert (
        _match(
            item,
            row,
            STUDIO,
            user={**STUDIO_ADMIN, "allowed_cartridges": ["sap_b1"]},
        )
        is None
    )
    for status in ("approved", "dismissed", "resolved"):
        assert (
            _match(
                {**item, "status": status},
                {**row, "status": status},
                STUDIO,
                user=STUDIO_ADMIN,
            )
            is None
        )


def test_studio_binds_the_decision_digest_when_a_decision_exists():
    item = business_item(decision_id=21, status="decision_created")
    row = direct_row(
        item,
        status="decision_created",
        decision_id=21,
        decision_workspace_id=WORKSPACE_ID,
    )
    contract = _match(item, row, STUDIO, user=STUDIO_ADMIN)

    assert contract is not None
    assert contract.decision_digest == decision_contract_digest(WORKSPACE_ID, 21)
    assert contract.decision_digest != NO_DECISION_DIGEST
    assert (
        _match(
            item,
            {**row, "decision_workspace_id": "dddddddd-dddd-dddd-dddd-dddddddddddd"},
            STUDIO,
            user=STUDIO_ADMIN,
        )
        is None
    )


@pytest.mark.parametrize("cartridge", ("sap_hcm", "sap_b1", "a1_b2"))
def test_studio_href_is_a_fixed_gold_tab_route(cartridge):
    assert studio_href(cartridge) == f"/studio?cartridge={cartridge}&tab=capas"


@pytest.mark.parametrize(
    "cartridge", ("", "SAP", "sap-hcm", "../x", "sap_hcm&tab=dags", "x" * 121)
)
def test_studio_href_rejects_injected_cartridges(cartridge):
    with pytest.raises(ValueError):
        studio_href(cartridge)


def test_public_actions_carry_kind_and_exact_server_labels():
    expected = {
        "create_followup_task": ("followup_task", True),
        "approve_exception": ("exception_approval", False),
        "open_in_studio": ("studio_adjustment", False),
        "create_decision_proposal": ("decision_proposal", False),
        "reopen_exception": ("exception_reopen", False),
    }
    for template_id, (kind, approval) in expected.items():
        action = public_action("a" * 64, template_id=template_id)
        assert action.model_dump() == {
            "action_handle": "a" * 64,
            "kind": kind,
            "label": ACTION_TEMPLATES[template_id]["label"],
            "enabled": True,
            "requires_approval": approval,
            "disabled_reason": None,
        }
    assert public_action("a" * 64).kind == "followup_task"


def test_public_action_schema_rejects_unknown_kinds_and_missing_kind():
    valid = public_action("a" * 64, template_id="approve_exception").model_dump()
    for payload in (
        {**valid, "kind": "execute"},
        {key: value for key, value in valid.items() if key != "kind"},
        {**valid, "template_id": "approve_exception"},
        {**valid, "item_id": "business-1"},
    ):
        with pytest.raises(ValidationError):
            ExperienceAction.model_validate(payload)


def test_open_facts_never_expose_reopen_and_exceptions_only_reopen():
    fact = {
        "kind": "anomaly",
        "title": "Hallazgo",
        "severity": "high",
        "observed_at": datetime(2026, 9, 20, tzinfo=UTC),
        "actions": [public_action("a" * 64, template_id="reopen_exception")],
    }
    with pytest.raises(ValidationError):
        ExperienceFactV2.model_validate(fact)
    exception = {
        "title": "Hallazgo",
        "observed_at": datetime(2026, 9, 20, tzinfo=UTC),
        "approved_by_you": True,
    }
    ExperienceExceptionV2.model_validate(
        {
            **exception,
            "actions": [public_action("a" * 64, template_id="reopen_exception")],
        }
    )
    for actions in (
        [public_action("a" * 64, template_id="approve_exception")],
        [
            public_action("a" * 64, template_id="reopen_exception"),
            public_action("b" * 64, template_id="reopen_exception"),
        ],
    ):
        with pytest.raises(ValidationError):
            ExperienceExceptionV2.model_validate({**exception, "actions": actions})
    with pytest.raises(ValidationError):
        ExperienceExceptionV2.model_validate({**exception, "item_id": "business-1"})
    with pytest.raises(ValidationError):
        ControlRoomExperienceV2Response.model_validate(
            {
                "schema_version": "control-room-experience/v2",
                "generated_at": datetime(2026, 9, 20, tzinfo=UTC),
                "exceptions": [exception] * 21,
            }
        )


def test_new_public_models_are_strict_and_free_of_open_types():
    for model in (
        ExperienceExceptionV2,
        ControlRoomExperienceV2Response,
        ExperienceAction,
    ):
        assert model.model_config.get("extra") == "forbid"
        for field in model.model_fields.values():
            nodes = (field.annotation, *get_args(field.annotation))
            assert Any not in nodes
            assert all(get_origin(node) is not dict for node in nodes)


class SlotConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any):
        statement = " ".join(query.split())
        self.calls.append((statement, args))
        if statement.startswith("SELECT"):
            return None
        return {"id": "11111111-1111-1111-1111-111111111111"}


@pytest.mark.asyncio
async def test_binding_slot_template_is_a_parameter_never_a_literal():
    item = business_item()
    contract = _match(item, direct_row(item), APPROVE)
    conn = SlotConn()
    handle, _expires_at = await issue_binding_slot(conn, contract, BindingIssue())

    select, insert = conn.calls
    assert "template_id=$5" in select[0]
    assert select[1][4] == "approve_exception"
    assert "'create_followup_task'" not in select[0] + insert[0]
    assert insert[1][7] == "approve_exception"
    assert len(handle) == 64


@pytest.mark.asyncio
async def test_binding_slot_rejects_unknown_templates():
    item = business_item()
    contract = _match(item, direct_row(item), APPROVE)
    forged = type(
        "Forged",
        (),
        {name: getattr(contract, name) for name in contract.__dataclass_fields__},
    )()
    forged.template_id = "request_owner_review"
    with pytest.raises(RuntimeError):
        await issue_binding_slot(SlotConn(), forged, BindingIssue())
