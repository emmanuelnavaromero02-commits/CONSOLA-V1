from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def wd_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import watchdog_registry as mod
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    mod.invalidate_list_cache()
    return mod


@pytest.fixture
def lessons_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import lessons_service as mod
    return mod


class _Pool:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_count = 0

    async def fetchval(self, *a, **kw):
        return "copilot_watchdogs"

    async def fetchrow(self, *a, **kw):
        return {"id": "new-id"}

    async def fetch(self, *a, **kw):
        self.fetch_count += 1
        return list(self.rows)

    async def execute(self, *a, **kw):
        return "DELETE 1"


def _row(slug, kw=("margen",), tools=("tool",)):
    return {
        "id": "id-" + slug,
        "cartridge_id": "replicon",
        "slug": slug,
        "name": "Watch " + slug,
        "description": "",
        "intent_keywords": list(kw),
        "agent_slug": None,
        "tools": list(tools),
        "risk_level": "read",
        "enabled": True,
        "metadata": {},
    }


def test_list_watchdogs_does_not_share_cached_list_reference(wd_mod, monkeypatch):
    pool = _Pool(rows=[_row("margin_watchdog"), _row("capacity_watchdog")])
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=pool))

    out1 = asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    out1.clear()
    out1.append({"poisoned": True})
    out2 = asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    assert len(out2) == 2
    assert not any(d.get("poisoned") for d in out2)
    assert pool.fetch_count == 1


def test_list_watchdogs_does_not_share_cached_dict_reference(wd_mod, monkeypatch):
    pool = _Pool(rows=[_row("margin_watchdog")])
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=pool))

    out1 = asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    out1[0]["enabled"] = False
    out2 = asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    assert out2[0]["enabled"] is True


def test_list_watchdogs_cache_key_is_case_insensitive(wd_mod, monkeypatch):
    pool = _Pool(rows=[_row("margin_watchdog")])
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=pool))

    asyncio.run(wd_mod.list_watchdogs(cartridge_id="Replicon"))
    asyncio.run(wd_mod.list_watchdogs(cartridge_id="REPLICON"))
    asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    assert pool.fetch_count == 1


def test_list_watchdogs_cache_caps_at_max_entries(wd_mod, monkeypatch):
    pool = _Pool(rows=[_row("a")])
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=pool))

    cap = wd_mod._LIST_CACHE_MAX_ENTRIES
    for i in range(cap + 5):
        asyncio.run(wd_mod.list_watchdogs(
            cartridge_id="replicon", limit=i + 1,
        ))
    assert len(wd_mod._list_cache) <= cap, len(wd_mod._list_cache)


def test_register_watchdog_invalidates_cache_even_on_failure(wd_mod, monkeypatch):
    pool_ok = _Pool(rows=[_row("a")])
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=pool_ok))
    asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    assert len(wd_mod._list_cache) >= 1

    class _BoomPool:
        async def fetchval(self, *a, **kw): return "copilot_watchdogs"
        async def fetchrow(self, *a, **kw):
            raise RuntimeError("simulated DB error mid-UPSERT")
        async def fetch(self, *a, **kw): return []
        async def execute(self, *a, **kw): return "DELETE 1"

    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=_BoomPool()))

    with pytest.raises(RuntimeError):
        asyncio.run(wd_mod.register_watchdog(
            cartridge_id="replicon",
            slug="margin",
            name="Margin",
            intent_keywords=["x"],
        ))
    assert len(wd_mod._list_cache) == 0


def test_unregister_watchdog_invalidates_cache_even_on_failure(wd_mod, monkeypatch):
    pool_ok = _Pool(rows=[_row("a")])
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=pool_ok))
    asyncio.run(wd_mod.list_watchdogs(cartridge_id="replicon"))
    assert len(wd_mod._list_cache) >= 1

    class _BoomPool:
        async def fetchval(self, *a, **kw): return "copilot_watchdogs"
        async def execute(self, *a, **kw):
            raise RuntimeError("simulated DB error mid-DELETE")

    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=_BoomPool()))

    with pytest.raises(RuntimeError):
        asyncio.run(wd_mod.unregister_watchdog(
            cartridge_id="replicon", slug="margin",
        ))
    assert len(wd_mod._list_cache) == 0


@pytest.mark.parametrize("secret_key", [
    "password", "passwd", "secret", "token", "bearer",
    "api_key", "apikey", "auth", "authorization",
    "credential", "credentials", "private_key", "privatekey",
    "client_secret", "session_id", "sessionid",
])
def test_scrub_args_drops_expanded_secret_keys(lessons_mod, secret_key):
    out = lessons_mod._scrub_args_for_lesson({
        secret_key: "SUPER-SECRET-VALUE",
        "client_id": "ACME",
    })
    assert secret_key not in out, secret_key
    assert "client_id" in out


def test_scrub_args_drops_anything_ending_in_underscore_key(lessons_mod):
    out = lessons_mod._scrub_args_for_lesson({
        "api_key_v2": "SK-XXXX",
        "session_token_secondary": "tok",
        "name": "ok",
    })
    assert "api_key_v2" not in out
    assert "session_token_secondary" not in out
    assert "name" in out


def test_scrub_args_preserves_innocent_keys(lessons_mod):
    out = lessons_mod._scrub_args_for_lesson({
        "project_name": "ACME",
        "client_id": "ACME-001",
        "dag_run_id": "manual__2024-01-01",
        "schedule": "0 0 1 * *",
    })
    assert set(out) == {"project_name", "client_id", "dag_run_id", "schedule"}
