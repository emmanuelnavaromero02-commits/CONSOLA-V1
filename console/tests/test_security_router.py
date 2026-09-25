import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys
import types

sys.modules.setdefault('app.dependencies', MagicMock())
sys.modules.setdefault('app.services.auth', MagicMock())

from fastapi import Request
from fastapi.testclient import TestClient
from fastapi import FastAPI
from app.routers import security as security_router
from app.routers.security import router

_ADMIN_USER = {"id": 1, "role": "admin", "email": "admin@example.com"}


def _make_app(user=None):
    application = FastAPI()
    injected_user = user or _ADMIN_USER

    @application.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = injected_user
        return await call_next(request)

    application.include_router(router)
    return application


app = _make_app()
client = TestClient(app)


def _csrf_headers():
    token = "test-csrf-token"
    client.cookies.set("csrf_token", token)
    return {"X-CSRF-Token": token}


def _make_fetch_side_effect(data_rows):
    async def fetch_side_effect(query, *args):
        if "information_schema.columns" in query:
            table_name = args[0] if args else ""
            if table_name == "user_sessions":
                return [{"column_name": "token_hash"}, {"column_name": "user_id"},
                        {"column_name": "last_seen"}, {"column_name": "user_agent"},
                        {"column_name": "created_at"}, {"column_name": "expires_at"},
                        {"column_name": "ip"}]
            elif table_name == "audit_events":
                return [{"column_name": "id"}, {"column_name": "user_id"},
                        {"column_name": "email"}, {"column_name": "action"},
                        {"column_name": "resource_type"}, {"column_name": "resource_id"},
                        {"column_name": "metadata"}, {"column_name": "ip"},
                        {"column_name": "created_at"}]
            return []
        return data_rows
    return fetch_side_effect


@patch.object(security_router._auth, "pool", new_callable=AsyncMock)
def test_get_sessions(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetchval = AsyncMock(return_value=True)
    mock_conn.fetch.side_effect = _make_fetch_side_effect(
        [{"session_id": "a" * 64, "user_id": 1, "user_email": "a@b.com",
          "ip": None, "last_seen": None, "user_agent": None,
          "created_at": None, "expires_at": None}]
    )

    response = client.get("/security/sessions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == 1
    assert data[0]["user_email"] == "a@b.com"
    assert "***" in data[0]["token_preview"] or "..." in data[0]["token_preview"]


@patch.object(security_router._auth, "pool", new_callable=AsyncMock)
def test_revoke_session(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetchval.return_value = True

    response = client.delete("/security/sessions/" + "a" * 64, headers=_csrf_headers())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch.object(security_router._auth, "pool", new_callable=AsyncMock)
def test_get_audit_events(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetchval = AsyncMock(return_value=True)
    mock_conn.fetch.side_effect = _make_fetch_side_effect(
        [{"id": 1, "user_id": 1, "user_email": "a@b.com", "action": "login",
          "resource_type": None, "resource_id": None,
          "details": '{"ip":"127.0.0.1"}',
          "ip": "127.0.0.1", "created_at": None}]
    )

    response = client.get("/security/audit")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["details"] == {"ip": "127.0.0.1"}


@patch.object(security_router._auth, "pool", new_callable=AsyncMock)
def test_tenant_permissions_payload_is_scoped(mock_pool):
    tenant_user = {
        "id": 2,
        "role": "user",
        "workspace_role": "tenant_admin",
        "email": "tenant-admin@example.com",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
        "workspaces": [{
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "workspace_id": "11111111-1111-1111-1111-111111111111",
            "workspace_role": "tenant_admin",
        }],
    }
    tenant_client = TestClient(_make_app(tenant_user))
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetchval = AsyncMock(return_value=True)

    async def fetch_side_effect(query, *args):
        if "SELECT name, description FROM roles" in query:
            return [
                {"name": "admin", "description": "global"},
                {"name": "tenant_admin", "description": "tenant"},
                {"name": "viewer", "description": "viewer"},
            ]
        return []

    mock_conn.fetch.side_effect = fetch_side_effect

    response = tenant_client.get("/security/permissions")

    assert response.status_code == 200
    body = response.json()
    role_names = {role["name"] for role in body["roles"]}
    permission_keys = {permission["key"] for permission in body["permissions"]}
    assert "tenant_admin" in role_names
    assert "admin" not in role_names
    assert "owner" not in body["matrix"]
    assert "admin" not in body["matrix"]
    assert body["db_roles"] == [{"name": "tenant_admin", "description": "tenant"}, {"name": "viewer", "description": "viewer"}]
    assert "vault.connections.write" in permission_keys
    assert "vault.secrets.read_masked" in permission_keys
    assert "vault.secrets.reveal" not in permission_keys
    assert "pipelines.run" in permission_keys
    assert "pipelines.write" not in permission_keys
    assert "studio.read" not in permission_keys
    assert "settings.read" not in permission_keys


@patch.object(security_router._auth, "pool", new_callable=AsyncMock)
def test_audit_for_a_workspace_admin_never_includes_a_co_member_event_from_another_scope(mock_pool):
    tenant_user = {
        "id": 2,
        "role": "user",
        "workspace_role": "tenant_admin",
        "email": "tenant-admin@example.com",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
        "workspaces": [{"workspace_id": "11111111-1111-1111-1111-111111111111"}],
    }
    captured = []

    async def fetch(query, *args):
        if "information_schema.columns" in query:
            return [{"column_name": name} for name in ("id", "user_id", "email", "action", "metadata", "created_at")]
        if "user_workspace_roles" in query:
            return [{"user_id": 7}]
        captured.append((query, args))
        return []

    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetchval = AsyncMock(return_value=True)
    mock_conn.fetch.side_effect = fetch
    scoped_client = TestClient(_make_app(tenant_user))

    assert scoped_client.get("/security/audit").status_code == 200
    query, args = captured[-1]
    assert "(a.user_id = ANY($3::bigint[]) AND (a.metadata->>'workspace_id' IS NULL OR a.metadata->>'workspace_id' = ANY($1::text[]))" in query
    assert "AND (a.metadata->>'tenant_id' IS NULL OR a.metadata->>'tenant_id' = $2))" in query
    assert args[:3] == (["11111111-1111-1111-1111-111111111111"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", [7])
