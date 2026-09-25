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


_VALID_WORKSPACE_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_record_lesson_binds_workspace_id_as_uuid(lessons_mod, monkeypatch):
    pool = _SqlCapturingPool()
    pool._fetchval_q = ["copilot_lessons", None]
    pool._fetchrow_q = [{"id": "new-lesson"}]
    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=pool))

    out = asyncio.run(lessons_mod.record_lesson(
        user_id=7,
        workspace_id=_VALID_WORKSPACE_UUID,
        trigger_pattern="t",
        lesson_text="l",
    ))
    assert out == "new-lesson"
    sql, args = pool.fetchrow_calls[-1]
    assert "workspace_id, scope" in sql
    assert "$2::uuid" in sql, sql
    assert args[1] == _VALID_WORKSPACE_UUID


def test_record_lesson_rejects_bad_workspace_uuid(lessons_mod, monkeypatch):
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
    raw = """{
        "classification": "diagnosis",
        "plan_summary": "True positive en cartera",
        "intent_keywords": ["cartera"],
        "subgoals": [{"description": "revisar cartera", "expected_cartridges": ["replicon"]}],
        "impact_estimate": {"currency": "MXN", "amount": 0, "direction": "unknown"}
    }"""
    out = goal_mod.parse_diagnosis(raw)
    assert "True positive" in out["plan_summary"]


def test_parse_diagnosis_still_rejects_unparseable(goal_mod):
    raw = """{"subgoals": [garbage"""
    with pytest.raises(ValueError):
        goal_mod.parse_diagnosis(raw)


def test_migration_94_seeds_watchdogs_per_cartridge():
    path = (
        Path(__file__).resolve().parents[1]
        / "infra" / "init" / "94_copilot_watchdog_seed.sql"
    )
    sql = path.read_text(encoding="utf-8")

    for cartridge in ("replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"):
        assert re.search(
            rf"^\s*\('{cartridge}',", sql, re.MULTILINE,
        ), f"no watchdog seeded for cartridge {cartridge!r}"

    assert "ON CONFLICT (cartridge_id, slug) DO UPDATE" in sql

    assert "INSERT INTO schema_migrations" in sql
    assert "94_copilot_watchdog_seed.sql" in sql


def test_migration_94_intent_keywords_match_real_business_terms():
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
