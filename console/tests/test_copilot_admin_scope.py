from app.domains.copilot import admin_scope
from app.services import permissions


def test_has_admin_accepts_canonical_roles():
    for role in ("owner", "super_admin", "admin", "workspace_admin"):
        assert admin_scope.has_admin({"role": role}) is True


def test_has_admin_rejects_substring_roles():
    assert admin_scope.has_admin({"role": "non_admin_observer"}) is False
    assert admin_scope.has_admin({"role": "administrative_assistant"}) is False


def test_has_admin_rejects_copilot_execute_only(monkeypatch):
    monkeypatch.setattr(
        permissions,
        "get_effective_permissions",
        lambda _user: {"copilot.execute"},
    )

    assert admin_scope.has_admin({"role": "power_user"}) is False


def test_has_admin_accepts_user_management_permission(monkeypatch):
    monkeypatch.setattr(
        permissions,
        "get_effective_permissions",
        lambda _user: {"iam.users.write"},
    )

    assert admin_scope.has_admin({"role": "custom_people_admin"}) is True
