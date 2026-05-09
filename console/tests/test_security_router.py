import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys

# Mock dependencies
sys.modules['app.dependencies'] = MagicMock()
sys.modules['app.services.auth'] = MagicMock()

from fastapi.testclient import TestClient
from fastapi import FastAPI
from app.routers.security import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

@patch("app.routers.security._auth.pool", new_callable=AsyncMock)
def test_get_sessions(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetch.return_value = [{"token": "abc", "user_id": 1, "user_email": "a@b.com"}]

    app.dependency_overrides[sys.modules['app.dependencies'].require_admin] = lambda: {"role": "admin"}

    response = client.get("/security/sessions")
    assert response.status_code == 200
    assert response.json() == [{"user_id": 1, "user_email": "a@b.com", "token_preview": "***"}]

@patch("app.routers.security._auth.pool", new_callable=AsyncMock)
def test_revoke_session(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.execute.return_value = "DELETE 1"

    response = client.delete("/security/sessions/abc")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@patch("app.routers.security._auth.pool", new_callable=AsyncMock)
def test_get_audit_events(mock_pool):
    mock_conn = AsyncMock()
    mock_pool.return_value = mock_conn
    mock_conn.fetch.return_value = [{"id": 1, "action": "login", "details": '{"ip":"127.0.0.1"}'}]

    response = client.get("/security/audit")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["details"] == {"ip": "127.0.0.1"}
