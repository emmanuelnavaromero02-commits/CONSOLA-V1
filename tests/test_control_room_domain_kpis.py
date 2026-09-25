from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from app.schemas.control_room_domain_kpi_responses import (
    ControlRoomFinanceKpisResponse,
    ControlRoomOperationsKpisResponse,
    ControlRoomRiskKpisResponse,
)
from app.schemas.control_room_public_projection import _safe_text
from app.services.control_room import domain_kpis
from app.services.intelligence.finance_aggregates import (
    BillableHoursLogged,
    LaborCostByDepartment,
    ProjectMargin,
)
from app.services.intelligence.operations_aggregates import (
    AbsenceRateCompanyByType,
    DataFreshnessByCartridge,
    PipelineHealth,
)
from app.services.intelligence.risk_aggregates import (
    AttritionRiskPopulation,
    DealSlippage,
    EmploymentEndExpiry,
)
from app.services.public_text_sensitivity import contains_public_technical_copy

TENANT_A = "11111111-1111-4111-8111-111111111111"
WORKSPACE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _user(**extra) -> dict:
    return {
        "id": 7,
        "tenant_id": TENANT_A,
        "active_tenant_id": TENANT_A,
        "workspace_id": WORKSPACE_A,
        "active_workspace_id": WORKSPACE_A,
        **extra,
    }


def _gold_ref(dataset: str, **filters) -> dict:
    return {
        "type": "gold_relation",
        "dataset": dataset,
        "relation": f'"public"."gold_{dataset}"',
        "run_id": f"run-{dataset}",
        "generation": 3,
        "published_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "missing_optional_columns": [],
        "filters": filters,
    }


def _billable(status: str = "ready") -> BillableHoursLogged:
    return BillableHoursLogged(
        status=status,
        window_start=date(2026, 8, 1),
        window_end=date(2026, 10, 1),
        months=2,
        billable_hours=120.5,
        billable_amount_usd=9040.0,
        projects_affected=3,
        contributors=4,
        top_projects=[
            {
                "proyecto": "P-1",
                "project_name": "Alpha",
                "cliente": "ACME",
                "billable_hours": 80.0,
                "billable_amount_usd": 6000.0,
            }
        ],
        evidence_refs=[
            _gold_ref("consultor_mensual", window_start=date(2026, 8, 1), months=2)
        ],
        notes=["billing_rate_usd ausente: billable_amount_usd no calculable"],
        proxy_note="developer note with identifiers (billing_rate_usd)",
    )


def _labor(status: str = "ready") -> LaborCostByDepartment:
    return LaborCostByDepartment(
        status=status,
        period=date(2026, 8, 1),
        departments_count=2,
        headcount=12,
        total_cost=45000.0,
        departments=[
            {
                "departamento": "SAP",
                "headcount": 7,
                "hours": 3000.0,
                "cost": 30000.0,
                "sunk_cost": 6000.0,
                "potential_cost": 36000.0,
            }
        ],
        evidence_refs=[_gold_ref("costo_consultor_mensual", period=date(2026, 8, 1))],
        notes=["sin meses cerrados con datos"],
    )


def _margin(status: str = "ready") -> ProjectMargin:
    return ProjectMargin(
        status=status,
        error="missing: dataset unavailable: pnl_mensual"
        if status == "unavailable"
        else None,
        projects_count=3,
        total_margin=60000.0,
        original_currencies=["MXN", "USD"],
        evidence_refs=[]
        if status == "unavailable"
        else [
            _gold_ref(
                "pnl_mensual",
                original_currencies=["MXN", "USD"],
                base_currency="unverified",
            )
        ],
    )


class _Recorder:

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    async def __call__(self, user, **kwargs):
        self.calls.append((user, kwargs))
        return self.result


@pytest.mark.parametrize(
    "statuses, expected",
    [
        (["ready", "ready", "ready"], "ready"),
        (["ready", "degraded", "ready"], "degraded"),
        (["ready", "unavailable", "ready"], "degraded"),
        (["unavailable", "unavailable"], "unavailable"),
        (["degraded", "unavailable"], "degraded"),
        ([], "unavailable"),
    ],
)
def test_combine_status(statuses, expected):
    assert domain_kpis.combine_status(statuses) == expected


def test_public_notes_and_labels_survive_projection():
    for metric, note in domain_kpis.PUBLIC_PROXY_NOTES.items():
        assert not contains_public_technical_copy(note), metric
        assert _safe_text(note, field="proxy_note") == note, metric
        assert "NO " in note, metric
    for key, label in domain_kpis.PUBLIC_SOURCE_LABELS.items():
        assert _safe_text(label, field="source") == label, key
    for key, label in domain_kpis.PUBLIC_CARTRIDGE_LABELS.items():
        assert _safe_text(label, field="cartridge") == label, key
    assert set(domain_kpis.PUBLIC_PROXY_NOTES) == set(
        domain_kpis.FINANCE_METRICS
        + domain_kpis.OPERATIONS_METRICS
        + domain_kpis.RISK_METRICS
        + domain_kpis.SAP_B1_MARGIN_METRICS
    )


def test_public_notes_translate_instead_of_dropping():
    kept = domain_kpis.public_notes(
        [
            "sin meses cerrados con datos en costo_consultor_mensual",
            "headcount_by_department sin filas para el scope: absence_rate no calculable",
            "sap_successfactors_talent_action_candidates no disponible: sin cifra oficial",
            "un detalle tecnico nuevo sobre gold_consultor_mensual",
            "sin meses cerrados con datos en absence_by_type_and_month",
            "",
            None,
        ]
    )
    assert kept == [
        "sin meses cerrados con datos",
        "la plantilla no tiene filas para este workspace: no se puede calcular la tasa, "
        "solo los dias de ausencia",
        "sin cifra oficial de Talento: el catalogo de acciones no esta publicado",
        domain_kpis._GENERIC_NOTE,
    ]
    for note in kept:
        assert _safe_text(note, field="notes") == note
        assert not contains_public_technical_copy(note)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("missing: dataset unavailable: pnl_mensual", "missing"),
        (
            "invalid_schema: consultor_mensual misses required columns mes",
            "invalid_schema",
        ),
        ("no_permission: gold dataset requires complete scope", "no_permission"),
        ("invalid_scope: active workspace is required", "invalid_scope"),
        ('unavailable: relation "gold_x" does not exist', "unavailable"),
        ("driver exploded", "unavailable"),
    ],
)
def test_public_error_keeps_the_reason_and_drops_the_technical_tail(raw, expected):
    public = domain_kpis.public_error(raw)
    assert public.split(":", 1)[0] == expected
    assert public == f"{expected}: {domain_kpis.PUBLIC_ERROR_REASONS[expected]}"
    assert _safe_text(public, field="error") == public
    for identifier in ("pnl_mensual", "consultor_mensual", "gold_x", "relation"):
        assert identifier not in public
    assert domain_kpis.public_error(None) is None


def test_public_evidence_sanitises_filter_values():
    refs = domain_kpis.public_evidence(
        "project_margin",
        [
            _gold_ref(
                "pnl_mensual",
                base_currency="unverified (pnl_mensual.base_currency is NULL)",
                original_currencies=["MXN", "USD"],
                months=2,
                sla_source="param",
            )
        ],
    )
    filters = refs[0]["filters"]
    assert filters["base_currency"] == "sin verificar en el origen"
    assert filters["original_currencies"] == ["MXN", "USD"]
    assert filters["months"] == 2
    assert filters["sla_source"] == "param"
    for value in filters.values():
        if isinstance(value, str):
            assert _safe_text(value, field="base_currency") == value
    assert "pnl_mensual" not in json.dumps(refs, ensure_ascii=False)
    assert domain_kpis.public_filter_value("gold_consultor_mensual snapshot") is None
    assert domain_kpis.public_filter_value("param") == "param"


def test_public_evidence_uses_labels_and_scalar_filters():
    refs = domain_kpis.public_evidence(
        "billable_hours_logged",
        [
            _gold_ref(
                "consultor_mensual",
                window_start=date(2026, 8, 1),
                months=2,
                workspace_id=WORKSPACE_A,
            ),
            {
                "type": "console_table",
                "table": "pipeline_runs",
                "filters": {"as_of": NOW},
            },
        ],
    )
    assert refs[0]["metric"] == "billable_hours_logged"
    assert refs[0]["type"] == "published_dataset"
    assert refs[0]["source"] == domain_kpis.PUBLIC_SOURCE_LABELS["consultor_mensual"]
    assert refs[0]["published_at"] == "2026-09-01T00:00:00+00:00"
    assert refs[0]["generation"] == 3
    assert refs[0]["partial_source"] is False
    assert refs[0]["filters"] == {"window_start": "2026-08-01", "months": 2}
    for forbidden in (
        "run_id",
        "published_run",
        "dataset",
        "relation",
        "missing_optional_columns",
        "workspace_id",
    ):
        assert forbidden not in refs[0], forbidden
    assert refs[1]["type"] == "run_log"
    assert refs[1]["source"] == domain_kpis.PUBLIC_SOURCE_LABELS["pipeline_runs"]
    assert refs[1]["filters"] == {"as_of": NOW.isoformat()}


@pytest.mark.asyncio
async def test_finance_kpis_folds_results_and_forwards_named_rows(monkeypatch):
    billable = _Recorder(_billable())
    labor = _Recorder(_labor("degraded"))
    margin = _Recorder(_margin("unavailable"))
    monkeypatch.setattr(
        domain_kpis.finance_aggregates, "query_billable_hours_logged", billable
    )
    monkeypatch.setattr(
        domain_kpis.finance_aggregates, "query_labor_cost_by_department", labor
    )
    monkeypatch.setattr(domain_kpis.finance_aggregates, "query_project_margin", margin)
    user = _user()

    payload = await domain_kpis.finance_kpis(user, top_n=25)

    assert payload["domain"] == "finance"
    assert payload["status"] == "degraded"
    assert payload["named_rows"] == 10
    assert margin.calls == [(user, {"top_n": 10})]
    assert billable.calls == [(user, {"top_n": 10})]
    assert labor.calls == [(user, {})]
    assert payload["unavailable_metrics"] == ["project_margin"]
    assert payload["degraded_metrics"] == ["labor_cost_by_department"]
    metric = payload["metrics"]["billable_hours_logged"]
    assert (
        metric["proxy_note"] == domain_kpis.PUBLIC_PROXY_NOTES["billable_hours_logged"]
    )
    assert "billing_rate_usd" not in metric["proxy_note"]
    assert metric["notes"] == [
        "sin tarifa publicada: no se puede calcular el monto facturable"
    ]
    assert "missing_columns" not in metric
    assert (
        metric["evidence_refs"][0]["source"]
        == domain_kpis.PUBLIC_SOURCE_LABELS["consultor_mensual"]
    )
    assert payload["metrics"]["labor_cost_by_department"]["notes"] == [
        "sin meses cerrados con datos"
    ]
    assert payload["notes"] == [
        "billable_hours_logged: sin tarifa publicada: no se puede calcular el monto facturable",
        "labor_cost_by_department: sin meses cerrados con datos",
    ]
    assert [ref["metric"] for ref in payload["evidence_refs"]] == [
        "billable_hours_logged",
        "labor_cost_by_department",
    ]
    assert payload["metrics"]["project_margin"]["error"] == (
        "missing: el origen no esta publicado para este workspace"
    )

    projected = ControlRoomFinanceKpisResponse.project(payload)
    out = projected.model_dump()
    assert out["status"] == "degraded"
    assert out["metrics"]["billable_hours_logged"]["billable_hours"] == 120.5
    assert out["metrics"]["billable_hours_logged"]["window_start"] == "2026-08-01"
    assert out["metrics"]["billable_hours_logged"]["proxy_note"] == metric["proxy_note"]
    assert (
        out["metrics"]["billable_hours_logged"]["top_projects"][0]["proyecto"] == "P-1"
    )
    assert (
        out["metrics"]["labor_cost_by_department"]["departments"][0]["cost"] == 30000.0
    )
    assert out["metrics"]["project_margin"]["status"] == "unavailable"
    assert out["metrics"]["project_margin"]["original_currencies"] == ["MXN", "USD"]
    assert out["evidence_refs"][0]["published_at"] == "2026-09-01T00:00:00+00:00"
    assert out["unavailable_metrics"] == ["project_margin"]
    assert out["metrics"]["project_margin"]["error"] == (
        "missing: el origen no esta publicado para este workspace"
    )
    assert out["generated_at"] == payload["generated_at"]
    assert out["metrics"]["billable_hours_logged"]["notes"] == metric["notes"]
    dumped = json.dumps(out, ensure_ascii=False)
    for identifier in (
        "pnl_mensual",
        "consultor_mensual",
        "costo_consultor_mensual",
        "billing_rate_usd",
        "revenue_base_amount",
        "gold_",
        "run-",
    ):
        assert identifier not in dumped, identifier


@pytest.mark.asyncio
async def test_finance_kpis_default_is_aggregates_only(monkeypatch):
    margin = _Recorder(_margin())
    billable = _Recorder(_billable())
    monkeypatch.setattr(
        domain_kpis.finance_aggregates,
        "query_billable_hours_logged",
        billable,
    )
    monkeypatch.setattr(
        domain_kpis.finance_aggregates,
        "query_labor_cost_by_department",
        _Recorder(_labor()),
    )
    monkeypatch.setattr(domain_kpis.finance_aggregates, "query_project_margin", margin)

    payload = await domain_kpis.finance_kpis(_user())

    assert payload["status"] == "ready"
    assert payload["named_rows"] == 0
    assert margin.calls[0][1] == {"top_n": 0}
    assert billable.calls[0][1] == {"top_n": 0}


@pytest.mark.asyncio
async def test_operations_kpis_labels_cartridges_and_validates(monkeypatch):
    health = PipelineHealth(
        status="ready",
        as_of=NOW,
        cartridges=[
            {
                "cartridge_id": "sap_successfactors",
                "failed_24h": 2,
                "success_24h": 1,
                "failed_7d": 3,
                "success_7d": 3,
                "partial_7d": 0,
                "failure_rate_7d": 0.5,
                "last_failed_at": NOW,
                "sources": ["pipeline_runs"],
            },
            {
                "cartridge_id": "replicon",
                "failed_24h": 0,
                "success_24h": 1,
                "failed_7d": 0,
                "success_7d": 7,
                "partial_7d": 0,
                "failure_rate_7d": 0.0,
                "last_failed_at": None,
                "sources": ["pipeline_runs"],
            },
        ],
        totals={
            "failed_24h": 2,
            "success_24h": 2,
            "failed_7d": 3,
            "success_7d": 10,
            "partial_7d": 0,
        },
        cartridges_count=2,
        cartridges_with_failures_24h=1,
        failing_entities=[
            {
                "cartridge_id": "sap_hcm",
                "entity": "PA0001",
                "failures_7d": 2,
                "failures_24h": 2,
                "last_failed_at": NOW,
                "sources": ["extraction_runs"],
            }
        ],
        evidence_refs=[
            {
                "type": "console_table",
                "table": "pipeline_runs",
                "database": "DATABASE_URL",
                "filters": {"as_of": NOW, "workspace_id": WORKSPACE_A},
            },
        ],
        notes=["solo SuccessFactors espeja extraction_runs en pipeline_runs"],
    )
    freshness = DataFreshnessByCartridge(
        status="degraded",
        as_of=NOW,
        sla_hours=24.0,
        sla_source="default",
        cartridges=[
            {
                "cartridge_id": "replicon",
                "last_success_at": None,
                "success_runs": 3,
                "hours_since_success": 30.0,
                "exceeds_sla": True,
                "sources": ["extraction_runs"],
            }
        ],
        cartridges_count=1,
        exceeding_sla=1,
        notes=["sla_hours no configurado: se usa el default de 24h"],
    )
    absence = AbsenceRateCompanyByType(
        status="ready",
        period=date(2026, 8, 1),
        working_days=21,
        headcount=100,
        total_days_workable=210.0,
        absence_rate=0.1,
        types_count=1,
        by_type=[
            {
                "absence_type": "0100",
                "days_workable": 150.0,
                "employees_affected": 40,
                "absence_records": 55,
                "rate": 0.0714,
            }
        ],
        evidence_refs=[_gold_ref("absence_by_type_and_month", period=date(2026, 8, 1))],
    )
    monkeypatch.setattr(
        domain_kpis.operations_aggregates, "query_pipeline_health", _Recorder(health)
    )
    monkeypatch.setattr(
        domain_kpis.operations_aggregates,
        "query_data_freshness_by_cartridge",
        _Recorder(freshness),
    )
    monkeypatch.setattr(
        domain_kpis.operations_aggregates,
        "query_absence_rate_company_by_type",
        _Recorder(absence),
    )

    payload = await domain_kpis.operations_kpis(_user())
    out = ControlRoomOperationsKpisResponse.project(payload).model_dump()

    assert out["domain"] == "operations"
    assert out["status"] == "degraded"
    rows = out["metrics"]["pipeline_health"]["cartridges"]
    assert rows[0]["cartridge"] == "SAP SuccessFactors"
    assert (
        rows[0]["cartridge_id"] is None
    )
    assert rows[1] == {
        "cartridge_id": "replicon",
        "cartridge": "Replicon",
        "failed_24h": 0,
        "success_24h": 1,
        "failed_7d": 0,
        "success_7d": 7,
        "partial_7d": 0,
        "failure_rate_7d": 0.0,
        "last_failed_at": None,
        "sources": ["pipeline_runs"],
    }
    assert out["metrics"]["pipeline_health"]["totals"]["failed_7d"] == 3
    assert (
        out["metrics"]["pipeline_health"]["failing_entities"][0]["cartridge"]
        == "SAP HCM"
    )
    assert (
        out["metrics"]["pipeline_health"]["failing_entities"][0]["entity"] == "PA0001"
    )
    assert (
        out["metrics"]["pipeline_health"]["evidence_refs"][0]["source"]
        == "Bitacora de corridas de pipelines"
    )
    assert (
        out["metrics"]["pipeline_health"]["evidence_refs"][0]["filters"]["as_of"]
        == NOW.isoformat()
    )
    assert out["metrics"]["data_freshness_by_cartridge"]["exceeding_sla"] == 1
    assert (
        out["metrics"]["data_freshness_by_cartridge"]["cartridges"][0]["exceeds_sla"]
        is True
    )
    assert out["metrics"]["absence_rate_company_by_type"]["absence_rate"] == 0.1
    assert (
        out["metrics"]["absence_rate_company_by_type"]["by_type"][0]["absence_type"]
        == "0100"
    )
    assert out["degraded_metrics"] == ["data_freshness_by_cartridge"]
    assert out["notes"] == [
        "pipeline_health: solo SuccessFactors espeja extraction_runs en pipeline_runs",
        "data_freshness_by_cartridge: sla_hours no configurado: se usa el default de 24h",
    ]


@pytest.mark.asyncio
async def test_risk_kpis_named_rows_and_projection(monkeypatch):
    attrition = AttritionRiskPopulation(
        status="ready",
        total=6160,
        high=6000,
        medium=100,
        low=50,
        insufficient_data=10,
        avg_score_valid=71.25,
        departments_top=[
            {
                "department_name": "Operaciones",
                "total": 4000,
                "high": 3900,
                "medium": 50,
            }
        ],
        talent_action={
            "action_id": "talent_retention_risk",
            "affected_count": 6000,
            "severity": "high",
            "present": True,
        },
        evidence_refs=[_gold_ref("sap_successfactors_talent_retention_risk", top_n=10)],
        notes=["recommendation_only: riesgo de salida sin datos de compensacion."],
    )
    expiry = EmploymentEndExpiry(
        status="ready",
        as_of=date(2026, 9, 13),
        within_30=4,
        within_60=9,
        within_90=15,
        active_filter_applied=True,
        sentinel_excluded_from=date(2030, 1, 1),
        departments_top=[
            {
                "department_name": "Ventas",
                "within_30": 1,
                "within_60": 4,
                "within_90": 11,
            }
        ],
    )
    deals = DealSlippage(
        status="ready",
        as_of=date(2026, 9, 13),
        deals=6010,
        amount_total=1234567.89,
        buckets={
            "1_30": {"deals": 6000, "amount": 1200000.0},
            "31_60": {"deals": 10, "amount": 34567.89},
            "over_60": {"deals": 0, "amount": 0.0},
        },
        by_stage=[{"stage_name": "Negotiation", "deals": 6000, "amount": 1200000.0}],
        by_reason=[
            {
                "risk_reason": "cierre vencido",
                "deals": 6010,
                "amount": 1234567.89,
                "max_days_overdue": 45,
            }
        ],
        top_deals=[
            {
                "opportunity_name": "Big One",
                "stage_name": "Negotiation",
                "amount": 250000.0,
                "close_date": date(2026, 9, 1),
                "days_overdue": 12,
                "risk_reason": "cierre vencido",
                "vendedor": "Juan Perez",
                "owner_email": "juan@example.com",
                "opportunity_id": "006ABC",
            }
        ],
        evidence_refs=[
            _gold_ref("salesforce_deals_en_riesgo", as_of=date(2026, 9, 13), top_n=3)
        ],
    )
    deals_fn = _Recorder(deals)
    monkeypatch.setattr(
        domain_kpis.risk_aggregates,
        "query_attrition_risk_population",
        _Recorder(attrition),
    )
    monkeypatch.setattr(
        domain_kpis.risk_aggregates, "query_employment_end_expiry", _Recorder(expiry)
    )
    monkeypatch.setattr(domain_kpis.risk_aggregates, "query_deal_slippage", deals_fn)

    payload = await domain_kpis.risk_kpis(_user(), top_n=3)
    out = ControlRoomRiskKpisResponse.project(payload).model_dump()

    assert deals_fn.calls[0][1] == {"top_n": 3}
    assert out["status"] == "ready"
    assert out["named_rows"] == 3
    assert out["metrics"]["attrition_risk_population"]["high"] == 6000
    assert (
        out["metrics"]["attrition_risk_population"]["talent_action"]["affected_count"]
        == 6000
    )
    assert out["metrics"]["attrition_risk_population"]["evidence_refs"][0][
        "source"
    ] == ("Talento: riesgo de retencion por empleado")
    assert out["metrics"]["employment_end_expiry"]["within_90"] == 15
    assert (
        out["metrics"]["employment_end_expiry"]["sentinel_excluded_from"]
        == "2030-01-01"
    )
    deal = out["metrics"]["deal_slippage"]
    assert deal["buckets"] == [
        {"bucket": "1_30", "deals": 6000, "amount": 1200000.0},
        {"bucket": "31_60", "deals": 10, "amount": 34567.89},
        {"bucket": "over_60", "deals": 0, "amount": 0.0},
    ]
    assert deal["by_reason"][0]["risk_reason"] == "cierre vencido"
    assert deal["top_deals"] == [
        {
            "opportunity_name": "Big One",
            "stage_name": "Negotiation",
            "amount": 250000.0,
            "close_date": "2026-09-01",
            "days_overdue": 12,
            "risk_reason": "cierre vencido",
        }
    ]
    for stripped in ("vendedor", "owner_email", "opportunity_id"):
        assert stripped not in deal["top_deals"][0], stripped
    assert "Juan Perez" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_control_room_service_exposes_domain_views(monkeypatch):
    import app.services.control_room.domain_kpis as fresh_domain_kpis
    from app.services import control_room_service

    calls: list[tuple] = []

    async def fake_finance(user, *, top_n=0):
        calls.append(("finance", user, top_n))
        return {"domain": "finance", "status": "ready"}

    async def fake_operations(user):
        calls.append(("operations", user))
        return {"domain": "operations", "status": "ready"}

    async def fake_risk(user, *, top_n=0):
        calls.append(("risk", user, top_n))
        return {"domain": "risk", "status": "ready"}

    monkeypatch.setattr(fresh_domain_kpis, "finance_kpis", fake_finance)
    monkeypatch.setattr(fresh_domain_kpis, "operations_kpis", fake_operations)
    monkeypatch.setattr(fresh_domain_kpis, "risk_kpis", fake_risk)
    user = _user()

    assert (await control_room_service.finance_kpis(user, top_n=4))[
        "domain"
    ] == "finance"
    assert (await control_room_service.operations_kpis(user))["domain"] == "operations"
    assert (await control_room_service.risk_kpis(user))["domain"] == "risk"
    assert calls == [("finance", user, 4), ("operations", user), ("risk", user, 0)]


@pytest.mark.asyncio
async def test_internal_view_dispatcher_wires_domain_views(monkeypatch):
    monkeypatch.setenv(
        "OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "0"
    )
    from app.routers import control_room as router
    from app.schemas import control_room_domain_kpi_responses as fresh_schemas
    from app.services import control_room_service

    calls: list[tuple] = []

    async def fake_finance(user, *, top_n=0):
        calls.append(("finance", user["workspace_id"], top_n))
        return {
            "domain": "finance",
            "status": "ready",
            "named_rows": top_n,
            "metrics": {},
        }

    async def fake_operations(user):
        calls.append(("operations", user["workspace_id"]))
        return {"domain": "operations", "status": "degraded", "metrics": {}}

    async def fake_risk(user, *, top_n=0):
        calls.append(("risk", user["workspace_id"], top_n))
        return {
            "domain": "risk",
            "status": "unavailable",
            "named_rows": top_n,
            "metrics": {},
        }

    monkeypatch.setattr(control_room_service, "finance_kpis", fake_finance)
    monkeypatch.setattr(control_room_service, "operations_kpis", fake_operations)
    monkeypatch.setattr(control_room_service, "risk_kpis", fake_risk)
    user = _user(_effective_permissions=["datasets.read"])

    finance = await router._control_room_internal_view(
        "finance_kpis", user, {"top_n": "99"}
    )
    operations = await router._control_room_internal_view("operations_kpis", user, {})
    risk = await router._control_room_internal_view("risk_kpis", user, {"top_n": -5})

    assert isinstance(finance, fresh_schemas.ControlRoomFinanceKpisResponse)
    assert isinstance(operations, fresh_schemas.ControlRoomOperationsKpisResponse)
    assert isinstance(risk, fresh_schemas.ControlRoomRiskKpisResponse)
    assert finance.status == "ready" and finance.named_rows == 10
    assert operations.status == "degraded"
    assert risk.status == "unavailable" and risk.named_rows == 0
    assert calls == [
        ("finance", WORKSPACE_A, 10),
        ("operations", WORKSPACE_A),
        ("risk", WORKSPACE_A, 0),
    ]

    no_permission = _user(_effective_permissions=["operations.read"])
    with pytest.raises(HTTPException) as exc:
        await router._control_room_internal_view("finance_kpis", no_permission, {})
    assert exc.value.status_code == 403
    assert "datasets.read" in str(exc.value.detail)
    assert "finance_kpis" not in router._INTERNAL_OPERATIONAL_VIEWS


@pytest.mark.asyncio
async def test_named_rows_do_not_share_a_cache_entry(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    from app.routers import control_room as router
    from app.services.control_room import authorization_cache
    from app.services import control_room_service

    async def fake_epoch(user):
        return "epoch-1"

    monkeypatch.setattr(authorization_cache, "publication_epoch", fake_epoch)
    authorization_cache.READ_CACHE.clear()
    calls: list[int] = []

    async def fake_finance(user, *, top_n=0):
        calls.append(top_n)
        return {
            "domain": "finance",
            "status": "ready",
            "named_rows": top_n,
            "metrics": {},
        }

    monkeypatch.setattr(control_room_service, "finance_kpis", fake_finance)
    user = _user(_effective_permissions=["datasets.read"])

    named = await router._control_room_internal_view("finance_kpis", user, {"top_n": 5})
    plain = await router._control_room_internal_view("finance_kpis", user, {})
    again = await router._control_room_internal_view("finance_kpis", user, {"top_n": 5})

    assert named.named_rows == 5
    assert plain.named_rows == 0
    assert again.named_rows == 5
    assert calls == [5, 0]
    namespaces = {key[0] for key in authorization_cache.READ_CACHE}
    assert {"finance-kpis-5", "finance-kpis-0"} <= namespaces
    authorization_cache.READ_CACHE.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain, view, model",
    [
        ("finance", "finance_kpis", ControlRoomFinanceKpisResponse),
        ("operations", "operations_kpis", ControlRoomOperationsKpisResponse),
        ("risk", "risk_kpis", ControlRoomRiskKpisResponse),
    ],
)
async def test_all_metrics_unavailable_reaches_the_llm_as_reason_codes(
    monkeypatch, domain, view, model
):
    metrics = {
        "finance": domain_kpis.FINANCE_METRICS,
        "operations": domain_kpis.OPERATIONS_METRICS,
        "risk": domain_kpis.RISK_METRICS,
    }[domain]
    results = {
        name: BillableHoursLogged(
            status="unavailable",
            error=f"invalid_schema: some_dataset misses required columns col_{index}",
            missing_columns=[f"col_{index}"],
        )
        for index, name in enumerate(metrics)
    }

    payload = domain_kpis.domain_payload(domain, results)
    out = model.project(payload).model_dump()

    assert out["status"] == "unavailable"
    assert out["unavailable_metrics"] == list(metrics)
    for name in metrics:
        metric = out["metrics"][name]
        assert metric["status"] == "unavailable"
        assert metric["error"] == (
            "invalid_schema: el origen publicado no tiene la forma esperada"
        )
        assert metric["proxy_note"] == domain_kpis.PUBLIC_PROXY_NOTES[name]
        assert "missing_columns" not in metric
    dumped = json.dumps(out, ensure_ascii=False)
    assert "some_dataset" not in dumped and "col_0" not in dumped
