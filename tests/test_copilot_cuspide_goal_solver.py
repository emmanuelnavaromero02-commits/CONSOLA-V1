from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def goal_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import goal_solver as mod
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    return mod


def test_parse_diagnosis_minimum_shape(goal_mod):
    raw = """
    Prefacio sin importancia.
    {
      "classification": "diagnosis",
      "plan_summary": "Cruza Replicon y SAP",
      "intent_keywords": ["margen","cartera"],
      "subgoals": [
        {"description": "Revisar billables Replicon", "expected_cartridges": ["replicon"]}
      ],
      "impact_estimate": {"currency":"MXN", "amount":50000, "direction":"save"}
    }
    """
    out = goal_mod.parse_diagnosis(raw)
    assert out["classification"] == "diagnosis"
    assert out["plan_summary"].startswith("Cruza")
    assert out["intent_keywords"] == ["margen", "cartera"]
    assert len(out["subgoals"]) == 1
    assert out["subgoals"][0]["expected_cartridges"] == ["replicon"]
    assert out["impact_estimate"] == {
        "currency": "MXN", "amount": 50000.0, "direction": "save",
    }


def test_parse_diagnosis_tolerates_trailing_comma(goal_mod):
    raw = """
    {
      "classification": "exploration",
      "plan_summary": "",
      "intent_keywords": ["x",],
      "subgoals": [
        {"description": "A",},
      ],
      "impact_estimate": {},
    }
    """
    out = goal_mod.parse_diagnosis(raw)
    assert out["intent_keywords"] == ["x"]
    assert len(out["subgoals"]) == 1


def test_parse_diagnosis_rejects_no_json(goal_mod):
    with pytest.raises(ValueError):
        goal_mod.parse_diagnosis("solo prosa, ningún JSON")


def test_parse_diagnosis_terminates_on_pathological_input(goal_mod):
    import time
    pathological = "{" * 5000 + "x" * 5000
    start = time.monotonic()
    with pytest.raises(ValueError):
        goal_mod.parse_diagnosis(pathological)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"parse_diagnosis took {elapsed:.2f}s on pathological input"


def test_extract_json_object_handles_nested(goal_mod):
    raw = 'prosa {"a":1,"b":{"c":2}} cola'
    out = goal_mod._extract_json_object(raw)
    assert out == '{"a":1,"b":{"c":2}}'


def test_extract_json_object_handles_strings_with_braces(goal_mod):
    raw = '{"note":"close } brace inside string"}'
    out = goal_mod._extract_json_object(raw)
    assert out == raw


def test_extract_json_object_none_when_unbalanced(goal_mod):
    assert goal_mod._extract_json_object("{{{ no closing") is None
    assert goal_mod._extract_json_object("") is None
    assert goal_mod._extract_json_object("no braces here") is None


def test_parse_diagnosis_rejects_empty_subgoals(goal_mod):
    raw = '{"subgoals": [], "intent_keywords": [], "impact_estimate":{}}'
    with pytest.raises(ValueError):
        goal_mod.parse_diagnosis(raw)


def test_parse_diagnosis_subgoal_cap_at_4(goal_mod):
    sgs = ",".join(
        '{"description":"x' + str(i) + '"}' for i in range(10)
    )
    raw = f'{{"subgoals":[{sgs}], "intent_keywords":[], "impact_estimate":{{}}}}'
    out = goal_mod.parse_diagnosis(raw)
    assert len(out["subgoals"]) == 4


def test_safe_amount_clamps_negatives_and_huge(goal_mod):
    assert goal_mod._safe_amount(-50) == 0.0
    assert goal_mod._safe_amount("not a number") == 0.0
    assert goal_mod._safe_amount(10 ** 14) == 1e12


class FakeRecord(dict):
    pass


class FakePool:
    def __init__(self):
        self._fetchval_queue: list[Any] = []
        self._fetchrow_queue: list[Any] = []
        self.execs: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        return self._fetchval_queue.pop(0) if self._fetchval_queue else None

    async def fetchrow(self, sql, *args):
        return self._fetchrow_queue.pop(0) if self._fetchrow_queue else None

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        self.execs.append((sql, args))
        return "UPDATE 1"


GOAL_ID_VALID = "12345678-1234-1234-1234-1234567890ab"
TENANT_ID_VALID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WORKSPACE_ID_VALID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_create_goal_sets_rls_scope_for_workspace(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals"]
    fake._fetchrow_queue = [FakeRecord(
        id=GOAL_ID_VALID, status="planning", created_at=None,
    )]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.run(
        goal_mod.create_goal(
            user_id=1,
            tenant_id=TENANT_ID_VALID,
            workspace_id=WORKSPACE_ID_VALID,
            goal_text="Diagnostica margen",
        )
    )

    assert out["id"] == GOAL_ID_VALID
    assert fake.execs, "workspace-scoped insert should set RLS scope first"
    assert "set_config('app.tenant_id'" in fake.execs[0][0]
    assert fake.execs[0][1] == (TENANT_ID_VALID, WORKSPACE_ID_VALID)


def test_diagnose_goal_persists_plan(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals", "copilot_goals"]
    fake._fetchrow_queue = [FakeRecord(
        id=GOAL_ID_VALID, user_id=1, workspace_id=None,
        conversation_id=None, goal_text="Mejora el margen",
        plan_summary=None, status="planning",
        impact_estimate={}, outcome_summary=None,
        workflow_ids=[], metadata={},
        created_at=None, finished_at=None,
    )]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    raw_llm = (
        '{"classification":"diagnosis","plan_summary":"Stub plan",'
        '"intent_keywords":["margen"],'
        '"subgoals":[{"description":"x","expected_cartridges":["replicon"]}],'
        '"impact_estimate":{"currency":"MXN","amount":1000,"direction":"save"}}'
    )

    async def fake_llm(system, messages):
        assert "JSON" in system or "json" in system
        return raw_llm

    out = asyncio.run(
        goal_mod.diagnose_goal(goal_id=GOAL_ID_VALID, user_id=1, llm_call=fake_llm)
    )
    assert out["plan_summary"] == "Stub plan"
    assert out["intent_keywords"] == ["margen"]
    assert any("UPDATE copilot_goals" in sql for sql, _ in fake.execs)


def test_get_goal_returns_none_on_invalid_uuid(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals"]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    out = asyncio.run(
        goal_mod.get_goal(goal_id="not-a-uuid", user_id=1)
    )
    assert out is None


def test_update_goal_status_drops_bad_workflow_ids(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals"]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    asyncio.run(
        goal_mod.update_goal_status(
            goal_id=GOAL_ID_VALID,
            user_id=1,
            status="completed",
            workflow_ids=["not-a-uuid", GOAL_ID_VALID],
        )
    )
    assert fake.execs, "UPDATE should have been issued"
    args = fake.execs[0][1]
    safe_ids = args[4]
    assert safe_ids == [GOAL_ID_VALID]


def test_diagnose_goal_raises_on_missing_goal(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals"]
    fake._fetchrow_queue = [None]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    async def fake_llm(system, messages):
        return "{}"

    with pytest.raises(ValueError):
        asyncio.run(
            goal_mod.diagnose_goal(goal_id="missing", user_id=1, llm_call=fake_llm)
        )


def test_conclude_goal_terminal_status_selection(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals", "copilot_goals"]
    fake._fetchrow_queue = [FakeRecord(
        id=GOAL_ID_VALID, user_id=1, workspace_id=None,
        conversation_id=None, goal_text="x",
        plan_summary="p", status="running",
        impact_estimate={}, outcome_summary=None,
        workflow_ids=[], metadata={},
        created_at=None, finished_at=None,
    )]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    async def fake_llm(system, messages):
        return "Resumen ejecutivo del resultado."

    out = asyncio.run(
        goal_mod.conclude_goal(
            goal_id=GOAL_ID_VALID, user_id=1,
            workflow_outcomes=[
                {"id": "w1", "status": "failed", "steps": []},
            ],
            llm_call=fake_llm,
        )
    )
    assert out["status"] == "failed"
    assert "Resumen ejecutivo" in out["outcome_summary"]


def test_conclude_goal_records_lesson_per_approved_step(goal_mod, monkeypatch):
    fake = FakePool()
    fake._fetchval_queue = ["copilot_goals", "copilot_goals"]
    fake._fetchrow_queue = [FakeRecord(
        id=GOAL_ID_VALID, user_id=1, workspace_id=None,
        conversation_id=None, goal_text="x", plan_summary=None,
        status="running", impact_estimate={}, outcome_summary=None,
        workflow_ids=[], metadata={},
        created_at=None, finished_at=None,
    )]
    monkeypatch.setattr(goal_mod.auth, "pool", AsyncMock(return_value=fake))

    recorded: list[dict] = []

    async def fake_record(*, user_id, workspace_id, tool_name, tool_args,
                          workflow_id=None, **kw):
        recorded.append({"tool": tool_name, "args": tool_args})
        return "lesson-id"

    monkeypatch.setattr(
        goal_mod.lessons_service, "record_lesson_from_approval", fake_record,
    )

    async def fake_llm(system, messages):
        return "ok"

    asyncio.run(
        goal_mod.conclude_goal(
            goal_id=GOAL_ID_VALID, user_id=1,
            workflow_outcomes=[{
                "id": "w1", "status": "completed",
                "steps": [
                    {"tool": "replicon.create_dag",
                     "args": {"name": "x"},
                     "status": "completed", "approved": True},
                    {"tool": "replicon.query_kb",
                     "args": {"q": "y"},
                     "status": "completed", "approved": False},
                ],
            }],
            llm_call=fake_llm,
        )
    )
    assert len(recorded) == 1
    assert recorded[0]["tool"] == "replicon.create_dag"
