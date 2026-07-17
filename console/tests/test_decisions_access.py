from app.domains.decisions.access import (
    can_delete_decision,
    can_edit_decision,
    current_workspace_id,
    is_decision_workspace_admin,
)
from app.services.control_room.business_projection import filter_business_decisions


def test_current_workspace_id_prefers_active_workspace():
    assert current_workspace_id({"active_workspace_id": "active", "workspace_id": "fallback"}) == "active"
    assert current_workspace_id({"active_workspace_id": "", "workspace_id": "fallback"}) == "fallback"
    assert current_workspace_id({}) is None


def test_is_decision_workspace_admin_accepts_global_or_workspace_admin_roles():
    assert is_decision_workspace_admin(is_global_admin=True, workspace_role=None) is True
    assert (
        is_decision_workspace_admin(
            is_global_admin=False, workspace_role="tenant_admin"
        )
        is True
    )
    assert (
        is_decision_workspace_admin(is_global_admin=False, workspace_role="viewer")
        is False
    )


def test_can_edit_decision_for_admin_creator_or_assignee():
    row = {"created_by_id": 7, "assignee_id": 8}

    assert can_edit_decision(row, {"id": 99}, is_workspace_admin=True) is True
    assert can_edit_decision(row, {"id": 7}, is_workspace_admin=False) is True
    assert can_edit_decision(row, {"id": 8}, is_workspace_admin=False) is True
    assert can_edit_decision(row, {"id": 9}, is_workspace_admin=False) is False


def test_can_delete_decision_for_admin_or_creator_only():
    row = {"created_by_id": 7, "assignee_id": 8}

    assert can_delete_decision(row, {"id": 99}, is_workspace_admin=True) is True
    assert can_delete_decision(row, {"id": 7}, is_workspace_admin=False) is True
    assert can_delete_decision(row, {"id": 8}, is_workspace_admin=False) is False


def test_historical_decisions_are_filtered_through_linked_business_items():
    decisions = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    linked = [
        {
            "decision_id": 1,
            "item_id": "diagnostic",
            "item_kind": "source_state",
            "source_dataset": "gold_a",
            "metadata": {},
        },
        {
            "decision_id": 2,
            "item_id": "derived",
            "item_kind": "agent_alert",
            "source_dataset": "gold_a",
            "metadata": '{"parent_item_id":"technical-parent"}',
        },
        {
            "decision_id": 3,
            "item_id": "business",
            "item_kind": "anomaly",
            "source_dataset": "gold_a",
            "metadata": {"data_status": "ready"},
        },
    ]
    lineage = [
        {
            "item_id": "technical-parent",
            "item_kind": "source_state",
            "source_dataset": "gold_a",
            "metadata": {"data_status": "missing"},
        }
    ]

    assert filter_business_decisions(
        decisions,
        linked,
        lineage_items=lineage,
    ) == [{"id": 3}, {"id": 4}]
