from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.domains.agentops import domain_monitors
from app.domains.agentops.domain_monitor_support import build_contract
from app.domains.agentops.invocation import agent_schedule_due
from app.domains.agentops.sap_b1_monitors import SAP_B1_MONITOR_SPECS
from app.domains.agentops.sap_b1_monitors import SAP_B1_SEMAFORO_MONITOR_SPEC as SEMAFORO
from app.domains.agentops.sap_b1_monitors import SAP_B1_SUPPLY_MONITOR_SPEC as SUPPLY
from app.schemas.control_room_domain_kpi_responses import (
    ControlRoomSapB1SemaforoKpisResponse,
    ControlRoomSapB1SupplyKpisResponse,
)
from app.services.control_room import domain_wisdom_bits, sap_b1_digest, sap_b1_kpis
from app.services.intelligence import sap_b1_aggregates as b1

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = "4b0e7d4c-1d34-4d4e-9a55-2f0f3c7c1a10"
EIGHT_LOCAL = datetime(2026, 9, 25, 14, 3, tzinfo=timezone.utc)


def test_supply_and_semaforo_run_after_the_other_monitors_in_mexico_city():
    supply = build_contract(SUPPLY)[2]
    semaforo = build_contract(SEMAFORO)[2]
    assert (supply["schedule"]["cron"], semaforo["schedule"]["cron"]) == ("30 7 * * *", "0 8 * * *")
    assert supply["schedule"]["tz"] == semaforo["schedule"]["tz"] == "America/Mexico_City"
    assert supply["monitor"]["wisdom_bit_id"] == "WB-B1-ABASTO" and semaforo["monitor"]["wisdom_bit_id"] == "WB-B1-SEMAFORO"
    for contract in (supply, semaforo):
        assert contract["monitor"]["writeback_enabled"] is False and contract["monitor"]["recommendation_only"] is True
    assert domain_wisdom_bits.VIEW_BY_KEY[SUPPLY.key] == "sap_b1_supply_kpis"
    assert domain_wisdom_bits.VIEW_BY_KEY[SEMAFORO.key] == "sap_b1_semaforo_kpis"
    assert [spec.slug for spec in SAP_B1_MONITOR_SPECS][-2:] == ["sap_b1_supply_monitor", "sap_b1_semaforo_monitor"]
    assert domain_monitors.spec_for_wisdom_bit("wb-b1-semaforo") is SEMAFORO
    assert agent_schedule_due(semaforo["schedule"], datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc),
                              interval_minutes=5, grace_minutes=2)


def _metric(status="ready", breaches=(), **extra):
    return {"status": status, "breaches": list(breaches), "notes": [], **extra}


def test_semaforo_payload_carries_one_colour_per_area():
    view = {
        "status": "ready",
        "generated_at": "2026-09-25T14:00:00+00:00",
        "unavailable_metrics": ["data_quality"],
        "notes": [],
        "metrics": {
            "group_margin": _metric(period="2026-08"),
            "item_coverage": _metric(breaches=[f"hallazgo {n}" for n in range(7)], as_of="2026-09-25"),
            "batch_expiry": _metric(status="degraded"),
            "data_quality": _metric(status="unavailable", error="sin datos publicados"),
        },
    }
    payload = domain_wisdom_bits.build_payload(SEMAFORO, view)
    areas = {area["metric"]: area for area in payload["areas"]}
    assert {name: area["color"] for name, area in areas.items()} == {
        "group_margin": "verde", "item_coverage": "rojo", "batch_expiry": "amarillo", "data_quality": "sin_datos",
    }
    assert areas["item_coverage"]["findings_total"] == 7 and len(areas["item_coverage"]["findings"]) == 5
    assert areas["data_quality"]["reason"] == "sin datos publicados" and areas["group_margin"]["period"] == "2026-08"
    assert "areas" not in domain_wisdom_bits.build_payload(SUPPLY, view)


def test_supply_and_semaforo_views_survive_the_public_projection(monkeypatch):
    async def result(factory, **fields):
        return factory(status="ready", **fields)

    coverage = lambda user: result(b1.ItemCoverage, as_of="2026-09-25", red=1, suggestions=2,  # noqa: E731
                                   breaches=["empresa_a: 1 articulos se agotan antes de que pueda llegar un pedido nuevo."],
                                   top_risks=[{"company": "empresa_a", "item": "RM-004", "color": "rojo",
                                               "stockout_date": "2026-09-28", "action": "comprar", "suggested_qty": 300.0}])
    monkeypatch.setattr(b1, "query_item_coverage", coverage)
    supply = asyncio.run(sap_b1_kpis.sap_b1_supply_kpis({"tenant_id": "t", "workspace_id": "w"}))
    projected = ControlRoomSapB1SupplyKpisResponse.project(supply).model_dump(mode="json")
    metric = projected["metrics"]["item_coverage"]
    assert metric["top_risks"][0]["item"] == "RM-004" and metric["proxy_note"].startswith("Cobertura por articulo")
    assert metric["breaches"] and projected["domain"] == "sap_b1_supply"

    for name, factory in (("query_group_margin", b1.GroupMargin), ("query_company_margin", b1.CompanyMargin),
                          ("query_distributor_scorecard", b1.DistributorScorecard),
                          ("query_batch_expiry", b1.BatchExpiry), ("query_data_quality", b1.DataQuality)):
        monkeypatch.setattr(b1, name, lambda user, factory=factory: result(factory))
    semaforo = asyncio.run(sap_b1_kpis.sap_b1_semaforo_kpis({"tenant_id": "t", "workspace_id": "w"}))
    projected = ControlRoomSapB1SemaforoKpisResponse.project(semaforo).model_dump(mode="json")
    assert set(projected["metrics"]) == set(sap_b1_kpis.SAP_B1_SEMAFORO_METRICS)
    assert projected["metrics"]["item_coverage"]["top_risks"][0]["stockout_date"] == "2026-09-28"


def test_recipients_are_per_workspace_and_only_valid_addresses():
    raw = (f"{WORKSPACE.upper()}=direccion@example.com, compras@example.com,no-es-correo,direccion@example.com;"
           "00000000-0000-0000-0000-000000000000=otro@example.com")
    assert sap_b1_digest.recipients_for(WORKSPACE, raw) == ["direccion@example.com", "compras@example.com"]
    assert sap_b1_digest.recipients_for("11111111-1111-1111-1111-111111111111", raw) == []
    assert sap_b1_digest.recipients_for(None, raw) == []
    many = WORKSPACE + "=" + ",".join(f"u{n}@example.com" for n in range(40))
    assert len(sap_b1_digest.recipients_for(WORKSPACE, many)) == sap_b1_digest.MAX_RECIPIENTS


def test_the_digest_only_goes_out_in_the_scheduled_hour_local_time():
    assert sap_b1_digest.in_schedule_window(SEMAFORO, EIGHT_LOCAL)
    assert not sap_b1_digest.in_schedule_window(SEMAFORO, datetime(2026, 9, 25, 8, 3, tzinfo=timezone.utc))
    assert not sap_b1_digest.in_schedule_window(SEMAFORO, datetime(2026, 9, 25, 15, 1, tzinfo=timezone.utc))


def _semaforo_payload(**extra):
    return {
        "wisdom_bit_id": "WB-B1-SEMAFORO",
        "evidence_handle": "evh_1",
        "areas": [
            {"metric": "group_margin", "label": "margen del grupo", "color": "verde", "period": "2026-08",
             "findings": [], "findings_total": 0, "reason": None},
            {"metric": "item_coverage", "label": "cobertura y reabasto", "color": "rojo", "period": "2026-09-25",
             "findings": ["<script>alert(1)</script> se agota"], "findings_total": 3, "reason": None},
        ],
        **extra,
    }


def test_render_puts_red_first_and_escapes_every_finding():
    subject, body, text = sap_b1_digest.render(_semaforo_payload(), date(2026, 9, 25))
    assert subject == "Semaforo SAP Business One 2026-09-25: 1 area en rojo"
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert body.index("cobertura y reabasto") < body.index("margen del grupo")
    assert "y 2 mas en la consola" in body and "[Rojo] cobertura y reabasto" in text


class _Conn:
    def __init__(self, ledger: dict):
        self.ledger = ledger

    async def fetchrow(self, sql, tenant, workspace, local_date, recipients, run_id, stale):
        assert "ON CONFLICT (tenant_id, workspace_id, local_date)" in sql
        key = (tenant, workspace, local_date)
        if key in self.ledger and self.ledger[key]["status"] != "failed":
            return None
        self.ledger[key] = {"status": "sending", "recipients": recipients}
        return {"local_date": local_date}

    async def execute(self, sql, tenant, workspace, local_date, delivered):
        self.ledger[(tenant, workspace, local_date)].update(status="sent" if delivered else "failed", delivered=delivered)


def _install(monkeypatch, ledger):
    @asynccontextmanager
    async def scoped(pool, user):
        yield _Conn(ledger), user["tenant_id"], user["workspace_id"]

    monkeypatch.setattr(sap_b1_digest, "scoped_db_for_user", scoped)
    monkeypatch.setenv(sap_b1_digest.RECIPIENTS_ENV, f"{WORKSPACE}=direccion@example.com,compras@example.com")


def test_the_digest_is_sent_once_per_day_and_retried_only_after_a_failed_delivery(monkeypatch):
    ledger: dict = {}
    _install(monkeypatch, ledger)
    user = {"tenant_id": "t1", "workspace_id": WORKSPACE, "agent_run_id": "run-1"}
    sent: list[tuple[str, str]] = []

    async def send(to, subject, html, text=None):
        sent.append((to, subject))
        return True

    first = asyncio.run(sap_b1_digest.maybe_send_digest(user, _semaforo_payload(), pool=None, now=EIGHT_LOCAL, send=send))
    assert first == {"sent": True, "recipients": 2, "delivered": 2, "local_date": "2026-09-25"}
    again = asyncio.run(sap_b1_digest.maybe_send_digest(user, _semaforo_payload(), pool=None, now=EIGHT_LOCAL, send=send))
    assert again == {"sent": False, "reason": "already_sent"} and len(sent) == 2

    async def down(to, subject, html, text=None):
        return False

    ledger.clear()
    failed = asyncio.run(sap_b1_digest.maybe_send_digest(user, _semaforo_payload(), pool=None, now=EIGHT_LOCAL, send=down))
    assert failed["sent"] is False and ledger[("t1", WORKSPACE, date(2026, 9, 25))]["status"] == "failed"
    retried = asyncio.run(sap_b1_digest.maybe_send_digest(user, _semaforo_payload(), pool=None, now=EIGHT_LOCAL, send=send))
    assert retried["delivered"] == 2


@pytest.mark.parametrize(
    "payload, now, reason",
    [
        (_semaforo_payload(evidence_handle=None), EIGHT_LOCAL, "unverified_run"),
        (_semaforo_payload(wisdom_bit_id="WB-B1-ABASTO"), EIGHT_LOCAL, "not_semaforo"),
        (_semaforo_payload(), datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc), "outside_schedule"),
    ],
)
def test_the_digest_never_leaves_for_manual_runs_or_off_schedule(monkeypatch, payload, now, reason):
    _install(monkeypatch, {})

    async def send(*args, **kwargs):
        raise AssertionError("must not send")

    user = {"tenant_id": "t1", "workspace_id": WORKSPACE}
    assert asyncio.run(sap_b1_digest.maybe_send_digest(user, payload, pool=None, now=now, send=send)) == {
        "sent": False, "reason": reason,
    }


def test_the_route_only_hooks_the_digest_after_verified_evidence(monkeypatch):
    import importlib

    from app.routers import intelligence

    digest_module = importlib.import_module("app.services.control_room.sap_b1_digest")
    calls = []

    async def fake_digest(user, payload, *, pool):
        calls.append(payload["evidence_handle"])
        return {"sent": True, "recipients": 1, "delivered": 1}

    async def fake_pool():
        return object()

    monkeypatch.setattr(digest_module, "maybe_send_digest", fake_digest)
    monkeypatch.setattr(intelligence.auth, "pool", fake_pool)
    user = {"tenant_id": "t1", "workspace_id": WORKSPACE}
    hooked = asyncio.run(intelligence._with_sap_b1_digest(user, SEMAFORO, {"evidence_handle": "evh_9"}))
    assert hooked["digest"]["sent"] is True and calls == ["evh_9"]
    assert asyncio.run(intelligence._with_sap_b1_digest(user, SEMAFORO, {"ok": True})) == {"ok": True}
    assert asyncio.run(intelligence._with_sap_b1_digest(user, SUPPLY, {"evidence_handle": "x"})) == {"evidence_handle": "x"}

    async def broken(user, payload, *, pool):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(digest_module, "maybe_send_digest", broken)
    failed = asyncio.run(intelligence._with_sap_b1_digest(user, SEMAFORO, {"evidence_handle": "evh_9"}))
    assert failed["digest"] == {"sent": False, "reason": "error"}


def test_digest_ledger_is_scoped_append_only_and_bounded():
    sql = (REPO_ROOT / "infra/init/99zzzzn_sap_b1_digest_deliveries.sql").read_text(encoding="utf-8")
    assert "PRIMARY KEY (tenant_id, workspace_id, local_date)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "REVOKE DELETE, TRUNCATE ON sap_b1_digest_deliveries FROM omega_console" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON sap_b1_digest_deliveries TO omega_console" in sql
    assert "'99zzzzn_sap_b1_digest_deliveries.sql'" in sql
    assert f"recipients BETWEEN 0 AND {sap_b1_digest.MAX_RECIPIENTS}" in sql
