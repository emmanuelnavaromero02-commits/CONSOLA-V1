"""Behavioral tests for the cartridge intent parser (Level 4)."""
from __future__ import annotations

from app.services.cartridge_intent import (
    learn_from_correction,
    parse_build_intent,
    recall_learned_sql,
    recall_pattern,
    suggest_analytics,
)


def test_parse_build_intent_salesforce():
    result = parse_build_intent("build a salesforce dashboard")
    assert result["actionable"] is True
    assert result["primary_source"]["id"] == "salesforce"
    assert "dashboard" in result["outputs"]


def test_parse_build_intent_sap_successfactors_family_veto():
    """'SAP SuccessFactors' must count as ONE source (same vendor family)."""
    result = parse_build_intent("sap successfactors employee report")
    assert len(result["sources"]) == 1
    assert result["cross_source"] is False


def test_parse_build_intent_two_different_sources_cross():
    result = parse_build_intent("join hubspot and salesforce")
    assert result["cross_source"] is True
    assert result["intends_cross"] is True


def test_parse_build_intent_cross_source_keyword_in_text():
    """'cross-source' phrase must set intends_cross=True (audit-33)."""
    result = parse_build_intent("cross-source dashboard of hubspot and salesforce")
    assert result["intends_cross"] is True


def test_parse_build_intent_sf_alias_recognized():
    """'sf' is a Salesforce alias; must be actionable (audit-33)."""
    result = parse_build_intent("sf leads pipeline")
    assert result["actionable"] is True
    assert result["primary_source"]["id"] == "salesforce"


def test_parse_build_intent_none_text_returns_inactionable():
    """None text must not raise and must return a safe default (audit-38)."""
    result = parse_build_intent(None)
    assert result["raw"] == ""
    assert result["sources"] == []
    assert result["primary_source"] is None
    assert result["actionable"] is False
    assert result["outputs"] == ["metrics"]


def test_parse_build_intent_outputs_fallback_to_metrics():
    result = parse_build_intent("give me hubspot data")
    assert result["outputs"] == ["metrics"]


def test_parse_build_intent_panel_hint_dashboard():
    result = parse_build_intent("quiero un panel de ventas")
    assert "dashboard" in result["outputs"]


def test_parse_build_intent_monitor_hint_agent():
    result = parse_build_intent("monitor deals in real time")
    assert "agent" in result["outputs"]
    assert result["wants_agent"] is True


def test_recall_pattern_rest_bearer():
    pattern = recall_pattern("rest", "bearer")
    assert pattern is not None
    assert pattern["key"] == "rest:bearer"
    assert pattern["extractor"] == "rest_offset_bearer"


def test_recall_pattern_odata_oauth2():
    pattern = recall_pattern("odata", "oauth2")
    assert pattern is not None
    assert "skiptoken" in pattern["pagination"]


def test_recall_pattern_unknown_kind_returns_none():
    """graphql:bearer is not in _BUILD_PATTERNS — must return None (audit-38)."""
    assert recall_pattern("graphql", "bearer") is None


def test_recall_pattern_none_inputs_use_defaults():
    """None kind/auth must not raise and must return a valid pattern (audit-33)."""
    pattern = recall_pattern(None, None)
    assert pattern is not None  # falls back to rest:bearer


def test_learn_and_recall_correction():
    mem = learn_from_correction({}, "rest:bearer", "SELECT * FROM silver_deals")
    assert recall_learned_sql(mem, "rest:bearer") == "SELECT * FROM silver_deals"
    assert recall_learned_sql(mem, "missing_key") is None


def test_learn_from_correction_does_not_mutate_input():
    """learn_from_correction must return a new dict, not modify the caller's (audit-39)."""
    original = {"learned_sql": {"old": "x"}}
    result = learn_from_correction(original, "new_key", "SELECT 1")
    assert "new_key" not in original.get("learned_sql", {})
    assert result["learned_sql"]["new_key"] == "SELECT 1"


def test_learn_from_correction_empty_sql_stored():
    """Storing an empty string is valid; recall returns '' not None (audit-38)."""
    mem = learn_from_correction({}, "rest:bearer", "")
    assert recall_learned_sql(mem, "rest:bearer") == ""


def test_suggest_analytics_crm():
    hints = suggest_analytics("crm")
    assert isinstance(hints, list)
    assert len(hints) > 0
    assert all("name" in h for h in hints)


def test_suggest_analytics_unknown_domain_returns_empty():
    assert suggest_analytics("unknown_xyz") == []


def test_parse_build_intent_sap_business_one_resolves_to_sap_b1():
    """'SAP B1' must resolve to the Business One cartridge, not to S/4HANA."""
    for text in ("conecta sap b1 y dame un dashboard", "business one ventas", "b1 inventario"):
        result = parse_build_intent(text)
        assert result["primary_source"]["id"] == "sap_b1", text
        assert result["primary_source"]["kind"] == "sql", text
        assert len(result["sources"]) == 1, text
        assert result["cross_source"] is False, text
    assert parse_build_intent("sap ventas")["primary_source"]["id"] == "sap_s4hana"
