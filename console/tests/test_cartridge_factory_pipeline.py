from __future__ import annotations

from app.services import cartridge_factory_pipeline as fp
from app.services import cartridge_intent as ci


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
    s = plan["summary"]
    assert s["entities"] >= 1
    assert s["gold"] >= 1
    assert "owner_email" in s["pii_protected"]
    assert plan["pattern"]["paginated"] is True
    assert plan["pattern"]["incremental"] is True
    assert any(a["name"] == "forecast_at_risk" for a in plan["suggested_analytics"])
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
    assert "highlighted_analytics" in plan


def test_plan_from_intent_no_source():
    plan = fp.plan_from_intent("hola que tal", {"sample": {}})
    assert plan["ok"] is False
    assert "could not identify" in plan["reason"]


def test_plan_blueprint_is_create_full_cartridge_shaped():
    plan = fp.plan_from_descriptor(
        _crm_sample_descriptor(), cartridge_id="hubspot", name="HubSpot", domain="crm"
    )
    bp = plan["blueprint"]
    for key in ("id", "name", "pattern", "category", "bronze_path",
                "entities", "datasets", "kbs", "agents", "semantic_model", "dags"):
        assert key in bp, f"missing {key}"


def test_plan_from_descriptor_accepts_preparsed_live_entities():
    plan = fp.plan_from_descriptor(
        {
            "kind": "openapi",
            "entities": [{
                "name": "deals",
                "fields": [
                    {"name": "deal_id", "type": "string", "primary_key": True},
                    {"name": "amount", "type": "float"},
                    {"name": "hs_lastmodifieddate", "type": "timestamp"},
                ],
                "primary_key": "deal_id",
                "watermark_field": "hs_lastmodifieddate",
            }],
        },
        cartridge_id="hubspot",
        name="HubSpot",
        domain="crm",
    )

    assert plan["ok"] is True, plan.get("validation")
    entity = plan["blueprint"]["entities"][0]
    assert entity["entity"] == "deals"
    assert entity["primary_key"] == "deal_id"
    assert entity["watermark_field"] == "hs_lastmodifieddate"
    assert plan["summary"]["gold"] >= 1


def test_suggest_analytics_does_not_leak_global():
    s = ci.suggest_analytics("crm")
    s[0]["name"] = "HACKED"
    assert ci.suggest_analytics("crm")[0]["name"] != "HACKED"


def test_learn_from_correction_deep_immutable():
    orig = {"learned_sql": {"k": {"sql": "x"}}}
    new = ci.learn_from_correction(orig, "k2", "y")
    new["learned_sql"]["k"]["sql"] = "MUT"
    assert orig["learned_sql"]["k"]["sql"] == "x"
    assert ci.recall_learned_sql(new, "k2") == "y"


def test_plan_from_descriptor_non_dict_guard():
    plan = fp.plan_from_descriptor("nope", cartridge_id="x", name="X")
    assert plan["ok"] is False
    assert plan["reason"] == "descriptor must be an object"


def test_highlight_handles_none_desc():
    intent_module = fp.cartridge_intent
    orig = intent_module._DOMAIN_ANALYTICS.get("crm")
    intent_module._DOMAIN_ANALYTICS["crm"] = [{"name": None, "desc": None}, {"name": "forecast_x", "desc": "forecast"}]
    try:
        plan = fp.plan_from_intent("dame forecast de HubSpot", _crm_sample_descriptor())
        assert any(a["name"] == "forecast_x" for a in plan["highlighted_analytics"])
    finally:
        intent_module._DOMAIN_ANALYTICS["crm"] = orig


def test_sap_successfactors_not_cross_source():
    intent = ci.parse_build_intent("conecta SAP SuccessFactors para ver headcount")
    assert intent["cross_source"] is False


def test_s4hana_alias_detected():
    intent = ci.parse_build_intent("dame los datos de SAP S/4HANA")
    assert intent["primary_source"] is not None
    assert intent["primary_source"]["id"] == "sap_s4hana"


def test_panel_word_boundary():
    intent = ci.parse_build_intent("quiero resultados en espanol")
    assert "dashboard" not in intent["outputs"]


def test_monitor_word_boundary():
    intent = ci.parse_build_intent("enable monitoring for hubspot")
    assert "agent" not in intent["outputs"]


def test_recall_pattern_odata_bearer_returns_oauth2():
    p = ci.recall_pattern("odata", "bearer")
    assert p is not None
    assert p["key"] == "odata:oauth2"


def test_parse_intent_non_string_text_never_raises():
    intent = ci.parse_build_intent(None)
    assert intent["actionable"] is False
    intent2 = ci.parse_build_intent(42)
    assert intent2["actionable"] is False


def test_plan_from_descriptor_pattern_family_normalized():
    plan = fp.plan_from_descriptor(
        _crm_sample_descriptor(), cartridge_id="hubspot", name="HubSpot", domain="crm"
    )
    assert plan["ok"] is True
    assert plan["blueprint"]["pattern"] in {"rest", "odata", "sql", "soap"}


def test_recall_learned_sql_non_dict_memory():
    assert ci.recall_learned_sql(None, "rest:bearer") is None
    assert ci.recall_learned_sql("garbage", "rest:bearer") is None
