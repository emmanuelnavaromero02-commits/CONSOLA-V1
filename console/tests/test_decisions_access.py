from app.domains.decisions.access import (
    can_delete_decision,
    can_edit_decision,
    current_workspace_id,
    is_decision_workspace_admin,
)


def test_current_workspace_id_prefers_active_workspace():
    assert (
        current_workspace_id(
            {"active_workspace_id": "active", "workspace_id": "fallback"}
        )
        == "active"
    )
    assert (
        current_workspace_id({"active_workspace_id": "", "workspace_id": "fallback"})
        == "fallback"
    )
    assert current_workspace_id({}) is None


def test_is_decision_workspace_admin_accepts_global_or_workspace_admin_roles():
    assert (
        is_decision_workspace_admin(is_global_admin=True, workspace_role=None) is True
    )
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
