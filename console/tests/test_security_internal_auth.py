from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.security.internal_auth import (
    INTERNAL_SERVICE_EMAIL,
    INTERNAL_SERVICE_ID,
    internal_service_user,
    is_internal_service_actor,
    require_effective_permission,
    user_payload,
)


def test_internal_service_user_shape_uses_admin_role():
    user = internal_service_user(role_admin="admin")

    assert user == {
        "id": INTERNAL_SERVICE_ID,
        "email": INTERNAL_SERVICE_EMAIL,
        "role": "admin",
        "workspace_role": None,
        "active_tenant_id": None,
        "active_workspace_id": None,
    }


def test_is_internal_service_actor_rejects_similar_users():
    assert is_internal_service_actor(None) is False
    assert is_internal_service_actor({"id": INTERNAL_SERVICE_ID, "email": "user@example.com"}) is False
    assert is_internal_service_actor({"id": 99, "email": INTERNAL_SERVICE_EMAIL}) is False


def test_is_internal_service_actor_preserves_legacy_string_id_match():
    assert is_internal_service_actor({"id": "0", "email": INTERNAL_SERVICE_EMAIL}) is True


def test_user_payload_adds_sorted_effective_permissions_without_mutating_input():
    original = {"id": 7, "email": "analyst@example.com", "role": "analyst"}

    def _permissions(payload):
        assert payload is not original
        assert payload["email"] == "analyst@example.com"
        return {"datasets.read", "control_room.read"}

    payload = user_payload(original, get_effective_permissions=_permissions)

    assert payload is not None
    assert payload["permissions"] == ["control_room.read", "datasets.read"]
    assert "permissions" not in original


def test_user_payload_none_stays_none():
    assert user_payload(None, get_effective_permissions=lambda _user: {"x"}) is None


def test_require_effective_permission_raises_consistent_403():
    with pytest.raises(HTTPException) as exc:
        require_effective_permission(
            {"id": 1},
            "monitor.read",
            has_permission=lambda _user, _permission: False,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "permission required: monitor.read"


def test_require_effective_permission_allows_granted_permission():
    require_effective_permission(
        {"id": 1},
        "monitor.read",
        has_permission=lambda _user, permission: permission == "monitor.read",
    )
