from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.domains.agentops import domain_monitors
from app.domains.agentops.domain_monitor_support import build_contract
from app.domains.agentops.invocation import agent_schedule_due
from app.domains.agentops.sap_b1_monitors import SAP_B1_EXPIRY_MONITOR_SPEC as EXPIRY
from app.domains.agentops.sap_b1_monitors import SAP_B1_LEARNING_MONITOR_SPEC as LEARNING
from app.domains.agentops.sap_b1_monitors import SAP_B1_MARGIN_MONITOR_SPEC as SPEC
from app.domains.agentops.sap_b1_monitors import SAP_B1_MONITOR_SPECS
from app.schemas.control_room_domain_kpi_responses import ControlRoomSapB1MarginKpisResponse
from app.services import monitor_alert_policy
from app.services.control_room import domain_wisdom_bits, sap_b1_kpis
from app.services.intelligence import sap_b1_aggregates as b1


def test_contract_is_scheduled_in_mexico_city_and_never_writes_back():
    allowed_tools, rag_filter, extra = build_contract(SPEC)
    assert allowed_tools[0] == "mcp-infra__control_room__sap_b1_kpis_read"
    assert "mcp-infra__wisdom_bits__run" in allowed_tools
    assert extra["schedule"] == {**extra["schedule"], "cron": "20 7 * * *", "tz": "America/Mexico_City", "enabled": True}
    monitor = extra["monitor"]
    assert monitor["wisdom_bit_id"] == "WB-B1-MARGEN" and monitor["domain"] == "Finanzas"
    assert monitor["writeback_enabled"] is False and monitor["recommendation_only"] is True
    assert all(engine["enabled"] is False for engine in monitor["engines"])
    assert rag_filter["cartridges"] == ["sap_b1"]


def test_registry_provisions_b1_monitors_at_runtime_without_touching_the_seed():
    assert SPEC in domain_monitors.ALL_DOMAIN_MONITOR_SPECS
    assert SPEC not in domain_monitors.DOMAIN_MONITOR_SPECS
    assert domain_monitors.spec_for_wisdom_bit("wb-b1-margen") is SPEC
    assert domain_monitors.spec_for_slug("sap_b1_margin_monitor") is SPEC
    assert "sap_b1" in domain_monitors.AGENTOPS_MONITOR_CARTRIDGES
    assert domain_wisdom_bits.VIEW_BY_KEY[SPEC.key] == "sap_b1_margin_kpis"
    for spec in domain_monitors.DOMAIN_MONITOR_SPECS:
        assert build_contract(spec)[2]["schedule"]["tz"] == "UTC"


def test_the_monitor_is_due_at_seven_twenty_local_time():
    schedule = build_contract(SPEC)[2]["schedule"]
    local_fire = datetime(2026, 9, 25, 13, 20, tzinfo=timezone.utc)
    assert agent_schedule_due(schedule, local_fire, interval_minutes=5, grace_minutes=2)
    assert not agent_schedule_due(schedule, datetime(2026, 9, 25, 7, 20, tzinfo=timezone.utc), interval_minutes=5, grace_minutes=2)


def _view(metrics: dict) -> dict:
    return {"status": "ready", "generated_at": "2026-09-25T13:20:00+00:00", "metrics": metrics,
            "unavailable_metrics": [], "notes": []}


def test_breaches_become_signals_and_trigger_the_advisory_alert():
    view = _view({
        "destructores": {"status": "ready", "breaches": ["empresa_a: 3 clientes destruyen margen en 2026-08."]},
        "calidad_datos": {"status": "ready", "breaches": ["Calidad de datos en empresa_b: rfc clientes valido al 93.33 %."]},
        "margen_bruto": {"status": "ready", "breaches": []},
    })
    payload = domain_wisdom_bits.build_payload(SPEC, view)
    assert payload["signals"]["count"] == 2 == len(payload["signals"]["items"])
    assert payload["signals"]["items"][0] == {
        "metric": "destructores de margen", "status": "alerta",
        "reason": "empresa_a: 3 clientes destruyen margen en 2026-08.",
    }
    contract = build_contract(SPEC)[2]["monitor"]
    scope = {"tenant_id": "t1", "workspace_id": "w1"}
    assert monitor_alert_policy.monitor_should_alert(contract, {**payload, **scope}) is True

    quiet = domain_wisdom_bits.build_payload(SPEC, _view({"margen_bruto": {"status": "ready", "breaches": []}}))
    assert quiet["signals"]["count"] == 0
    assert monitor_alert_policy.monitor_should_alert(contract, {**quiet, **scope}) is False


def test_signals_stay_bounded():
    many = _view({"destructores": {"status": "ready", "breaches": [f"hallazgo {n}" for n in range(30)]}})
    assert domain_wisdom_bits.build_payload(SPEC, many)["signals"]["count"] == domain_wisdom_bits.MAX_SIGNALS


def test_the_view_survives_the_public_projection(monkeypatch):
    async def result(factory, **fields):
        return factory(status="ready", period="2026-08", **fields)

    monkeypatch.setattr(b1, "query_margen_bruto", lambda user: result(
        b1.MarginTotals, currency="MXN", group={"value": 315.0, "pct": 31.5, "revenue": 1000.0, "commission": 5.0},
        breaches=["El margen bruto del grupo de 2026-08 fue 31.5 % y cayó 4.0 puntos."]))
    monkeypatch.setattr(b1, "query_margen_contribucion", lambda user: result(
        b1.MarginTotals, companies=[{"company": "empresa_a", "value": 2.0, "pct": 20.0, "revenue": 10.0, "commission": 0.5}]))
    monkeypatch.setattr(b1, "query_destructores", lambda user, top_n=0: result(b1.Destroyers, customers=4, margin_lost=12.0))
    monkeypatch.setattr(b1, "query_concentracion_top20", lambda user: result(b1.Concentration))
    monkeypatch.setattr(b1, "query_margen_vendedor", lambda user, top_n=0: result(b1.SellerMargin))
    monkeypatch.setattr(b1, "query_reconciliacion_finanzas", lambda user: result(
        b1.FinanceReconciliation, notes=["Finanzas todavía no carga su corrida manual"]))
    monkeypatch.setattr(b1, "query_calidad_datos", lambda user: result(b1.DataQuality, checks=24))
    monkeypatch.setattr(b1, "query_modelo_entidades", lambda user: result(b1.EntityModel, entities=[
        {"entity": "cliente", "company": "grupo", "records": 40, "identities": 35, "shared_identities": 5,
         "complete_records": 36, "completeness_pct": 90.0, "orphans": 0, "relation_rule": "RFC válido"}]))

    view = asyncio.run(sap_b1_kpis.sap_b1_margin_kpis({"tenant_id": "t", "workspace_id": "w"}, top_n=3))
    projected = ControlRoomSapB1MarginKpisResponse.project(view).model_dump(mode="json")
    metrics = projected["metrics"]
    assert projected["domain"] == "sap_b1_margin" and projected["named_rows"] == 3
    assert metrics["margen_bruto"]["breaches"] == ["El margen bruto del grupo de 2026-08 fue 31.5 % y cayó 4.0 puntos."]
    assert metrics["margen_bruto"]["group"]["pct"] == 31.5 and metrics["margen_bruto"]["proxy_note"].startswith("Venta neta")
    assert metrics["margen_contribucion"]["companies"][0]["company"] == "empresa_a"
    assert metrics["reconciliacion_finanzas"]["notes"] == ["Finanzas todavía no carga su corrida manual"]
    assert metrics["destructores"]["margin_lost"] == 12.0
    assert metrics["modelo_entidades"]["entities"][0]["relation_rule"] == "RFC válido"


def _route():
    from app.routers import intelligence

    return intelligence


def _body(**overrides):
    route = _route()
    return route.InternalEnsureMonitorsRequest(security_context={"signed": True}, cartridge_id="sap_b1", **overrides)


def test_provisioning_route_needs_airflow_and_a_scoped_run_context(monkeypatch):
    route = _route()
    calls = []

    async def fake_ensure(user, spec, **kwargs):
        calls.append((user["tenant_id"], user["workspace_id"], spec.slug))

    from app.domains.agentops import domain_monitor_support

    monkeypatch.setattr(domain_monitor_support, "ensure_domain_monitor", fake_ensure)
    good = {"trusted": True, "tenant_id": "t1", "workspace_id": "w1", "permissions": ["pipelines.run"],
            "allowed_cartridges": ["sap_b1"], "user_id": "airflow:sap_b1_refresh"}
    monkeypatch.setattr(route, "verify_signed_security_context", lambda ctx: dict(good))

    result = asyncio.run(route.intelligence_ensure_monitors_internal(_body(), internal_service="airflow"))
    slugs = [spec.slug for spec in SAP_B1_MONITOR_SPECS]
    assert result == {"ok": True, "monitors": slugs} and len(slugs) == len(set(slugs))
    assert calls == [("t1", "w1", slug) for slug in slugs]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.intelligence_ensure_monitors_internal(_body(), internal_service="mcp-infra"))
    assert exc.value.status_code == 403
    for broken in ({"permissions": ["datasets.read"]}, {"workspace_id": ""}, {"allowed_cartridges": ["hubspot"]}, {"trusted": False}):
        monkeypatch.setattr(route, "verify_signed_security_context", lambda ctx, broken=broken: {**good, **broken})
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.intelligence_ensure_monitors_internal(_body(), internal_service="airflow"))
        assert exc.value.status_code == 403

    def invalid(ctx):
        raise ValueError("bad signature")

    monkeypatch.setattr(route, "verify_signed_security_context", invalid)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.intelligence_ensure_monitors_internal(_body(), internal_service="airflow"))
    assert exc.value.status_code == 403
    assert len(calls) == len(SAP_B1_MONITOR_SPECS)


def test_expiry_monitor_runs_after_the_margin_one_on_its_own_view():
    _allowed, _rag, extra = build_contract(EXPIRY)
    assert extra["schedule"]["cron"] == "25 7 * * *" and extra["schedule"]["tz"] == "America/Mexico_City"
    assert extra["monitor"]["wisdom_bit_id"] == "WB-B1-CADUCIDAD" and extra["monitor"]["domain"] == "Ventas"
    assert domain_wisdom_bits.VIEW_BY_KEY[EXPIRY.key] == "sap_b1_expiry_kpis"
    assert domain_monitors.spec_for_wisdom_bit("WB-B1-CADUCIDAD") is EXPIRY
    assert len({spec.wisdom_bit_id for spec in domain_monitors.ALL_DOMAIN_MONITOR_SPECS}) == len(domain_monitors.ALL_DOMAIN_MONITOR_SPECS)
    assert agent_schedule_due(extra["schedule"], datetime(2026, 9, 25, 13, 25, tzinfo=timezone.utc), interval_minutes=5, grace_minutes=2)


def test_learning_agent_runs_after_the_case_agents_on_its_own_view():
    _allowed, _rag, extra = build_contract(LEARNING)
    assert extra["schedule"]["cron"] == "40 7 * * *" and extra["schedule"]["tz"] == "America/Mexico_City"
    assert extra["monitor"]["wisdom_bit_id"] == "WB-B1-APRENDIZAJE" and extra["monitor"]["domain"] == "Dirección"
    assert extra["monitor"]["writeback_enabled"] is False
    assert domain_wisdom_bits.VIEW_BY_KEY[LEARNING.key] == "sap_b1_learning_kpis"
    assert domain_monitors.spec_for_wisdom_bit("WB-B1-APRENDIZAJE") is LEARNING
    assert [spec.wisdom_bit_id for spec in SAP_B1_MONITOR_SPECS] == [
        "WB-B1-MARGEN", "WB-B1-CADUCIDAD", "WB-B1-ABASTO", "WB-B1-APRENDIZAJE", "WB-B1-SEMAFORO"]
