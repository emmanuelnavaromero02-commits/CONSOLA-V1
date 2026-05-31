"""End-to-end tests for the Level 1→4 cartridge factory orchestrator + intent."""
from __future__ import annotations

from app.services import cartridge_factory_pipeline as fp
from app.services import cartridge_intent as ci


# ── Level 4: intent parsing ──────────────────────────────────────────────────


def test_parse_intent_hubspot_dashboard():
    intent = ci.parse_build_intent("OMEGA, conéctame HubSpot y dame un dashboard de forecast")
    assert intent["primary_source"]["id"] == "hubspot"
    assert intent["primary_source"]["domain"] == "crm"
    assert "dashboard" in intent["outputs"]
    assert "forecast" in intent["outputs"]
    assert intent["actionable"]


def test_parse_intent_cross_source():
    intent = ci.parse_build_intent("cruza los deals de HubSpot con los costos de Replicon")
    ids = {s["id"] for s in intent["sources"]}
    assert {"hubspot", "replicon"} <= ids
    assert intent["cross_source"] is True


def test_parse_intent_unknown_source():
    intent = ci.parse_build_intent("hazme un café")
    assert not intent["actionable"]
    assert intent["primary_source"] is None


# ── Level 3: pattern memory + suggestions ────────────────────────────────────


def test_recall_pattern_odata_oauth2():
    p = ci.recall_pattern("odata", "oauth2")
    assert p and p["key"] == "odata:oauth2"


def test_recall_pattern_fallback():
    p = ci.recall_pattern("rest", "weird_auth")
    assert p and p["key"].startswith("rest:")


def test_suggest_analytics_crm():
    s = ci.suggest_analytics("crm")
    names = {a["name"] for a in s}
    assert "win_rate_by_owner" in names
    assert "forecast_at_risk" in names


def test_learn_from_correction_roundtrip():
    mem = ci.learn_from_correction({}, "rest:bearer", "SELECT 1")
    assert ci.recall_learned_sql(mem, "rest:bearer") == "SELECT 1"


# ── Level 1→4: full orchestrator ─────────────────────────────────────────────


def _crm_sample_descriptor() -> dict:
    return {
        "kind": "rest_sample",
        "auth_type": "bearer",
        "entity_name": "deals",
        "sample": {"results": [{
            "deal_id": "d1",
            "amount": 5000.0,
            "owner_email": "rep@x.com",
            "close_date": "2026-03-01",
            "stage": "open",
        }], "next": "cursor1"},
    }


def test_plan_from_descriptor_full_chain():
    plan = fp.plan_from_descriptor(
        _crm_sample_descriptor(), cartridge_id="hubspot", name="HubSpot", domain="crm"
    )
    assert plan["ok"] is True, plan.get("validation")
    assert plan["source_kind"] == "rest_sample"
    # introspection found the deal entity with money + pii + date
    s = plan["summary"]
    assert s["entities"] >= 1
    assert s["gold"] >= 1
    assert "owner_email" in s["pii_protected"]
    # pattern detection saw pagination + incremental
    assert plan["pattern"]["paginated"] is True
    assert plan["pattern"]["incremental"] is True
    # domain suggestions present
    assert any(a["name"] == "forecast_at_risk" for a in plan["suggested_analytics"])
    # validation passed and self-repair report is all-ok
    assert plan["validation"]["ok"] is True
    assert all(r["ok"] for r in plan["repair_report"])


def test_plan_from_descriptor_empty_source():
    plan = fp.plan_from_descriptor(
        {"kind": "soap", "wsdl": "<broken"}, cartridge_id="x", name="X"
    )
    assert plan["ok"] is False
    assert "no entities" in plan["reason"]


def test_plan_from_intent_end_to_end():
    plan = fp.plan_from_intent(
        "conéctame HubSpot y dame un dashboard de forecast",
        _crm_sample_descriptor(),
    )
    assert plan["ok"] is True
    assert plan["intent"]["primary_source"]["id"] == "hubspot"
    assert plan["summary"]["entities"] >= 1
    # forecast highlighted because the user asked for it
    assert "highlighted_analytics" in plan


def test_plan_from_intent_no_source():
    plan = fp.plan_from_intent("hola que tal", {"sample": {}})
    assert plan["ok"] is False
    assert "could not identify" in plan["reason"]


def test_plan_blueprint_is_create_full_cartridge_shaped():
    """The produced blueprint must carry the keys create_full_cartridge consumes."""
    plan = fp.plan_from_descriptor(
        _crm_sample_descriptor(), cartridge_id="hubspot", name="HubSpot", domain="crm"
    )
    bp = plan["blueprint"]
    for key in ("id", "name", "pattern", "category", "bronze_path",
                "entities", "datasets", "kbs", "agents", "semantic_model", "dags"):
        assert key in bp, f"missing {key}"
