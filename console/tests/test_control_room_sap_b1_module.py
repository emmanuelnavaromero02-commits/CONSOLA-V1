from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room import evidence_tickets
from app.services.control_room.attested_monitor_alerts import project_monitor_alerts
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from app.services.control_room.readiness_manifest import dataset_readiness_registry
from app.services.control_room.surface_snapshot import SurfaceScope, SurfaceSnapshot

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
AGENT = "33333333-3333-4333-8333-333333333333"

USER = {
    "id": 7,
    "email": "ops@example.com",
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "allowed_cartridges": ["sap_b1"],
    "role": "admin",
    "_effective_permissions": ["control_room.read"],
}

EXPECTED_SOURCES = {
    "sap_b1_margin_kpis_month": ("Finanzas", "Finanzas", "indicator", "dim_label"),
    "sap_b1_kpi_reconciliation": ("Finanzas", "Finanzas", "indicator", "dim_label"),
    "sap_b1_batch_expiry": ("Ventas", "Ventas", "batch", "item_code"),
    "sap_b1_item_coverage": ("Compras", "Compras", "item_code", "item_name"),
}

SAMPLE_ROWS = {
    "sap_b1_margin_kpis_month": [
        {
            "company": "empresa_a",
            "period": "2026-08",
            "indicator": "margen_bruto",
            "dimension": "total",
            "dim_key": "",
            "dim_label": "Total",
            "value_local": 300.0,
            "value_pct": 30.0,
        }
    ],
    "sap_b1_kpi_reconciliation": [
        {
            "company": "empresa_a",
            "period": "2026-08",
            "indicator": "margen_bruto",
            "dimension": "total",
            "dim_key": "",
            "dim_label": "Total",
            "status": "ok",
            "tolerance_pct": 1.0,
        }
    ],
    "sap_b1_batch_expiry": [
        {
            "company": "empresa_a",
            "item_code": "SKU-1",
            "batch": "L-77",
            "bucket": "0-30",
            "alert_level": "rojo",
            "at_risk_value_local": 1000.0,
        }
    ],
    "sap_b1_item_coverage": [
        {
            "company": "empresa_a",
            "item_code": "MP-9",
            "item_name": "Materia prima 9",
            "coverage_color": "rojo",
            "coverage_days": 4.0,
        }
    ],
}


def _installations() -> AsyncMock:
    return AsyncMock(
        return_value=[
            {
                "cartridge_id": "sap_b1",
                "installation_status": "ready",
                "label": "SAP Business One",
            }
        ]
    )


def _sap_b1_sources():
    return [
        source
        for source in control_room_service._all_sources()  # noqa: SLF001
        if source.cartridge == "sap_b1"
    ]


async def _empty_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
    return []


async def _populated_fetcher(dataset: str, user: dict | None, _limit: int) -> list[dict]:
    context = user or USER
    return [
        {
            **row,
            "tenant_id": context["tenant_id"],
            "workspace_id": context["active_workspace_id"],
        }
        for row in SAMPLE_ROWS.get(dataset, [])
    ]


async def _dashboard(fetcher) -> dict:
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0
    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_installed_cartridges", new=_installations()
        ),
    ):
        return await control_room_service.dashboard(USER, fetcher=fetcher)


def test_sap_b1_module_registered_with_business_spanish_sources():
    modules = [
        module
        for module in control_room_service.MODULES
        if module.cartridge == "sap_b1"
    ]
    assert len(modules) == 1
    module = modules[0]
    assert module.visible_id == "sap_b1"
    assert module.label == "SAP Business One"
    assert not module.operational

    sources = {source.dataset: source for source in module.sources}
    assert set(sources) == set(EXPECTED_SOURCES)
    for dataset, (domain, label, id_field, label_field) in EXPECTED_SOURCES.items():
        source = sources[dataset]
        assert source.domain == domain
        assert source.module_label == label
        assert source.entity_id_field == id_field
        assert source.entity_label_field == label_field
        assert source.kind == "metric"
        assert source.normalizer == "metric_snapshot"
        assert source.visible_module_id == "sap_b1"
        assert source.domain in control_room_service.DOMAIN_ORDER


def test_sap_b1_sources_have_honest_readiness_manifest_entries():
    registry = dataset_readiness_registry()
    for dataset in EXPECTED_SOURCES:
        entry = registry[("sap_b1", dataset)]
        assert entry["data_readiness"] == "partial"
        assert "SAP Business One" in entry["reason"]
        assert entry["blockers"]


def test_sap_b1_partial_sources_are_not_fetched_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CONTROL_ROOM_SHOW_KNOWN_NON_READY", raising=False)
    for source in _sap_b1_sources():
        assert control_room_service._source_contract_is_visible(source) is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_dashboard_empty_sap_b1_gold_yields_no_facts_and_no_zeros():
    result = await _dashboard(_empty_fetcher)

    sap_b1_sources = [
        source for source in result["sources"] if source["cartridge"] == "sap_b1"
    ]
    assert {source["dataset"] for source in sap_b1_sources} == set(EXPECTED_SOURCES)
    for source in sap_b1_sources:
        assert source["count"] == 0
        assert source["operationally_ready"] is False
    assert result["items"] == []

    module = next(item for item in result["cartridges"] if item["id"] == "sap_b1")
    assert module["label"] == "SAP Business One"
    assert module["operationally_ready"] is False
    assert module["item_count"] == 0


@pytest.mark.asyncio
async def test_dashboard_populated_sap_b1_gold_counts_rows_without_inventing_items():
    result = await _dashboard(_populated_fetcher)

    sap_b1_sources = {
        source["dataset"]: source
        for source in result["sources"]
        if source["cartridge"] == "sap_b1"
    }
    for dataset in EXPECTED_SOURCES:
        source = sap_b1_sources[dataset]
        assert source["status"] == "ok"
        assert source["count"] == len(SAMPLE_ROWS[dataset])
        assert source["data_readiness"] == "partial"
        assert source["readiness_reason"]
        assert source["operationally_ready"] is False
    assert [item for item in result["items"] if item.get("cartridge") == "sap_b1"] == []

    domains = {domain["label"] for domain in result["domains"]}
    assert {"Finanzas", "Ventas", "Compras"} <= domains


class _FakeConn:
    def __init__(self, agent_row: dict):
        self.agent_row = agent_row
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: object):
        self.executed.append((sql, args))
        return self.agent_row

    async def fetchval(self, sql: str, *args: object):
        self.executed.append((sql, args))
        return args[0]

    def inserts(self) -> list[tuple]:
        return [
            args
            for sql, args in self.executed
            if "INSERT INTO control_room_evidence_tickets" in sql
        ]


CONTRACT = {
    "engine": "wisdom_bit",
    "wisdom_bit_id": "WB-B1-MARGEN",
    "domain": "Finanzas",
    "dataset": "sap_b1_margin_kpis_month",
    "severity": "medium",
    "dedup_key": "sap_b1:sap-b1-margen:WB-B1-MARGEN",
}
PAYLOAD = {
    "wisdom_bit_id": "WB-B1-MARGEN",
    "status": "degraded",
    "signals": {"count": 2, "items": [{}, {}]},
}


async def _minted_sap_b1_reference(monkeypatch) -> tuple[str, dict]:
    @asynccontextmanager
    async def _scoped_db(pool, tenant_id, workspace_id):
        yield pool

    monkeypatch.setattr(evidence_tickets, "scoped_db", _scoped_db)
    conn = _FakeConn(
        {
            "cartridge_id": "sap_b1",
            "slug": "sap-b1-margen",
            "extra": json.dumps({"monitor": CONTRACT}),
        }
    )
    handle = await evidence_tickets.mint_monitor_evidence_ticket(
        conn,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        agent_id=AGENT,
        agent_run_id=41,
        schedule_run_id=9,
        fencing_token=3,
        internal_service="mcp-infra",
        security_context_source="agent_runner",
        requested_wisdom_bit_id="WB-B1-MARGEN",
        requested_cartridge_id="sap_b1",
        payload=PAYLOAD,
    )
    assert handle is not None
    (insert,) = conn.inserts()
    return insert[3], json.loads(insert[20])


def _alert_row(item_id: str) -> dict:
    return {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "item_id": item_id,
        "cartridge_id": "sap_b1",
        "domain": "Finanzas",
        "source_dataset": "sap_b1_margin_kpis_month",
        "title": "Margen bruto por debajo del minimo acordado",
        "severity": "medium",
        "status": "open",
        "entity_label": "WB-B1-MARGEN",
        "metadata": {
            "source": "agent",
            "agent_id": AGENT,
            "agent_run_id": "41",
            "alert_type": "wisdombit_monitor",
            "analysis_evidence": {
                "analysis_type": "wb-b1-margen_monitor",
                "engine": "wisdom_bit",
                "engine_run_id": f"agent:{AGENT}:run:41:wisdombit:WB-B1-MARGEN",
                "metrics": {"status": "degraded", "signal_count": 2},
                "blockers": [],
            },
        },
        "last_seen_at": datetime.now(UTC),
    }


def _snapshot(surface) -> SurfaceSnapshot:
    return SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=SurfaceScope(tenant_id=TENANT, workspace_id=WORKSPACE),
        items=surface.items,
        diagnostics=(),
        sources=(),
        installations=(),
        narratives=surface.narratives,
    )


@pytest.mark.asyncio
async def test_attested_sap_b1_alert_becomes_a_fact_with_the_real_number(monkeypatch):
    item_id, reference = await _minted_sap_b1_reference(monkeypatch)

    surface = project_monitor_alerts(
        [_alert_row(item_id)], {item_id: [reference]}, user=USER
    )
    assert [item["id"] for item in surface.items] == [item_id]

    response = build_business_experience_v2(
        _snapshot(surface), user=USER, enabled_template_ids=frozenset(), actions_by_item={}
    )
    facts = [fact for section in response.sections for fact in section.facts]
    assert len(facts) == 1
    assert facts[0].kind == "alert"
    assert facts[0].metric is not None and facts[0].metric.value == 2
    assert response.sections[0].title == "Finanzas"


@pytest.mark.asyncio
async def test_sap_b1_alert_without_attested_evidence_yields_no_facts(monkeypatch):
    item_id, _reference = await _minted_sap_b1_reference(monkeypatch)

    surface = project_monitor_alerts([_alert_row(item_id)], {}, user=USER)
    assert surface.items == ()

    response = build_business_experience_v2(
        _snapshot(surface), user=USER, enabled_template_ids=frozenset(), actions_by_item={}
    )
    assert response.sections == []
