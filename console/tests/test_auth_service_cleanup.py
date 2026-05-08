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
    sys.modules.pop("app.services.auth", None)
    auth = importlib.import_module("app.services.auth")
    yield auth
    sys.modules.pop("app.services.auth", None)


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
