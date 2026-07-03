from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.copilot.router_helpers import (
    require_uuid_path,
    tenant_id,
    user_id,
    workspace_id,
)


def test_user_id_accepts_id_or_user_id():
    assert user_id({"id": "7"}) == 7
    assert user_id({"user_id": 8}) == 8


def test_user_id_rejects_missing_or_invalid_session_id():
    with pytest.raises(HTTPException) as missing:
        user_id({})
    assert missing.value.status_code == 401
    assert missing.value.detail == "session has no user id"

    with pytest.raises(HTTPException) as invalid:
        user_id({"id": "not-number"})
    assert invalid.value.status_code == 401
    assert invalid.value.detail == "invalid user id in session"


def test_workspace_and_tenant_prefer_active_scope():
    user = {
        "active_workspace_id": "active-ws",
        "workspace_id": "fallback-ws",
        "active_tenant_id": "active-tenant",
        "tenant_id": "fallback-tenant",
    }

    assert workspace_id(user) == "active-ws"
    assert tenant_id(user) == "active-tenant"
    assert workspace_id({"active_workspace_id": "", "workspace_id": "fallback"}) == "fallback"
    assert tenant_id({"active_tenant_id": "", "tenant_id": "fallback"}) == "fallback"
    assert workspace_id({}) is None
    assert tenant_id({}) is None


def test_require_uuid_path_rejects_bad_values():
    good = "11111111-1111-1111-1111-111111111111"
    assert require_uuid_path(good, label="goal_id") == good

    with pytest.raises(HTTPException) as exc:
        require_uuid_path("not-a-uuid", label="goal_id")

    assert exc.value.status_code == 400
    assert exc.value.detail == "goal_id must be a valid UUID"
