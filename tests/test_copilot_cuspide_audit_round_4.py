"""Sprint v1.45 cúspide audit round 4 — production-only regression
guards. These tests pin the round-4 hardening:

* ``lessons_service.record_lesson`` / ``fetch_relevant_lessons`` /
  ``list_lessons`` now cast ``workspace_id`` to ``UUID`` in SQL and
  coerce the binding through ``_coerce_uuid_or_none``. Without these
  casts the queries crash with ``asyncpg.DataError`` in production
  the moment the session carries a real workspace UUID.
* ``goal_solver.parse_diagnosis`` tolerates Python-flavoured
  ``True``/``False``/``None`` literals when (and only when) a strict
  JSON parse has already failed.
* Migration ``94_copilot_watchdog_seed.sql`` ships at least one
  watchdog per priority cartridge so Nivel 4 has something to match.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def lessons_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import lessons_service as mod
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    return mod


@pytest.fixture
def goal_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import goal_solver as mod
    return mod


class _SqlCapturingPool:
    def __init__(self):
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self._fetchval_q: list[Any] = []
        self._fetchrow_q: list[Any] = []
        self._fetch_q: list[Any] = []

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        return self._fetchval_q.pop(0) if self._fetchval_q else None

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self._fetchrow_q.pop(0) if self._fetchrow_q else None

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self._fetch_q.pop(0) if self._fetch_q else []

    async def execute(self, *a, **kw):
        return "UPDATE 1"


# ── workspace_id UUID cast (P0 fix) ─────────────────────────────────


_VALID_WORKSPACE_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_record_lesson_binds_workspace_id_as_uuid(lessons_mod, monkeypatch):
    """The INSERT must use ``$2::uuid`` for the workspace_id slot and
    the bound value must be a coerced UUID, not the raw operator
    string. Otherwise asyncpg surfaces ``invalid input for type uuid``
    at runtime against ``workspaces.id`` (which is UUID)."""
    pool = _SqlCapturingPool()
    pool._fetchval_q = ["copilot_lessons", None]  # table present, no dupe
    pool._fetchrow_q = [{"id": "new-lesson"}]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=pool))

    out = asyncio.run(lessons_mod.record_lesson(
        user_id=7,
        workspace_id=_VALID_WORKSPACE_UUID,
        trigger_pattern="t",
        lesson_text="l",
    ))
    assert out == "new-lesson"
    # Most recent fetchrow is the INSERT.
    sql, args = pool.fetchrow_calls[-1]
    assert "workspace_id, scope" in sql
    assert "$2::uuid" in sql, sql
    # arg[1] is the coerced workspace_id.
    assert args[1] == _VALID_WORKSPACE_UUID


def test_record_lesson_rejects_bad_workspace_uuid(lessons_mod, monkeypatch):
    """A garbage workspace_id (e.g. an int that snuck through, or a
    non-UUID string) is coerced to ``None`` before binding instead of
    being passed raw to asyncpg."""
    pool = _SqlCapturingPool()
    pool._fetchval_q = ["copilot_lessons", None]
    pool._fetchrow_q = [{"id": "x"}]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=pool))

    asyncio.run(lessons_mod.record_lesson(
        user_id=7,
        workspace_id="not-a-uuid",
        trigger_pattern="t",
        lesson_text="l",
    ))
    _sql, args = pool.fetchrow_calls[-1]
    # Position 1 (zero-indexed) is workspace_id_uuid — must be None
    # so the cast lands as NULL::uuid rather than crashing.
    assert args[1] is None


def test_fetch_relevant_lessons_casts_workspace_id(lessons_mod, monkeypatch):
    pool = _SqlCapturingPool()
    pool._fetchval_q = ["copilot_lessons"]
    pool._fetch_q = [[]]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=pool))

    asyncio.run(lessons_mod.fetch_relevant_lessons(
        user_id=7, workspace_id=_VALID_WORKSPACE_UUID,
    ))
    sql, args = pool.fetch_calls[-1]
    assert "$2::uuid" in sql, sql
    assert args[1] == _VALID_WORKSPACE_UUID


def test_list_lessons_casts_workspace_id(lessons_mod, monkeypatch):
    pool = _SqlCapturingPool()
    pool._fetchval_q = ["copilot_lessons"]
    pool._fetch_q = [[]]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=pool))

    asyncio.run(lessons_mod.list_lessons(
        user_id=7, workspace_id=_VALID_WORKSPACE_UUID,
    ))
    sql, args = pool.fetch_calls[-1]
    assert "$2::uuid" in sql, sql
    assert args[1] == _VALID_WORKSPACE_UUID


# ── Python-bool tolerance in parse_diagnosis (P1 fix) ────────────────


def test_parse_diagnosis_tolerates_python_booleans(goal_mod):
    raw = """{
        "classification": "diagnosis",
        "plan_summary": "Stub",
        "intent_keywords": ["margen"],
        "subgoals": [{"description": "x", "expected_cartridges": ["replicon"]}],
        "impact_estimate": {"currency": "MXN", "amount": 100, "direction": "save"},
        "verified": True,
        "alternative": None,
        "skip": False
    }"""
    out = goal_mod.parse_diagnosis(raw)
    assert out["plan_summary"] == "Stub"
    assert len(out["subgoals"]) == 1


def test_parse_diagnosis_does_not_corrupt_string_literal_True(goal_mod):
    """Critical: when the strict JSON parse succeeds, the substitution
    pass must NOT run, so a legitimate ``"True positive"`` string
    inside a well-formed payload survives intact."""
    raw = """{
        "classification": "diagnosis",
        "plan_summary": "True positive en cartera",
        "intent_keywords": ["cartera"],
        "subgoals": [{"description": "revisar cartera", "expected_cartridges": ["replicon"]}],
        "impact_estimate": {"currency": "MXN", "amount": 0, "direction": "unknown"}
    }"""
    out = goal_mod.parse_diagnosis(raw)
    # Capitalisation preserved when the input was already valid JSON.
    assert "True positive" in out["plan_summary"]


def test_parse_diagnosis_still_rejects_unparseable(goal_mod):
    """Even after the bool-tolerance retry, junk must still raise."""
    raw = """{"subgoals": [garbage"""
    with pytest.raises(ValueError):
        goal_mod.parse_diagnosis(raw)


# ── Watchdog seed migration ship at least 1/cartridge ────────────────


def test_migration_94_seeds_watchdogs_per_cartridge():
    """The PR's Nivel 4 (watchdog orchestrator) promise reads as
    decorative if the registry ships empty. Migration 94 must seed
    at least one watchdog for each of the four priority cartridges
    so the matcher has something concrete to return on the very
    first turn."""
    path = (
        Path(__file__).resolve().parents[1]
        / "infra" / "init" / "94_copilot_watchdog_seed.sql"
    )
    sql = path.read_text(encoding="utf-8")

    # Every priority cartridge appears at least once.
    for cartridge in ("replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"):
        assert re.search(
            rf"^\s*\('{cartridge}',", sql, re.MULTILINE,
        ), f"no watchdog seeded for cartridge {cartridge!r}"

    # ON CONFLICT clause present so a re-run is idempotent.
    assert "ON CONFLICT (cartridge_id, slug) DO UPDATE" in sql

    # Self-registers in schema_migrations so the operator can detect
    # whether it ran.
    assert "INSERT INTO schema_migrations" in sql
    assert "94_copilot_watchdog_seed.sql" in sql


def test_migration_94_intent_keywords_match_real_business_terms():
    """Smoke test the seeded keywords actually match the kind of
    Spanish/English business terminology the goal_solver hands over.
    A regression here (someone seeded ``["foo", "bar"]``) would make
    the matcher useless even with the seed."""
    path = (
        Path(__file__).resolve().parents[1]
        / "infra" / "init" / "94_copilot_watchdog_seed.sql"
    )
    sql = path.read_text(encoding="utf-8")
    expected_any = ("margen", "cartera", "capacidad", "payroll",
                    "headcount", "lifecycle")
    assert any(t in sql for t in expected_any), (
        "seed file lost business-vocabulary intent_keywords"
    )
