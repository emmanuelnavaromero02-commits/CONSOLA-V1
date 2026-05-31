"""Sprint v1.45 cúspide — briefing_v2 tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def briefing_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import briefing_v2 as mod
    return mod


def test_priority_score_critical_outranks_warning(briefing_mod):
    crit = briefing_mod._priority_score(
        {"severity": "critical", "category": "failure"},
    )
    warn = briefing_mod._priority_score(
        {"severity": "warning", "category": "freshness"},
    )
    assert crit > warn


def test_priority_score_clamps_to_range(briefing_mod):
    assert 0 <= briefing_mod._priority_score({"severity": "info"}) <= 100
    assert 0 <= briefing_mod._priority_score({"severity": "critical"}) <= 100


def test_suggest_next_action_freshness(briefing_mod):
    out = briefing_mod._suggest_next_action({
        "category": "freshness", "cartridge": "replicon",
    })
    assert out is not None
    assert out["kind"] == "run_extraction"
    assert "replicon" in out["href"]


def test_suggest_next_action_failure(briefing_mod):
    out = briefing_mod._suggest_next_action({
        "category": "failure", "cartridge": "sap_hcm",
    })
    assert out["kind"] == "open_pipeline"
    assert "sap_hcm" in out["href"]


def test_suggest_next_action_unknown_returns_none(briefing_mod):
    assert briefing_mod._suggest_next_action({"category": "weird"}) is None


def test_briefing_v2_enriches_and_sorts(briefing_mod, monkeypatch):
    sample = [
        {
            "id": "freshness:replicon",
            "severity": "warning",
            "title": "Replicon hace 30h sin extraer",
            "body": "Última extracción exitosa fue hace 30 horas",
            "category": "freshness",
            "cartridge": "replicon",
            "action_label": None,
            "action_href": None,
        },
        {
            "id": "failure:sap_hcm",
            "severity": "critical",
            "title": "3 extracciones SAP fallidas",
            "body": "Las últimas 3 corridas fallaron por timeout",
            "category": "failure",
            "cartridge": "sap_hcm",
            "action_label": None,
            "action_href": None,
        },
    ]

    async def fake_briefing(user_id, *, limit=6):
        return sample

    async def fake_match(intent, *, cartridge_id=None, min_score=0.1, limit=2):
        return []  # no watchdogs registered in this unit test

    monkeypatch.setattr(briefing_mod.proactive_service, "briefing_for_user", fake_briefing)
    monkeypatch.setattr(briefing_mod.watchdog_registry, "relevant_watchdogs", fake_match)

    out = asyncio.get_event_loop().run_until_complete(
        briefing_mod.briefing_v2_for_user(user_id=1, limit=6)
    )
    assert len(out) == 2
    # critical should sort first
    assert out[0]["id"] == "failure:sap_hcm"
    # enrichments present
    assert "priority_score" in out[0]
    assert out[0]["priority_score"] >= out[1]["priority_score"]
    assert out[0]["next_action"]["kind"] == "open_pipeline"
    assert out[0]["watchdogs"] == []
