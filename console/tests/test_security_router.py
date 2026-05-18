import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys
import types

# Stub out auth and asyncpg before importing the router
sys.modules.setdefault('app.dependencies', MagicMock())
sys.modules.setdefault('app.services.auth', MagicMock())

from fastapi import Request
from fastapi.testclient import TestClient
from fastapi import FastAPI
from app.routers import security as security_router
from app.routers.security import router

_ADMIN_USER = {"id": 1, "role": "admin", "email": "admin@example.com"}


def _make_app():
    application = FastAPI()

    @application.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _ADMIN_USER
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
    """Return a side_effect for mock_conn.fetch that handles _columns() queries."""
    async def fetch_side_effect(query, *args):
        if "information_schema.columns" in query:
            # _columns() expects rows with "column_name" key
            table_name = args[0] if args else ""
            if table_name == "user_sessions":
                return [{"column_name": "token"}, {"column_name": "user_id"},
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
    mock_conn.fetchval = AsyncMock(return_value=True)  # _table_exists → True
    mock_conn.fetch.side_effect = _make_fetch_side_effect(
        [{"token": "abc123456789", "user_id": 1, "user_email": "a@b.com",
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
    mock_conn.execute.return_value = "DELETE 1"

    response = client.delete("/security/sessions/abc", headers=_csrf_headers())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch.object(security_router._auth, "pool", new_callable=AsyncMock)
def test_get_audit_events(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetchval = AsyncMock(return_value=True)  # _table_exists → True
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
