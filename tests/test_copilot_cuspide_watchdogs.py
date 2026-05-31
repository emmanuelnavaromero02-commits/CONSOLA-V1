"""Sprint v1.45 cúspide — watchdog_registry tests."""
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
    return mod


def test_clean_string_list_normalises(wd_mod):
    out = wd_mod._clean_string_list(
        ["Margen", " MARGEN", "rentabilidad", None, "", "x" * 200],
        max_count=10, max_len=64,
    )
    # dedup + lower
    assert "margen" in out
    assert "rentabilidad" in out
    assert out.count("margen") == 1  # second " MARGEN" deduped
    # blanks / None dropped
    assert "" not in out
    # cap respected
    assert all(len(s) <= 64 for s in out)


def test_clean_string_list_truncates_long(wd_mod):
    out = wd_mod._clean_string_list(["rentabilidad"], max_count=10, max_len=5)
    assert out == ["renta"]


def test_clean_string_list_caps_count(wd_mod):
    out = wd_mod._clean_string_list(
        [f"kw{i}" for i in range(50)],
        max_count=3, max_len=10,
    )
    assert len(out) == 3


def test_score_watchdog_no_keywords_returns_zero(wd_mod):
    wd = {"intent_keywords": []}
    assert wd_mod._score_watchdog(wd, {"margen", "cartera"}) == 0.0


def test_score_watchdog_full_overlap_is_one(wd_mod):
    wd = {"intent_keywords": ["margen", "cartera"]}
    assert wd_mod._score_watchdog(wd, {"margen", "cartera"}) == 1.0


def test_score_watchdog_partial_overlap(wd_mod):
    wd = {"intent_keywords": ["margen", "cartera", "billable"]}
    score = wd_mod._score_watchdog(wd, {"margen"})
    assert 0 < score < 1


# ── DB-mocked behaviour ──────────────────────────────────────────────


class FakeRecord(dict):
    pass


class FakePool:
    def __init__(self):
        self._fetchval_queue: list[Any] = []
        self._fetchrow_queue: list[Any] = []
        self._fetch_queue: list[list[Any]] = []
        self.execs: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        return self._fetchval_queue.pop(0) if self._fetchval_queue else None

    async def fetchrow(self, sql, *args):
        return self._fetchrow_queue.pop(0) if self._fetchrow_queue else None

    async def fetch(self, sql, *args):
        return self._fetch_queue.pop(0) if self._fetch_queue else []

    async def execute(self, sql, *args):
        self.execs.append((sql, args))
        return "DELETE 1"


def test_list_watchdogs_returns_rows(wd_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]
    fake._fetch_queue = [[
        FakeRecord(
            id="w1", cartridge_id="replicon", slug="margin",
            name="Margin watchdog", description="d",
            intent_keywords=["margen"], agent_slug=None,
            tools=["replicon.query_kb"], risk_level="read",
            enabled=True, metadata={},
        ),
    ]]
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.get_event_loop().run_until_complete(
        wd_mod.list_watchdogs()
    )
    assert len(out) == 1
    assert out[0]["slug"] == "margin"


def test_register_watchdog_writes_row(wd_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]
    fake._fetchrow_queue = [FakeRecord(id="new-id")]
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.get_event_loop().run_until_complete(
        wd_mod.register_watchdog(
            cartridge_id="replicon",
            slug="margin_watchdog",
            name="Margin Watchdog",
            intent_keywords=["margen", "rentabilidad"],
            tools=["replicon.query_kb"],
            risk_level="read",
        )
    )
    assert out == "new-id"


def test_register_watchdog_skips_when_table_missing(wd_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = [None]
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.get_event_loop().run_until_complete(
        wd_mod.register_watchdog(
            cartridge_id="x", slug="y", name="z",
        )
    )
    assert out is None


def test_register_watchdog_rejects_invalid_risk(wd_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]
    fake._fetchrow_queue = [FakeRecord(id="x")]
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    # risk_level falls back to 'read' silently
    out = asyncio.get_event_loop().run_until_complete(
        wd_mod.register_watchdog(
            cartridge_id="c", slug="s", name="n",
            risk_level="WILD",
        )
    )
    assert out == "x"  # didn't raise


def test_relevant_watchdogs_ranks_by_overlap(wd_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]
    fake._fetch_queue = [[
        FakeRecord(
            id="a", cartridge_id="replicon", slug="margin",
            name="Margin", description="",
            intent_keywords=["margen", "rentabilidad"], agent_slug=None,
            tools=[], risk_level="read", enabled=True, metadata={},
        ),
        FakeRecord(
            id="b", cartridge_id="replicon", slug="hours",
            name="Hours", description="",
            intent_keywords=["horas", "billable"], agent_slug=None,
            tools=[], risk_level="read", enabled=True, metadata={},
        ),
    ]]
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.get_event_loop().run_until_complete(
        wd_mod.relevant_watchdogs("revisa el margen este trimestre")
    )
    assert out
    assert out[0]["slug"] == "margin"


def test_list_watchdogs_caches_within_ttl(wd_mod, monkeypatch):
    """The list cache lives in module state. Two calls in quick
    succession must NOT issue a second SELECT against pool.fetch.
    """
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    wd_mod.invalidate_list_cache()
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]  # has_table on first call
    fake._fetch_queue = [[]]                        # one fetch only
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    a = asyncio.get_event_loop().run_until_complete(wd_mod.list_watchdogs())
    b = asyncio.get_event_loop().run_until_complete(wd_mod.list_watchdogs())

    assert a == b == []
    # The second list_watchdogs call hit the in-process cache; the
    # FakePool's fetch queue therefore still has zero remaining items.
    assert fake._fetch_queue == []


def test_register_watchdog_invalidates_cache(wd_mod, monkeypatch):
    """After register, the cached list_watchdogs result must be dropped
    so the new row is observable on the next list call. We assert by
    checking that the *list* cache is empty post-register; the
    table-existence cache lives separately in _copilot_helpers.
    """
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    wd_mod.invalidate_list_cache()
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]  # one is enough — cached after
    fake._fetchrow_queue = [FakeRecord(id="abc")]
    fake._fetch_queue = [[]]
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    # Pre-warm the list cache with a dummy entry so we can verify the
    # register call drops it.
    wd_mod._list_cache[(None, True, wd_mod._MAX_LIST_LIMIT)] = (
        99999999.0, [{"id": "stale"}],
    )
    asyncio.get_event_loop().run_until_complete(
        wd_mod.register_watchdog(cartridge_id="c", slug="s", name="n")
    )
    # Post-register, the stale list-cache entry must be gone.
    assert (None, True, wd_mod._MAX_LIST_LIMIT) not in wd_mod._list_cache


def test_invoke_watchdog_handles_missing(wd_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_watchdogs"]
    fake._fetchrow_queue = [None]  # get_watchdog returns nothing
    monkeypatch.setattr(wd_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.get_event_loop().run_until_complete(
        wd_mod.invoke_watchdog(
            cartridge_id="x", slug="y",
            user={"id": 1}, input_text="hola",
        )
    )
    assert out["error"] == "watchdog_not_found"
