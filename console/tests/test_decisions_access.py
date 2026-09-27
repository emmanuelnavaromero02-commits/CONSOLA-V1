from app.domains.decisions.access import (
    can_delete_decision,
    can_edit_decision,
    can_view_decisions,
    current_workspace_id,
    is_decision_workspace_admin,
    require_decisions_page,
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


def test_decisions_page_opens_to_council_roles_not_only_platform_admins():
    from app import main

    route = next(
        route
        for route in main.app.routes
        if getattr(route, "path", None) == "/decisions"
        and "GET" in (getattr(route, "methods", None) or set())
    )
    guards = {dependency.call for dependency in route.dependant.dependencies}
    assert require_decisions_page in guards
    assert main.require_admin not in guards
    assert can_view_decisions({"id": 1, "role": "admin"}) is True
    assert can_view_decisions(
        {"id": 2, "role": "user", "workspace_role": "control_room_approver"}
    ) is True
    assert can_view_decisions({"id": 3, "role": "tenant_admin"}) is True
    assert can_view_decisions({"id": 4, "role": "analyst"}) is False
