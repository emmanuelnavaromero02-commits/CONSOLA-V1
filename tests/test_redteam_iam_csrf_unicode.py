from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "console"))

from app.domains.accounts.lifecycle import normalize_email_or_400  # noqa: E402
from app.domains.admin import users_scope  # noqa: E402
from app.domains.iam import roles  # noqa: E402
from app.services.permission_roles import ROLE_DEFINITIONS  # noqa: E402

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _assignable(target, actor_role):
    return roles.assignable_role(
        target, {"role": actor_role},
        role_definitions=ROLE_DEFINITIONS, role_admin="admin",
    )


@pytest.mark.parametrize("higher", ["owner", "super_admin"])
def test_admin_cannot_assign_higher_role(higher):
    with pytest.raises(HTTPException) as e:
        _assignable(higher, "admin")
    assert e.value.status_code == 403


def test_legit_assignments_still_work():
    assert _assignable("admin", "admin") == "admin"
    assert _assignable("analyst", "admin") == "analyst"
    assert _assignable("owner", "owner") == "owner"
    assert _assignable("admin", "owner") == "admin"
    assert _assignable("admin", "super_admin") == "admin"
    with pytest.raises(HTTPException):
        _assignable("owner", "super_admin")


def test_iam_admin_workspace_ids_filters_by_role():
    user = {"workspaces": [
        {"workspace_id": "A", "workspace_role": "admin"},
        {"workspace_id": "B", "workspace_role": "viewer"},
    ]}
    assert roles.iam_admin_workspace_ids(user) == {"A"}
    assert roles.session_workspace_ids(user) == {"A", "B"}, "el amplio sigue intacto"


def test_horizontal_leak_closed():
    admin = {"id": 1, "role": "user", "workspaces": [
        {"workspace_id": "A", "workspace_role": "admin"},
        {"workspace_id": "B", "workspace_role": "viewer"},
    ]}
    userB = {"id": 2, "role": "user", "workspaces": [
        {"workspace_id": "B", "workspace_role": "analyst"}]}

    class FakePool:
        async def fetch(self, sql, ids):
            rows = []
            if "A" in ids:
                rows.append({"user_id": 1})
            if "B" in ids:
                rows += [{"user_id": 1}, {"user_id": 2}]
            return rows

    async def pool():
        return FakePool()

    visible = asyncio.run(users_scope.visible_user_ids_for_admin(
        admin, [admin, userB],
        get_db_pool=pool,
        workspace_scope_db_unavailable=lambda e: False,
        is_global_iam_admin=lambda u: False,
        session_workspace_ids=roles.session_workspace_ids,
        admin_workspace_ids=roles.iam_admin_workspace_ids,
    ))
    assert visible == {1}, "el admin (viewer en B) NO debe ver a userB"


def test_manage_target_blocked_across_workspace_where_only_viewer():
    admin = {"id": 1, "role": "user", "workspaces": [
        {"workspace_id": "A", "workspace_role": "admin"},
        {"workspace_id": "B", "workspace_role": "viewer"},
    ]}

    async def get_user(uid):
        return {"id": 2, "role": "user"}

    async def target_ws(uid):
        return {"B"}

    with pytest.raises(HTTPException) as e:
        asyncio.run(users_scope.assert_can_manage_target_user(
            admin, 2,
            auth_get_user_by_id=get_user,
            is_global_iam_admin=lambda u: False,
            session_workspace_ids=roles.session_workspace_ids,
            target_workspace_ids=target_ws,
            admin_workspace_ids=roles.iam_admin_workspace_ids,
        ))
    assert e.value.status_code == 403, "admin-solo-viewer-en-B no puede gestionar en B"


def test_email_rejects_homoglyphs_zerowidth_and_normalizes():
    assert normalize_email_or_400("Admin@Empresa.COM", email_re=EMAIL_RE) == "admin@empresa.com"
    for bad in (
        "аdmin@empresa.com",
        "ad​min@empresa.com",
        "admin@empresa‍.com",
    ):
        with pytest.raises(HTTPException):
            normalize_email_or_400(bad, email_re=EMAIL_RE)


def test_csrf_bearer_exemption_requires_no_session_cookie():
    from app.services import csrf
    src = Path(__file__).resolve().parents[1] / "console/app/services/csrf.py"
    text = src.read_text(encoding="utf-8")
    assert "SESSION_COOKIE_NAME not in request.cookies" in text
    assert csrf.SESSION_COOKIE_NAME == "mod_session"
