import sys
import types

import pytest
from fastapi import HTTPException

from app.domains.iam.roles import (
    assignable_role,
    is_global_iam_admin,
    session_workspace_ids,
    workspace_scope_db_unavailable,
)


ROLE_DEFINITIONS = {
    "admin": {"assignable": True},
    "tenant_admin": {"assignable": True},
    "viewer": {"assignable": True},
    "hidden": {"assignable": False},
}


def test_global_admin_can_assign_global_or_workspace_roles():
    actor = {"role": "admin"}

    assert (
        assignable_role(
            "tenant_admin",
            actor,
            role_definitions=ROLE_DEFINITIONS,
            role_admin="admin",
        )
        == "tenant_admin"
    )
    assert (
        assignable_role(
            "admin",
            actor,
            role_definitions=ROLE_DEFINITIONS,
            role_admin="admin",
        )
        == "admin"
    )


def test_workspace_admin_cannot_assign_global_roles():
    with pytest.raises(HTTPException) as excinfo:
        assignable_role(
            "admin",
            {"role": "user"},
            role_definitions=ROLE_DEFINITIONS,
            role_admin="admin",
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "global role assignment requires platform admin"


def test_unknown_or_unassignable_roles_fall_back_to_user():
    assert (
        assignable_role(
            "missing",
            {"role": "admin"},
            role_definitions=ROLE_DEFINITIONS,
            role_admin="admin",
        )
        == "user"
    )
    assert (
        assignable_role(
            "hidden",
            {"role": "admin"},
            role_definitions=ROLE_DEFINITIONS,
            role_admin="admin",
        )
        == "user"
    )


def test_global_admin_and_session_workspace_helpers():
    assert is_global_iam_admin({"role": "owner"}, role_admin="admin") is True
    assert is_global_iam_admin({"role": "user"}, role_admin="admin") is False
    assert session_workspace_ids(
        {"workspaces": [{"workspace_id": "w1"}, {"workspace_id": None}, {}]}
    ) == {"w1"}


def test_workspace_scope_db_unavailable_detects_test_stub(monkeypatch):
    assert workspace_scope_db_unavailable(
        RuntimeError("DATABASE_URL is not configured")
    )

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(__name__="stub"))

    assert workspace_scope_db_unavailable(
        AttributeError("module has no attribute create_pool")
    )
    assert not workspace_scope_db_unavailable(ValueError("boom"))

