import asyncio

import pytest
from fastapi import HTTPException

from app.domains.admin.users_scope import (
    list_admin_users_payload,
    set_workspace_role_for_user,
)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self):
        self.statements = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, *args):
        self.statements.append((query, args))
        return "OK"


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, role_id=7):
        self.conn = FakeConnection()
        self.role_id = role_id

    async def fetchval(self, query, role):
        assert "SELECT id FROM roles" in query
        assert role == "tenant_admin"
        return self.role_id

    def acquire(self):
        return FakeAcquire(self.conn)


def test_set_workspace_role_for_user_replaces_existing_role():
    fake_pool = FakePool()

    async def fake_get_db_pool():
        return fake_pool

    asyncio.run(
        set_workspace_role_for_user(
            43,
            "11111111-1111-1111-1111-111111111111",
            "tenant_admin",
            get_db_pool=fake_get_db_pool,
        )
    )

    statements = [query for query, _args in fake_pool.conn.statements]
    assert any("DELETE FROM user_workspace_roles" in query for query in statements)
    assert any("INSERT INTO user_workspace_roles" in query for query in statements)
    assert not any("ON CONFLICT (user_id, workspace_id)" in query for query in statements)


def test_set_workspace_role_for_user_rejects_invalid_role():
    fake_pool = FakePool(role_id=None)

    async def fake_get_db_pool():
        return fake_pool

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            set_workspace_role_for_user(
                43,
                "11111111-1111-1111-1111-111111111111",
                "tenant_admin",
                get_db_pool=fake_get_db_pool,
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid workspace role"


def test_list_admin_users_payload_global_admin_gets_all_users():
    users = [{"id": 1}, {"id": 2}]

    async def auth_list_users(active_only=True):
        assert active_only is False
        return users

    async def visible_user_ids_for_admin(_admin_user, _users):
        raise AssertionError("global admin should not need scoped filtering")

    async def attach_workspace_summaries(items):
        return [{**item, "workspaces": []} for item in items]

    result = asyncio.run(
        list_admin_users_payload(
            admin_user={"id": 1, "role": "admin"},
            auth_list_users=auth_list_users,
            is_global_iam_admin=lambda user: user.get("role") == "admin",
            visible_user_ids_for_admin=visible_user_ids_for_admin,
            attach_workspace_summaries=attach_workspace_summaries,
        )
    )

    assert [user["id"] for user in result["users"]] == [1, 2]
    assert result["users"][0]["workspaces"] == []


def test_list_admin_users_payload_workspace_admin_gets_visible_users_only():
    users = [{"id": 1}, {"id": 2}, {"id": 3}]

    async def auth_list_users(active_only=True):
        assert active_only is False
        return users

    async def visible_user_ids_for_admin(admin_user, listed_users):
        assert admin_user["id"] == 1
        assert listed_users == users
        return {1, 3}

    async def attach_workspace_summaries(items):
        return [{**item, "workspaces": [{"workspace_id": "w1"}]} for item in items]

    result = asyncio.run(
        list_admin_users_payload(
            admin_user={"id": 1, "role": "user"},
            auth_list_users=auth_list_users,
            is_global_iam_admin=lambda _user: False,
            visible_user_ids_for_admin=visible_user_ids_for_admin,
            attach_workspace_summaries=attach_workspace_summaries,
        )
    )

    assert [user["id"] for user in result["users"]] == [1, 3]
    assert all(user["workspaces"] == [{"workspace_id": "w1"}] for user in result["users"])
