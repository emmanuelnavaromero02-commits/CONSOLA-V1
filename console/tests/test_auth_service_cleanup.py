from __future__ import annotations

import importlib
import sys
import types

import pytest


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def auth_module(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "test_internal_api_key_with_more_than_32_chars")
    monkeypatch.setitem(sys.modules, "bcrypt", _module())
    monkeypatch.setitem(sys.modules, "asyncpg", _module())
    import app.services as _svc_pkg
    _original = sys.modules.get("app.services.auth")
    _original_pkg_attr = getattr(_svc_pkg, "auth", None)
    sys.modules.pop("app.services.auth", None)
    auth = importlib.import_module("app.services.auth")
    sys.modules["app.services.auth"] = auth
    setattr(_svc_pkg, "auth", auth)
    yield auth
    if _original is not None:
        sys.modules["app.services.auth"] = _original
        setattr(_svc_pkg, "auth", _original)
    else:
        sys.modules.pop("app.services.auth", None)
        if _original_pkg_attr is not None:
            setattr(_svc_pkg, "auth", _original_pkg_attr)


@pytest.mark.anyio
async def test_get_user_by_email_does_not_expose_password_hash(auth_module, monkeypatch):
    class FakePool:
        async def fetchrow(self, query, email):
            return {
                "id": 42,
                "email": email,
                "name": "Test User",
                "password_hash": "hashed-secret",
                "role": "user",
                "is_active": True,
                "must_change_password": False,
            }

    async def fake_pool():
        return FakePool()

    monkeypatch.setattr(auth_module, "pool", fake_pool)

    user = await auth_module.get_user_by_email("USER@EXAMPLE.COM")

    assert user["email"] == "user@example.com"
    assert "password_hash" not in user


@pytest.mark.anyio
async def test_close_pool_closes_and_resets_global_pool(auth_module):
    class FakePool:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    fake_pool = FakePool()
    auth_module._POOL = fake_pool

    await auth_module.close_pool()

    assert fake_pool.closed is True
    assert auth_module._POOL is None


@pytest.mark.anyio
async def test_logout_hashes_both_credentials_in_one_database_call(
    auth_module, monkeypatch
):
    class FakePool:
        def __init__(self):
            self.call = None

        async def fetchrow(self, query, session_hash, refresh_hash):
            self.call = (query, session_hash, refresh_hash)
            return {"session_deleted": True, "refresh_revoked": True}

    fake_pool = FakePool()

    async def pool():
        return fake_pool

    monkeypatch.setattr(auth_module, "pool", pool)
    result = await auth_module.logout_tokens("session-raw", "refresh-raw")

    assert result == (True, True)
    assert fake_pool.call == (
        "SELECT * FROM omega_auth_logout($1, $2)",
        auth_module.hash_session_token("session-raw"),
        auth_module.hash_refresh_token("refresh-raw"),
    )


@pytest.mark.anyio
async def test_delete_user_removes_workspace_memberships_before_user(auth_module, monkeypatch):
    class Ctx:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def __init__(self):
            self.statements = []

        def transaction(self):
            return Ctx(None)

        async def fetchval(self, query, user_id):
            self.statements.append(query)
            if "SELECT EXISTS" in query:
                return True
            return 0

        async def execute(self, query, user_id):
            self.statements.append(query)
            if query == "DELETE FROM users WHERE id = $1":
                return "DELETE 1"
            return "DELETE 0"

    class FakePool:
        def __init__(self, conn):
            self.conn = conn

        def acquire(self):
            return Ctx(self.conn)

    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(auth_module, "pool", fake_pool)

    assert await auth_module.delete_user(123) is True
    assert conn.statements.index("DELETE FROM user_workspace_roles WHERE user_id = $1") < conn.statements.index(
        "DELETE FROM users WHERE id = $1"
    )
    assert "SELECT omega_auth_revoke_user_tokens($1)" in conn.statements


@pytest.mark.anyio
async def test_remaining_service_close_pools_close_and_reset(monkeypatch):
    monkeypatch.setitem(sys.modules, "asyncpg", _module())

    class FakePool:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    modules = [
        ("app.services.tokens", "_POOL"),
        ("app.services.job_service", "_pool"),
        ("app.services.token_store", "_pool"),
        ("app.services.mcp_registry", "_pool"),
        ("app.services.agent_runtime", "_pool"),
    ]

    for module_name, pool_attr in modules:
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
        fake_pool = FakePool()
        setattr(module, pool_attr, fake_pool)

        await module.close_pool()

        assert fake_pool.closed is True
        assert getattr(module, pool_attr) is None
        sys.modules.pop(module_name, None)
