from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.security.internal_auth import (
    INTERNAL_SERVICE_EMAIL,
    INTERNAL_SERVICE_ID,
    internal_cartridge_headers,
    internal_outbound_headers,
    internal_outbound_key,
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


def test_internal_outbound_key_prefers_pair_specific_secret():
    key = internal_outbound_key(
        "VAULT",
        internal_api_key="legacy",
        is_production=True,
        environ={"INTERNAL_API_KEY_CONSOLE_TO_VAULT": "pair-secret"},
    )

    assert key == "pair-secret"


def test_internal_outbound_key_allows_legacy_fallback_outside_production():
    key = internal_outbound_key(
        "REFINEMENT",
        internal_api_key="legacy",
        is_production=False,
        environ={},
    )

    assert key == "legacy"


def test_internal_outbound_key_rejects_legacy_fallback_in_production():
    with pytest.raises(RuntimeError) as exc:
        internal_outbound_key(
            "MCP_INFRA",
            internal_api_key="legacy",
            is_production=True,
            environ={},
        )

    assert (
        str(exc.value)
        == "Missing INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA; legacy fallback disabled in production"
    )


def test_internal_outbound_headers_include_request_id_when_present():
    headers = internal_outbound_headers(
        "VAULT",
        internal_api_key="legacy",
        is_production=False,
        request_id="req-123",
        environ={},
    )

    assert headers == {
        "x-api-key": "legacy",
        "x-internal-service": "console",
        "x-request-id": "req-123",
    }


def test_internal_cartridge_headers_prefer_pair_key():
    headers = internal_cartridge_headers(
        cartridge_api_key="cartridge-key",
        internal_api_key="legacy",
        is_production=True,
    )

    assert headers == {
        "x-api-key": "cartridge-key",
        "x-internal-service": "console",
    }


def test_internal_cartridge_headers_allow_legacy_outside_production():
    headers = internal_cartridge_headers(
        cartridge_api_key=None,
        internal_api_key="legacy",
        is_production=False,
    )

    assert headers == {"x-api-key": "legacy", "x-internal-service": "console"}


def test_internal_cartridge_headers_reject_legacy_in_production():
    with pytest.raises(RuntimeError) as exc:
        internal_cartridge_headers(
            cartridge_api_key=None,
            internal_api_key="legacy",
            is_production=True,
        )

    assert (
        str(exc.value)
        == "Missing INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE; legacy fallback disabled in production"
    )
