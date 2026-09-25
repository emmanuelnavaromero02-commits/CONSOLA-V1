from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.domains.agentops import domain_monitors
from app.domains.agentops.domain_monitor_support import build_contract
from app.domains.agentops.invocation import agent_schedule_due
from app.domains.agentops.sap_b1_monitors import SAP_B1_EXPIRY_MONITOR_SPEC as EXPIRY
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
        "company_margin": {"status": "ready", "breaches": ["La empresa empresa_a cerro 2026-08 con margen de 20.0%."]},
        "data_quality": {"status": "ready", "breaches": ["Calidad de datos en empresa_b: rfc clientes valido al 93.33%."]},
        "group_margin": {"status": "ready", "breaches": []},
    })
    payload = domain_wisdom_bits.build_payload(SPEC, view)
    assert payload["signals"]["count"] == 2 == len(payload["signals"]["items"])
    assert payload["signals"]["items"][0] == {
        "metric": "margen por empresa", "status": "alerta",
        "reason": "La empresa empresa_a cerro 2026-08 con margen de 20.0%.",
    }
    contract = build_contract(SPEC)[2]["monitor"]
    scope = {"tenant_id": "t1", "workspace_id": "w1"}
    assert monitor_alert_policy.monitor_should_alert(contract, {**payload, **scope}) is True

    quiet = domain_wisdom_bits.build_payload(SPEC, _view({"group_margin": {"status": "ready", "breaches": []}}))
    assert quiet["signals"]["count"] == 0
    assert monitor_alert_policy.monitor_should_alert(contract, {**quiet, **scope}) is False


def test_signals_stay_bounded():
    many = _view({"customer_margin": {"status": "ready", "breaches": [f"hallazgo {n}" for n in range(30)]}})
    assert domain_wisdom_bits.build_payload(SPEC, many)["signals"]["count"] == domain_wisdom_bits.MAX_SIGNALS


def test_the_view_survives_the_public_projection(monkeypatch):
    async def result(factory, **fields):
        return factory(status="ready", period="2026-08", **fields)

    monkeypatch.setattr(b1, "query_group_margin", lambda user: result(b1.GroupMargin, consolidated_margin_pct=31.5,
                        breaches=["El margen del grupo de 2026-08 fue 31.5% y cayo 4.0 puntos."]))
    monkeypatch.setattr(b1, "query_company_margin", lambda user: result(b1.CompanyMargin,
                        companies=[{"company": "empresa_a", "revenue": 10.0, "gross_profit": 2.0, "margin_pct": 20.0, "min_margin_pct": 25.0}]))
    monkeypatch.setattr(b1, "query_customer_margin", lambda user, top_n=0: result(b1.CustomerMargin, customers=4))
    monkeypatch.setattr(b1, "query_item_family_margin", lambda user: result(b1.ItemFamilyMargin))
    monkeypatch.setattr(b1, "query_below_min_sales", lambda user: result(b1.BelowMinSales, below_min_pct=12.0))
    monkeypatch.setattr(b1, "query_reconciliation", lambda user: result(b1.Reconciliation, notes=["finanzas todavia no entrego totales de control"]))
    monkeypatch.setattr(b1, "query_data_quality", lambda user: result(b1.DataQuality, checks=24))

    view = asyncio.run(sap_b1_kpis.sap_b1_margin_kpis({"tenant_id": "t", "workspace_id": "w"}, top_n=3))
    projected = ControlRoomSapB1MarginKpisResponse.project(view).model_dump(mode="json")
    metrics = projected["metrics"]
    assert projected["domain"] == "sap_b1_margin" and projected["named_rows"] == 3
    assert metrics["group_margin"]["breaches"] == ["El margen del grupo de 2026-08 fue 31.5% y cayo 4.0 puntos."]
    assert metrics["group_margin"]["proxy_note"].startswith("Margen bruto del grupo")
    assert metrics["company_margin"]["companies"][0]["company"] == "empresa_a"
    assert metrics["reconciliation"]["notes"] == ["finanzas todavia no entrego totales de control"]
    assert metrics["below_min_sales"]["below_min_pct"] == 12.0


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
    assert extra["monitor"]["wisdom_bit_id"] == "WB-B1-CADUCIDAD" and extra["monitor"]["domain"] == "Operacion"
    assert domain_wisdom_bits.VIEW_BY_KEY[EXPIRY.key] == "sap_b1_expiry_kpis"
    assert domain_monitors.spec_for_wisdom_bit("WB-B1-CADUCIDAD") is EXPIRY
    assert len({spec.wisdom_bit_id for spec in domain_monitors.ALL_DOMAIN_MONITOR_SPECS}) == len(domain_monitors.ALL_DOMAIN_MONITOR_SPECS)
    assert agent_schedule_due(extra["schedule"], datetime(2026, 9, 25, 13, 25, tzinfo=timezone.utc), interval_minutes=5, grace_minutes=2)
