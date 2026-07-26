from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_source_scope import scoped_source_row
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    persistence_metadata,
    workflow_eligibility_provenance,
)
from console.tests.control_room_execution_helpers import (
    executed_item as _executed_item,
)
from console.tests.control_room_execution_router_helpers import (
    execution_fetchrow_router as _execution_fetchrow_router,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
    "allowed_cartridges": ["sap_hcm", "sap_s4hana", "sap_successfactors", "replicon"],
    "_effective_permissions": [
        "control_room.read",
        "control_room.write",
        "control_room.execute",
    ],
}


def _successful_command_tag(query: object, *_args: object) -> str:
    sql = " ".join(str(query).split()).upper()
    for command in ("INSERT", "UPDATE", "DELETE"):
        if sql.startswith(command) or f" {command} " in f" {sql} ":
            return f"{command} 0 1" if command == "INSERT" else f"{command} 1"
    return "SELECT 1"


def _enable_successful_writes(mock_pool: AsyncMock) -> None:
    mock_pool.execute = AsyncMock(side_effect=_successful_command_tag)


def _observed_anomaly_fields(item_id: str) -> dict:
    return {
        "data_status": "ready",
        "metric_type": "scalar",
        "observed_value": 1,
        "detected_at": "2026-06-07T00:00:00Z",
        "evidence_refs": [f"gold_business_observations:{item_id}"],
        "source_dataset": "gold_business_observations",
        "entity_id": item_id,
    }


def _authoritative_item_row(
    item: dict,
    *,
    decision_id: int | None = None,
    stage: WorkflowStage | None = None,
    status: str | None = None,
    selected_option_id: str | None = None,
    execution_status: str | None = None,
) -> dict:
    resolved_decision = (
        decision_id if decision_id is not None else item.get("decision_id")
    )
    policy_item = {
        **item,
        "metadata": business_policy_metadata(item.get("metadata"), item),
    }
    resolved_status = status or item.get("status") or "open"
    metadata = persistence_metadata(policy_item)
    if resolved_decision is not None:
        metadata[DECISION_PROVENANCE_KEY] = workflow_eligibility_provenance(
            item,
            stage=stage
            or (
                WorkflowStage.APPROVED
                if resolved_status == "approved"
                else WorkflowStage.DECISION_CREATED
            ),
            workspace_id="workspace-A",
            decision_id=int(resolved_decision),
            option_id=selected_option_id or item.get("selected_option_id"),
        )
    return {
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "owner_user_id": 7,
        "item_id": item["id"],
        "cartridge_id": item.get("cartridge"),
        "domain": item.get("domain"),
        "source_dataset": item.get("source_dataset"),
        "item_kind": item.get("kind") or item.get("item_kind"),
        "title": item.get("title"),
        "severity": item.get("severity"),
        "status": resolved_status,
        "decision_id": resolved_decision,
        "entity_kind": item.get("entity_kind"),
        "entity_id": item.get("entity_id"),
        "entity_label": item.get("entity_label"),
        "anomaly_type": item.get("anomaly_type"),
        "metadata": metadata,
        "impact_estimate": item.get("impact_estimate"),
        "impact_currency": item.get("impact_currency"),
        "confidence": item.get("confidence"),
        "priority_score": item.get("priority_score"),
        "selected_option_id": selected_option_id
        if selected_option_id is not None
        else item.get("selected_option_id"),
        "execution_status": execution_status
        or item.get("execution_status")
        or "not_started",
        "first_seen_at": datetime(2026, 5, 20, 9, 0, 0),
        "last_seen_at": datetime(2026, 5, 20, 10, 0, 0),
        "resolved_at": None,
        "dismissed_at": None,
    }


def _approval_fetchrows(item: dict, action: dict) -> list[dict | None]:
    item_id = item["id"]
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-A",
        decision_id=42,
    )
    decision = {"id": 42, "created_by_id": 7, "kpis": []}
    item_row = _authoritative_item_row(item, decision_id=42)
    item_row["metadata"][DECISION_PROVENANCE_KEY] = provenance
    return [
        decision,
        item_row,
        None,
        item_row,
        action,
        {"item_id": item_id, "owner_user_id": 7, "decision_id": 42},
        {"item_id": item_id},
    ]


@pytest.fixture(autouse=True)
def scoped_test_installations(monkeypatch):
    async def installed(user):
        allowed = {
            str(item)
            for item in (user or {}).get("allowed_cartridges", [])
            if str(item).strip()
        }
        return [
            {
                "cartridge_id": cartridge_id,
                "installation_status": "ready",
                "connection_id": f"{cartridge_id}_test",
                "auth_method": "test",
            }
            for cartridge_id in sorted(allowed)
        ]

    monkeypatch.setattr(control_room_service, "_installed_cartridges", installed)


SAMPLE_ROWS = {
    "employees_anomalies": [
        {
            "pernr": "1001",
            "full_name": "Ana Gomez",
            "anomaly_type": "terminated_but_active",
            "severity": "critical",
            "details": '{"reason":"Baja terminada pero usuario activo"}',
            "detected_at": "2026-05-20T10:00:00Z",
        }
    ],
    "business_partner_anomalies": [
        {
            "business_partner": "BP-9",
            "full_name": "Northwind",
            "anomaly_type": "missing_address",
            "severity": "high",
            "details": {"reason": "Sin direccion fiscal"},
            "detected_at": "2026-05-20T11:00:00Z",
        }
    ],
    "sap_successfactors_employees_anomalies": [
        {
            "user_id": "sf-77",
            "full_name": "Luis Perez",
            "anomaly_type": "missing_manager",
            "severity": "medium",
            "details": "{}",
            "detected_at": "2026-05-20T12:00:00Z",
        }
    ],
    "revenue_by_customer": [],
    "open_sales_orders": [],
    "purchase_spend_by_supplier": [],
    "consultor_asignacion": [],
    "consultor_mensual": [],
    "pnl_mensual": [],
    "analytic_skill_gap_by_manager": [],
}

for _source in control_room_service._all_sources():  # noqa: SLF001 - registry contract test fixture
    SAMPLE_ROWS.setdefault(_source.dataset, [])


def test_replicon_control_room_sources_match_beta_gold_contract():
    replicon_sources = [
        source.dataset
        for source in control_room_service._all_sources()  # noqa: SLF001
        if source.cartridge == "replicon"
    ]

    assert "consultor_mensual" in replicon_sources
    assert "consultor_timesheet_semanal" not in replicon_sources
    assert "project_progress_history" not in replicon_sources


def test_replicon_timesheet_normalizer_accepts_monthly_gold_shape():
    source = next(
        source
        for source in control_room_service._all_sources()  # noqa: SLF001
        if source.cartridge == "replicon" and source.dataset == "consultor_mensual"
    )

    item = control_room_service._normalize_replicon_timesheet(  # noqa: SLF001
        source,
        {
            "mes": "2026-08-01",
            "consultor": "Ana Gomez",
            "proyecto": "PROJ-1",
            "project_name": "Omega Norte",
            "horas_no_facturables": 60,
            "horas_totales": 100,
        },
    )

    assert item is not None
    assert item["source_dataset"] == "consultor_mensual"
    assert item["anomaly_type"] == "non_billable_ratio"
    assert item["threshold_state"] == "critical"
    assert "2026-08-01" in item["description"]


async def sample_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
    return _scoped_rows(dataset, SAMPLE_ROWS[dataset], _user)


def _scoped_rows(dataset: str, rows: list[dict], user: dict | None) -> list[dict]:
    context = user or USER
    source = next(
        (
            item
            for item in control_room_service._all_sources()
            if item.dataset == dataset
        ),
        None,
    )
    scoped = []
    for row in rows:
        source_row = {
            **row,
            "tenant_id": context["tenant_id"],
            "workspace_id": context["active_workspace_id"],
        }
        projected = scoped_source_row(
            source_row,
            tenant_id=context["tenant_id"],
            workspace_id=context["active_workspace_id"],
        )
        observed_at = next(
            (
                str(source_row[field])
                for field in (
                    "detected_at",
                    "generated_at",
                    "observation_date",
                    "mes",
                    "semana",
                    "spend_month",
                )
                if source_row.get(field)
            ),
            "",
        )
        if source is not None and observed_at:
            projected.update(
                runtime_row_evidence_fields(
                    source_dataset=dataset,
                    source_system=source.cartridge,
                    cartridge=source.cartridge,
                    tenant_id=context["tenant_id"],
                    workspace_id=context["active_workspace_id"],
                    source_row=source_row,
                    locator_field=source.entity_id_field,
                    observed_at=observed_at,
                    business_observation=projected,
                )
            )
        scoped.append(projected)
    return scoped


@pytest.mark.asyncio
async def test_sap_successfactors_gold_kpis_reads_scoped_gold(monkeypatch):
    from app.services.intelligence import gold_fetcher

    calls: list[tuple[str, dict | None, int]] = []

    async def fake_gold_rows(dataset: str, user: dict | None, limit: int) -> list[dict]:
        calls.append((dataset, user, limit))
        if dataset == "sap_successfactors_employee_360":
            return [
                {"user_id": "100", "full_name": "A", "is_active": True},
                {"user_id": "101", "full_name": "B", "is_active": "true"},
                {"user_id": "102", "full_name": "C", "is_active": False},
            ]
        if dataset == "sap_successfactors_headcount_by_company":
            return [{"company_id": "MX01", "company_name": "FEMSA", "headcount": 2}]
        if dataset == "sap_successfactors_headcount_by_location":
            return [
                {"location_id": "MTY", "location_name": "Monterrey", "headcount": 2}
            ]
        if dataset == "sap_successfactors_headcount_by_department":
            return [
                {"department_id": "HR", "department_name": "People", "headcount": 2}
            ]
        return []

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", fake_gold_rows)

    result = await control_room_service.sap_successfactors_gold_kpis(USER)

    assert result["connection_id"] == "femsa_sf"
    assert result["tenant_id"] == USER["tenant_id"]
    assert result["workspace_id"] == USER["active_workspace_id"]
    active = next(
        widget for widget in result["widgets"] if widget["id"] == "sf_active_headcount"
    )
    assert active["value"] == 2
    assert active["status"] == "ready"
    assert active["dataset"] == "sap_successfactors_employee_360"
    by_company = next(
        widget
        for widget in result["widgets"]
        if widget["id"] == "sf_headcount_by_company"
    )
    assert by_company["status"] == "ready"
    assert by_company["rows"] == [{"label": "FEMSA", "id": "MX01", "headcount": 2}]
    assert {dataset for dataset, _user, _limit in calls} == {
        "sap_successfactors_employee_360",
        "sap_successfactors_headcount_by_company",
        "sap_successfactors_headcount_by_location",
        "sap_successfactors_headcount_by_department",
    }
    assert all(user is USER for _dataset, user, _limit in calls)


@pytest.mark.asyncio
async def test_sap_successfactors_gold_kpis_degrades_when_gold_missing(monkeypatch):
    from app.services.intelligence import gold_fetcher

    async def missing_gold(
        _dataset: str, _user: dict | None, _limit: int
    ) -> list[dict]:
        raise HTTPException(404, "dataset unavailable")

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", missing_gold)

    result = await control_room_service.sap_successfactors_gold_kpis(USER)

    assert result["connection_id"] == "femsa_sf"
    assert result["tenant_id"] == USER["tenant_id"]
    assert result["workspace_id"] == USER["active_workspace_id"]
    assert [widget["value"] for widget in result["widgets"]] == [None, None, None, None]
    assert [widget["status"] for widget in result["widgets"]] == [
        "missing",
        "missing",
        "missing",
        "missing",
    ]
    assert all(
        "dataset unavailable" in str(widget["error"]) for widget in result["widgets"]
    )
    assert all(widget["rows"] == [] for widget in result["widgets"])


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_dashboard_includes_successfactors_talent_gold_signals(monkeypatch):
    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_employee_profile":
            return _scoped_rows(dataset, [{"employee_key": "tal_1"}], _user)
        if dataset == "sap_successfactors_talent_signals":
            return _scoped_rows(
                dataset,
                [
                    {
                        "signal_id": "talent_role_requirements_missing",
                        "signal_type": "pipeline",
                        "severity": "medium",
                        "affected_count": 42,
                        "title": "Requisitos de rol pendientes",
                        "recommendation": "Validar Position y entidades de skills.",
                        "status": "recommendation_only",
                        "generated_at": "2026-06-24T03:27:32Z",
                        "user_id": "100",
                        "full_name": "Ana Gomez",
                    }
                ],
                _user,
            )
        return []

    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_load_lesson_rows", new=AsyncMock(return_value=[])
        ),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_successfactors",
                        "installation_status": "ready",
                        "connection_id": "femsa_sf",
                        "auth_method": "saml_bearer_assertion",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=fetcher)

    signal_items = [
        item
        for item in result["items"]
        if item["source_dataset"] == "sap_successfactors_talent_signals"
        and item["kind"] == "intelligence_signal"
    ]
    assert len(signal_items) == 1
    signal = signal_items[0]
    assert signal["module_id"] == "sap_successfactors_talent"
    assert signal["title"] == "Requisitos de rol pendientes"
    assert signal["control_origin"] == "sap_successfactors_talent_signal"
    assert signal["priority"]["score"] >= 55
    assert signal["recommendation_only"] is True
    assert signal["intelligence"]["baseline"]["actual_value"] == 42
    assert signal["intelligence"]["baseline"]["expected_value"] == 0
    assert signal["intelligence"]["signal"]["confidence"] > 0
    assert (
        signal["intelligence"]["evidence_pack"]["source_dataset"]
        == "sap_successfactors_talent_signals"
    )
    assert signal["intelligence"]["evidence_pack"]["items"]
    assert signal["bayesian_calibration"]["status"] == "not_calibrated"
    assert (
        signal["bayesian_calibration"]["group"] == "sap_successfactors:talent_readiness"
    )
    assert signal["monte_carlo"]["source_id"] == "WB-TALENTO"
    assert signal["math_provenance"]["monte_carlo"]["status"] in {
        "blocked",
        "not_applicable",
    }
    signal_text = json.dumps(signal, ensure_ascii=False)
    assert "Ana Gomez" not in signal_text
    assert '"100"' not in signal_text

    sources = {source["dataset"]: source for source in result["sources"]}
    assert sources["sap_successfactors_talent_employee_profile"]["count"] == 1
    assert (
        sources["sap_successfactors_talent_employee_profile"]["data_readiness"]
        == "partial"
    )
    assert sources["sap_successfactors_talent_signals"]["count"] == 1


@pytest.mark.asyncio
async def test_sap_successfactors_talent_9box_payload_is_aggregate(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box_operational":
            return [
                {
                    "box_key": "estrella",
                    "box_label": "Estrella",
                    "employee_count": 3,
                    "ready_count": 3,
                    "blocked_count": 0,
                    "box_status": "ready",
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    assert result["status"] == "ready"
    assert result["totals"]["ready"] == 3
    assert len(result["cells"]) == 9
    star = next(cell for cell in result["cells"] if cell["box_id"] == "estrella")
    assert star["ready_count"] == 3
    assert "full_name" not in star
    assert "user_id" not in star


@pytest.mark.asyncio
async def test_sap_successfactors_talent_9box_accepts_internal_reference(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box_operational":
            return [
                {
                    "box_key": "core",
                    "box_label": "Core",
                    "employee_count": 8,
                    "ready_count": 8,
                    "benchmark_count": 8,
                    "blocked_count": 0,
                    "box_status": "benchmark_internal",
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    assert result["status"] == "ready"
    assert result["totals"]["ready"] == 8
    assert result["totals"]["reference"] == 8
    assert not any(
        blocker["id"] == "talent_9box_cpa_incomplete" for blocker in result["blockers"]
    )
    core = next(cell for cell in result["cells"] if cell["box_id"] == "core")
    assert core["ready_count"] == 8
    assert core["reference_count"] == 8


@pytest.mark.asyncio
async def test_sap_successfactors_talent_kpis_reads_operational_blocked_count_aliases(
    monkeypatch,
):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_operational_features":
            return [
                {
                    "profiled_count": 1288,
                    "calculable_count": 1288,
                    "readiness_low": 100,
                    "readiness_medium": 700,
                    "readiness_high": 488,
                    "nine_box_classified_count": 1288,
                    "learning_blocked_count": 3,
                    "recruiting_blocked_count": 2,
                    "feature_status": "partial",
                    "readiness_status": "benchmark_internal",
                    "source_mode": "benchmark_internal",
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_kpis(USER)

    assert result["readiness"]["profiled_employees"] == 1288
    assert result["readiness"]["calculable_employees"] == 1288
    assert result["readiness"]["nine_box_available"] == 1288
    assert result["readiness"]["learning_blockers"] == 3
    assert result["readiness"]["recruiting_blockers"] == 2


@pytest.mark.asyncio
async def test_sap_successfactors_talent_kpis_use_readiness_when_operational_row_is_stale(
    monkeypatch,
):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_employee_profile":
            return [{"employee_key": "tal_1"}, {"employee_key": "tal_2"}]
        if dataset == "sap_successfactors_talent_readiness":
            return [
                {
                    "employee_key": "tal_1",
                    "readiness_status": "benchmark_internal",
                    "source_mode": "benchmark_internal",
                },
                {
                    "employee_key": "tal_2",
                    "readiness_status": "benchmark_internal",
                    "source_mode": "benchmark_internal",
                },
            ]
        if dataset == "sap_successfactors_talent_9box":
            return [
                {
                    "employee_key": "tal_1",
                    "box_status": "benchmark_internal",
                    "source_mode": "benchmark_internal",
                }
            ]
        if dataset == "sap_successfactors_talent_operational_features":
            return [
                {
                    "profiled_count": 2,
                    "calculable_count": 0,
                    "readiness_pending_count": 2,
                    "nine_box_classified_count": 0,
                    "feature_status": "partial",
                    "source_mode": "",
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_kpis(USER)

    assert result["readiness"]["calculable_employees"] == 2
    assert result["readiness"]["insufficient_data_employees"] == 0
    assert result["readiness"]["nine_box_available"] == 1
    assert result["readiness"]["source_mode"] == "benchmark_internal"
    assert result["readiness"]["readiness_status"] == "benchmark_internal"
    assert not any(
        blocker["id"] == "talent_cpa_inputs_missing" for blocker in result["blockers"]
    )


@pytest.mark.asyncio
async def test_sap_successfactors_talent_9box_falls_back_to_detailed_rows(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box_operational":
            return [
                {
                    "box_key": "core",
                    "employee_count": 0,
                    "ready_count": 0,
                    "benchmark_count": 0,
                    "blocked_count": 0,
                    "box_status": "blocked",
                }
            ]
        if dataset == "sap_successfactors_talent_9box":
            return [
                {
                    "employee_key": "tal_1",
                    "box_key": "core",
                    "box_status": "benchmark_internal",
                    "source_mode": "benchmark_internal",
                },
                {
                    "employee_key": "tal_2",
                    "box_key": "estrella",
                    "box_status": "ready",
                    "source_mode": "cpa_real",
                },
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    assert result["status"] == "ready"
    assert result["totals"]["ready"] == 2
    assert result["totals"]["reference"] == 1
    assert not any(
        blocker["id"] == "talent_9box_cpa_incomplete" for blocker in result["blockers"]
    )


@pytest.mark.asyncio
async def test_sap_successfactors_talent_9box_roster_masks_people(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box":
            return [
                {
                    "user_id": "100",
                    "full_name": "Ana Gomez",
                    "department_name": "People",
                    "location_name": "Monterrey",
                    "job_code": "MGR",
                    "role_name": "Manager",
                    "readiness_status": "ready",
                    "box_key": "estrella",
                    "box_label": "Estrella",
                    "performance_band": "high",
                    "potential_band": "high",
                    "fit_score": 91.4,
                    "box_status": "ready",
                },
                {
                    "user_id": "101",
                    "full_name": "Luis Perez",
                    "department_name": "Ventas",
                    "location_name": "CDMX",
                    "job_code": "REP",
                    "role_name": "Representante",
                    "readiness_status": "ready",
                    "box_key": "estrella",
                    "box_label": "Estrella",
                    "performance_band": "high",
                    "potential_band": "high",
                    "fit_score": 82,
                    "box_status": "ready",
                },
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_9box_box(
        USER, "estrella"
    )

    assert result["count"] == 2
    assert [
        row["display_name"].startswith("Colaborador ") for row in result["roster"]
    ] == [True, True]
    for row in result["roster"]:
        assert "full_name" not in row
        assert "user_id" not in row
        assert row["employee_key"].startswith("tal_")
        assert row["fit_band"] == "high"
    roster_text = json.dumps(result["roster"], ensure_ascii=False)
    assert "Ana Gomez" not in roster_text
    assert "Luis Perez" not in roster_text
    assert '"100"' not in roster_text


@pytest.mark.asyncio
async def test_sap_successfactors_talent_metadata_and_preview_are_recommendation_only(
    monkeypatch,
):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_cpa_scores":
            return [
                {"user_id": "100", "cpa_status": "ready"},
                {"user_id": "101", "cpa_status": "insufficient_data"},
            ]
        if dataset == "sap_successfactors_talent_action_candidates":
            return [
                {
                    "action_id": "talent_calibration_sensitivity",
                    "kind": "anomaly",
                    "action_type": "sensibilidad",
                    "metric_type": "count",
                    "severity": "medium",
                    "title": "Casos cerca de cortes 9-box",
                    "affected_count": 4,
                    "recommendation": "Revisar calibracion.",
                    "status": "recommendation_only",
                    "method": "cut_sensitivity",
                    "generated_at": "2026-07-16T10:00:00Z",
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    readiness = await control_room_service.sap_successfactors_talent_metadata_readiness(
        USER
    )
    preview = await control_room_service.sap_successfactors_talent_action_preview(
        USER,
        {"action_id": "talent_calibration_sensitivity", "box_id": "core"},
    )

    assert readiness["status"] == "partial"
    assert readiness["summary"]["cpa_ready_employees"] == 1
    assert preview["status"] == "preview_only"
    assert preview["write_back_enabled"] is False
    assert preview["compensation_enabled"] is False
    assert preview["recommendation_only"] is True
    assert preview["external_mutations"] == []


@pytest.mark.asyncio
async def test_talent_performance_entity_available_when_performance_present(
    monkeypatch,
):
    """GATE 1 (Opcion 3): con performance_score presente pero competency/aspiration
    ausentes (cpa_status='insufficient_data', ready_cpa=0), la entidad Performance deja
    de estar 'blocked' y pasa a 'available' (Desempeno disponible - Potencial pendiente).
    Competencia y Aspiracion permanecen bloqueadas; no se fabrica fit ni tono verde."""

    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_cpa_scores":
            return [
                {
                    "user_id": "200",
                    "cpa_status": "insufficient_data",
                    "performance_score": 4.0,
                    "competency_score": None,
                    "aspiration_score": None,
                },
                {
                    "user_id": "201",
                    "cpa_status": "insufficient_data",
                    "performance_score": None,
                    "competency_score": None,
                    "aspiration_score": None,
                },
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    readiness = await control_room_service.sap_successfactors_talent_metadata_readiness(
        USER
    )
    entities = {entity["id"]: entity for entity in readiness["entities"]}

    # Performance ya no aparece bloqueado; pasa a 'available' y NUNCA a 'ready' (verde exige C/P/A).
    assert entities["performance"]["status"] == "available"
    assert entities["performance"]["status"] != "ready"
    assert any(
        "Potencial pendiente" in str(blocker)
        for blocker in entities["performance"]["blockers"]
    )
    # Competencia y Aspiracion permanecen bloqueadas.
    assert entities["competency"]["status"] == "blocked"
    assert entities["aspiration"]["status"] == "blocked"
    # ready_cpa == 0 aqui: no hay empleados con C/P/A completo, no se fabrica fit_score.
    assert readiness["summary"]["cpa_ready_employees"] == 0


@pytest.mark.asyncio
async def test_talent_performance_entity_stays_blocked_without_performance(monkeypatch):
    """Sin performance_score en ninguna fila, Performance sigue 'blocked' (sin regresion)."""

    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_cpa_scores":
            return [
                {
                    "user_id": "300",
                    "cpa_status": "insufficient_data",
                    "performance_score": None,
                    "competency_score": None,
                    "aspiration_score": None,
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    readiness = await control_room_service.sap_successfactors_talent_metadata_readiness(
        USER
    )
    entities = {entity["id"]: entity for entity in readiness["entities"]}
    assert entities["performance"]["status"] == "blocked"
    assert entities["competency"]["status"] == "blocked"
    assert entities["aspiration"]["status"] == "blocked"


def test_talent_performance_band_reuses_current_cuts():
    """Opcion 1: banda real desde performance_score con los cortes actuales (0-5: >=4/>=3).
    Nunca proxy. None sin desempeno."""
    pb = control_room_service._sf_talent_performance_band
    assert pb(4.5) == "high"
    assert pb(3.2) == "medium"
    assert pb(2.0) == "low"
    assert pb(90) == "high"  # escala 0-100 -> /20 = 4.5
    assert pb(64) == "medium"  # 64/20 = 3.2
    assert pb(None) is None
    assert pb("") is None


def test_talent_roster_row_desempeno_disponible_and_separation():
    """Desempeno disponible = desempeno real presente + Potencial pendiente. Fit no se infiere
    (queda insufficient_data mientras fit_score sea NULL)."""
    row_perf = {
        "user_id": "1",
        "performance_score": 4.4,
        "performance_band_available": "high",
        "potential_pending": True,
        "fit_score": None,
    }
    row_noperf = {
        "user_id": "2",
        "performance_score": None,
        "performance_band_available": None,
        "potential_pending": True,
        "fit_score": None,
    }
    row_full = {
        "user_id": "3",
        "performance_score": 4.0,
        "performance_band_available": "high",
        "potential_pending": False,
        "fit_score": 85,
    }
    masked_perf = control_room_service._sf_talent_masked_roster_row(row_perf)
    assert masked_perf["performance_band_available"] == "high"
    assert masked_perf["desempeno_disponible"] is True
    assert masked_perf["fit_band"] == "insufficient_data"  # Fit no inferido
    assert (
        control_room_service._sf_talent_masked_roster_row(row_noperf)[
            "desempeno_disponible"
        ]
        is False
    )
    # Con C/P/A completo (potencial no pendiente) no entra a la cohorte.
    assert (
        control_room_service._sf_talent_masked_roster_row(row_full)[
            "desempeno_disponible"
        ]
        is False
    )


def test_talent_roster_row_band_compute_fallback_without_gold_column():
    """Si el gold aun no trae performance_band_available, se calcula desde performance_score."""
    row = {"user_id": "9", "performance_score": 4.6, "cpa_status": "insufficient_data"}
    masked = control_room_service._sf_talent_masked_roster_row(row)
    assert masked["performance_band_available"] == "high"
    assert masked["desempeno_disponible"] is True  # perf presente + cpa insuficiente


def test_talent_desempeno_cohort_counts_bands_and_sorts():
    rows = [
        {
            "user_id": "1",
            "performance_score": 4.5,
            "performance_band_available": "high",
            "potential_pending": True,
        },
        {
            "user_id": "2",
            "performance_score": 3.1,
            "performance_band_available": "medium",
            "potential_pending": True,
        },
        {
            "user_id": "3",
            "performance_score": None,
            "performance_band_available": None,
            "potential_pending": True,
        },
        {
            "user_id": "4",
            "performance_score": 4.0,
            "performance_band_available": "high",
            "potential_pending": False,
        },
    ]
    cohort = control_room_service._sf_talent_desempeno_cohort(rows)
    assert cohort["count"] == 2  # rows 1 y 2 (3 sin perf, 4 potencial no pendiente)
    assert cohort["band_counts"] == {"high": 1, "medium": 1, "low": 0}
    assert cohort["roster"][0]["performance_band_available"] == "high"  # orden desc


@pytest.mark.asyncio
async def test_talent_9box_payload_exposes_desempeno_cohort(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box":
            return [
                {
                    "user_id": "1",
                    "performance_score": 4.5,
                    "performance_band_available": "high",
                    "potential_pending": True,
                    "box_key": "core",
                    "box_label": "Core",
                    "box_status": "benchmark_internal",
                    "performance_band": "high",
                    "potential_band": "medium",
                },
                {
                    "user_id": "2",
                    "performance_score": 3.0,
                    "performance_band_available": "medium",
                    "potential_pending": True,
                    "box_key": "riesgo",
                    "box_label": "Riesgo",
                    "box_status": "benchmark_internal",
                    "performance_band": "medium",
                    "potential_band": "low",
                },
                {
                    "user_id": "3",
                    "performance_score": None,
                    "performance_band_available": None,
                    "potential_pending": True,
                    "box_key": "",
                    "box_label": "Sin datos suficientes",
                    "box_status": "blocked",
                    "performance_band": "insufficient_data",
                    "potential_band": "insufficient_data",
                },
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)
    payload = await control_room_service.sap_successfactors_talent_9box(USER)
    cohort = payload["desempeno_disponible"]
    assert cohort["count"] == 2
    assert cohort["band_counts"] == {"high": 1, "medium": 1, "low": 0}
    assert cohort["roster"][0]["performance_band_available"] == "high"


# Keep assert messages lazy; Ruff 0.6.9 otherwise rewrites their evaluation order.
# fmt: off
def test_desempeno_module_rewired_to_real_performance_not_compensation():
    """Etapa 1 (Trabajo 1): el modulo Desempeno consume el dataset REAL de performance
    (talent_cpa_scores), no el stub de compensacion; Compensacion es un modulo SEPARADO."""
    from app.services.control_room.core import MODULES

    perf = next(
        (m for m in MODULES if m.module_id == "sap_successfactors_performance"), None
    )
    comp = next(
        (m for m in MODULES if m.module_id == "sap_successfactors_compensation"), None
    )
    assert perf is not None, (
        "modulo Desempeno (sap_successfactors_performance) debe existir"
    )
    assert comp is not None, (
        "modulo Compensacion (sap_successfactors_compensation) separado debe existir"
    )

    perf_datasets = {s.dataset for s in perf.sources}
    comp_datasets = {s.dataset for s in comp.sources}
    assert "sap_successfactors_talent_cpa_scores" in perf_datasets
    assert "sap_successfactors_compensation_distribution" not in perf_datasets
    assert "sap_successfactors_compensation_distribution" in comp_datasets


def test_no_module_mixes_compensation_and_performance():
    """Invariante anti-mezcla: ningun modulo tiene a la vez dataset de compensacion y de
    performance; compensation_distribution lo consume exactamente un modulo; y la separacion
    de term-match del frontend se conserva (Desempeno no matchea terminos de compensacion,
    y viceversa)."""
    from app.services.control_room.core import MODULES

    comp_consumers = 0
    for module in MODULES:
        datasets = {source.dataset for source in module.sources}
        has_comp = any("compensation" in ds for ds in datasets)
        has_perf = any(("performance" in ds or "cpa_scores" in ds) for ds in datasets)
        assert not (has_comp and has_perf), (
            f"modulo {module.visible_id} mezcla compensacion y desempeno"
        )
        if "sap_successfactors_compensation_distribution" in datasets:
            comp_consumers += 1
    assert comp_consumers == 1, (
        "compensation_distribution debe consumirlo exactamente un modulo"
    )

    comp_terms = (
        "compensation",
        "paycomp",
        "payment",
        "paygroup",
        "payroll",
        "salary",
        "amount",
        "currency",
    )
    perf_terms = ("performance", "review", "goal", "competency")
    perf = next(m for m in MODULES if m.module_id == "sap_successfactors_performance")
    comp = next(m for m in MODULES if m.module_id == "sap_successfactors_compensation")

    def match_string(module) -> str:
        parts = [module.module_id or ""]
        for source in module.sources:
            parts.append(source.dataset)
            parts.append(source.module_label or "")
            parts.append(source.visible_module_id)
        return " ".join(parts).lower()

    perf_str, comp_str = match_string(perf), match_string(comp)
    assert any(t in perf_str for t in perf_terms), (
        "Desempeno debe seguir matcheando su tarjeta"
    )
    assert not any(t in perf_str for t in comp_terms), (
        "Desempeno no debe matchear compensacion"
    )
    assert any(t in comp_str for t in comp_terms), (
        "Compensacion debe matchear su tarjeta"
    )
    assert not any(t in comp_str for t in perf_terms), (
        "Compensacion no debe matchear desempeno"
    )
# fmt: on


@pytest.mark.asyncio
async def test_summary_uses_scoped_vault_connected_cartridges(monkeypatch):
    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        return _scoped_rows(dataset, SAMPLE_ROWS[dataset], _user)

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_successfactors",
                        "installation_status": "ready",
                        "connection_id": "femsa_sf",
                        "auth_method": "saml_bearer_assertion",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.summary(USER, fetcher=fetcher)

    assert {source["cartridge"] for source in result["sources"]} == {
        "sap_successfactors"
    }
    assert set(result["by_cartridge"]) <= {"sap_successfactors"}


@pytest.mark.asyncio
async def test_list_anomalies_normalizes_all_real_sources():
    result = await control_room_service.list_anomalies(USER, fetcher=sample_fetcher)

    assert len(result["anomalies"]) == 3
    assert {item["source_dataset"] for item in result["anomalies"]} == {
        "employees_anomalies",
        "business_partner_anomalies",
        "sap_successfactors_employees_anomalies",
    }
    first = result["anomalies"][0]
    assert first["source_dataset"] == "employees_anomalies"
    assert first["severity"] == "critical"
    assert first["severity_weight"] == 4
    assert first["recommendation"]
    assert first["id"]
    source_summary = {
        source["dataset"]: {
            key: source[key] for key in ("cartridge", "status", "count")
        }
        for source in result["sources"]
    }
    assert source_summary["employees_anomalies"] == {
        "cartridge": "sap_hcm",
        "status": "ok",
        "count": 1,
    }
    assert source_summary["business_partner_anomalies"] == {
        "cartridge": "sap_s4hana",
        "status": "ok",
        "count": 1,
    }
    assert source_summary["sap_successfactors_employees_anomalies"] == {
        "cartridge": "sap_successfactors",
        "status": "ok",
        "count": 1,
    }
    anomaly_sources = {
        "employees_anomalies",
        "business_partner_anomalies",
        "sap_successfactors_employees_anomalies",
    }
    expected_empty = {
        source.dataset for source in control_room_service._all_sources()
    } - anomaly_sources  # noqa: SLF001
    actual_empty = {
        item["dataset"] for item in result["sources"] if item["status"] == "empty"
    }
    assert expected_empty <= actual_empty


@pytest.mark.asyncio
async def test_list_anomalies_marks_missing_dataset_unavailable_without_failing():
    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "business_partner_anomalies":
            raise HTTPException(404, "dataset not found")
        return _scoped_rows(dataset, SAMPLE_ROWS[dataset], _user)

    result = await control_room_service.list_anomalies(USER, fetcher=fetcher)

    assert len(result["anomalies"]) == 2
    failed_source = next(
        item
        for item in result["sources"]
        if item["dataset"] == "business_partner_anomalies"
    )
    assert failed_source["status"] == "missing"
    assert failed_source["count"] == 0
    assert "dataset not found" in failed_source["error"]


@pytest.mark.asyncio
async def test_dashboard_marks_partial_and_stub_sources_not_operationally_ready():
    async def readiness_fetcher(
        dataset: str, _user: dict | None, _limit: int
    ) -> list[dict]:
        rows = {key: list(value) for key, value in SAMPLE_ROWS.items()}
        rows["manager_hierarchy"] = [
            {
                "pernr": "1001",
                "full_name": "Ana Gomez",
                "manager_pernr": None,
                "manager_name": None,
                "direct_reports": 0,
                "depth": 0,
            }
        ]
        rows["workforce_cost_monthly"] = [
            {
                "cost_month": "2026-06-01",
                "org_unit_id": "ORG-1",
                "org_unit_name": "People",
                "cost_center": "CC-10",
                "active_headcount": 8,
                "total_base_salary": None,
            }
        ]
        return _scoped_rows(dataset, rows[dataset], _user)

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=readiness_fetcher)

    manager_source = next(
        source
        for source in result["sources"]
        if source["dataset"] == "manager_hierarchy"
    )
    payroll_source = next(
        source
        for source in result["sources"]
        if source["dataset"] == "workforce_cost_monthly"
    )
    assert manager_source["status"] == "ok"
    assert manager_source["data_readiness"] == "partial"
    assert manager_source["operationally_ready"] is False
    assert manager_source["readiness_blockers"]
    assert payroll_source["status"] == "ok"
    assert payroll_source["data_readiness"] == "stub"
    assert payroll_source["operationally_ready"] is False

    summary = result["summary"]
    assert summary["data_readiness"]["partial"] >= 1
    assert summary["data_readiness"]["stub"] >= 1
    assert summary["partial_modules"] >= 1
    assert summary["stub_modules"] >= 1
    assert summary["data_ready_modules"] < summary["active_modules"]
    assert all(item["kind"] != "source_state" for item in result["items"])


@pytest.mark.asyncio
async def test_dashboard_exposes_real_financial_metrics_from_available_sources():
    async def finance_fetcher(
        dataset: str, _user: dict | None, _limit: int
    ) -> list[dict]:
        rows = {key: list(value) for key, value in SAMPLE_ROWS.items()}
        rows["pnl_mensual"] = [
            {
                "mes": "2026-05-01",
                "revenue_manager": "RM Norte",
                "proyecto": "P-1",
                "project_name": "Omega Norte",
                "revenue_usd": 100000,
                "facturacion_mes_usd": 76000,
                "wip_usd": 24000,
                "costo_total": 70000,
                "margen_bruto_usd": 30000,
                "margen_bruto_pct": 30,
            },
            {
                "mes": "2026-05-01",
                "revenue_manager": "RM Sur",
                "proyecto": "P-2",
                "project_name": "Omega Sur",
                "revenue_usd": 20000,
                "facturacion_mes_usd": 26000,
                "wip_usd": -6000,
                "costo_total": 25000,
                "margen_bruto_usd": -5000,
                "margen_bruto_pct": -25,
            },
        ]
        rows["revenue_by_customer"] = [{"customer_code": "C-1", "revenue": 50000}]
        rows["open_sales_orders"] = [
            {
                "customer_code": "C-1",
                "open_value": 12000,
                "open_orders": 3,
                "oldest_age_days": 75,
            }
        ]
        rows["purchase_spend_by_supplier"] = [
            {"supplier_code": "S-1", "total_spend": 15000}
        ]
        return _scoped_rows(dataset, rows[dataset], _user)

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_s4hana",
                        "installation_status": "ready",
                        "label": "SAP S/4HANA",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=finance_fetcher)

    financial = result["summary"]["financial"]
    assert financial["status"] == "ok"
    assert financial["revenue_usd"] == 120000
    assert financial["billed_usd"] == 102000
    assert financial["margin_pct"] == pytest.approx(20.83)
    assert financial["backlog_value"] == 12000
    assert financial["oldest_backlog_days"] == 75
    assert financial["risk_projects"][0]["label"] == "Omega Sur"
    assert result["summary"]["cycle_counts"]["signals"] == len(result["items"])


async def finance_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
    rows = {key: list(value) for key, value in SAMPLE_ROWS.items()}
    rows["pnl_mensual"] = [
        {
            "mes": "2026-05-01",
            "revenue_manager": "RM Norte",
            "proyecto": "P-LOW",
            "project_name": "Omega Low Margin",
            "revenue_usd": 100000,
            "facturacion_mes_usd": 76000,
            "wip_usd": 6000,
            "costo_total": 95000,
            "margen_bruto_usd": 5000,
            "margen_bruto_pct": 5,
        }
    ]
    return _scoped_rows(dataset, rows[dataset], _user)


async def threshold_margin_fetcher(
    dataset: str, _user: dict | None, _limit: int
) -> list[dict]:
    rows = {key: [] for key in SAMPLE_ROWS}
    rows["pnl_mensual"] = [
        {
            "mes": "2026-05-01",
            "revenue_manager": "RM Norte",
            "proyecto": "P-THR",
            "project_name": "Omega Threshold",
            "revenue_usd": 100000,
            "facturacion_mes_usd": 90000,
            "wip_usd": 0,
            "costo_total": 85000,
            "margen_bruto_usd": 15000,
            "margen_bruto_pct": 15,
        }
    ]
    return _scoped_rows(dataset, rows[dataset], _user)


@pytest.mark.asyncio
async def test_dashboard_adds_real_impact_and_priority_without_inventing_money():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=finance_fetcher)

    pnl_item = next(
        item for item in result["items"] if item["anomaly_type"] == "low_margin"
    )
    assert pnl_item["impact_status"] == "ok"
    assert pnl_item["impact_estimate"] == 21000
    assert pnl_item["impact_currency"] == "USD"
    assert pnl_item["priority_score"] > 0
    hcm_item = next(
        item
        for item in result["items"]
        if item["source_dataset"] == "employees_anomalies"
    )
    assert hcm_item["impact_status"] == "unavailable"
    assert hcm_item["impact_estimate"] is None


def test_impact_calculator_uses_cart_specific_cost_basis_when_available():
    hcm = {
        **_observed_anomaly_fields("hcm-1"),
        "id": "hcm-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "anomaly_type": "terminated_but_active",
        "severity": "critical",
        "threshold_state": "default",
        "details": {"salary_monthly_usd": 4200},
    }
    sf = {
        **_observed_anomaly_fields("sf-1"),
        "id": "sf-1",
        "kind": "anomaly",
        "cartridge": "sap_successfactors",
        "anomaly_type": "missing_manager",
        "severity": "medium",
        "threshold_state": "default",
        "details": {"affected_employees": 8, "avg_monthly_cost_usd": 3000},
    }
    bp = {
        **_observed_anomaly_fields("bp-1"),
        "id": "bp-1",
        "kind": "anomaly",
        "cartridge": "sap_s4hana",
        "anomaly_type": "missing_address",
        "severity": "high",
        "threshold_state": "default",
        "details": {"business_partner": "BP-1", "open_value": 50000},
    }

    hcm_impact = control_room_service._impact_for_item(hcm)  # noqa: SLF001
    sf_impact = control_room_service._impact_for_item(sf)  # noqa: SLF001
    bp_impact = control_room_service._impact_for_item(bp)  # noqa: SLF001

    assert hcm_impact["estimate"] == 12600
    assert "monthly_cost_usd * 3" in hcm_impact["formula"]
    assert sf_impact["estimate"] == 3600
    assert "affected_employees" in sf_impact["formula"]
    assert bp_impact["estimate"] == 50000
    assert bp_impact["drivers"][0]["label"] == "Exposicion BP"


def test_action_templates_are_specific_by_cartridge_module_and_anomaly_type():
    cases = [
        (
            {
                **_observed_anomaly_fields("hcm-template"),
                "kind": "anomaly",
                "cartridge": "sap_hcm",
                "anomaly_type": "terminated_but_active",
            },
            "prepare_hcm_access_review",
        ),
        (
            {
                **_observed_anomaly_fields("sf-template"),
                "kind": "anomaly",
                "cartridge": "sap_successfactors",
                "module_id": "sap_successfactors_recruiting",
                "anomaly_type": "stale_requisition",
            },
            "prepare_successfactors_recruiting_review",
        ),
        (
            {
                **_observed_anomaly_fields("bp-template"),
                "kind": "anomaly",
                "cartridge": "sap_s4hana",
                "anomaly_type": "missing_address",
            },
            "prepare_s4_business_partner_review",
        ),
        (
            {
                **_observed_anomaly_fields("revenue-template"),
                "kind": "anomaly",
                "cartridge": "sap_s4hana",
                "anomaly_type": "aged_sales_backlog",
            },
            "prepare_s4_revenue_review",
        ),
        (
            {
                **_observed_anomaly_fields("procurement-template"),
                "kind": "anomaly",
                "cartridge": "sap_s4hana",
                "anomaly_type": "supplier_spend_concentration",
            },
            "prepare_s4_procurement_review",
        ),
    ]

    for item, expected_template in cases:
        templates = control_room_service._action_templates_for_item(item)  # noqa: SLF001
        assert templates[0]["template_id"] == expected_template


@pytest.mark.asyncio
async def test_dashboard_applies_workspace_thresholds_to_detection_and_priority():
    threshold_row = {
        "id": 11,
        "cartridge_id": "replicon",
        "anomaly_type": "low_margin",
        "metric": "margen_bruto_pct",
        "warning_value": 30,
        "critical_value": 16,
        "currency": "PCT",
        "enabled": True,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
        "updated_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=[[threshold_row], []])
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(
            USER, fetcher=threshold_margin_fetcher
        )

    item = next(
        item for item in result["items"] if item["anomaly_type"] == "low_margin"
    )
    assert item["severity"] == "critical"
    assert item["threshold_state"] == "critical"
    assert item["thresholds_applied"][0]["source"] == "workspace"
    assert item["thresholds_applied"][0]["warning_value"] == 30
    assert item["priority_score"] >= 80
    assert result["summary"]["thresholds"]["active"] == 1
    assert result["summary"]["thresholds"]["items_with_thresholds"] >= 1


@pytest.mark.asyncio
async def test_dashboard_exposes_push_ready_alert_queue_with_priority_drivers():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=finance_fetcher)

    alerts = result["alerts"]
    assert alerts
    assert result["summary"]["alerts"]["total"] == len(alerts)
    assert result["summary"]["alerts"]["push_ready"] == 0
    top = alerts[0]
    assert top["push_ready"] is False
    assert top["delivery"]["status"] == "not_configured"
    assert top["delivery"]["enabled"] is False
    assert top["priority_score"] >= alerts[-1]["priority_score"]
    assert any(driver["label"] == "Severidad" for driver in top["drivers"])
    threshold_alert = next(
        alert for alert in alerts if alert["alert_type"] == "threshold_breach"
    )
    assert threshold_alert["item_id"]
    assert threshold_alert["recommended_action"]
    item = next(
        item for item in result["items"] if item["id"] == threshold_alert["item_id"]
    )
    assert item["priority"]["score"] == item["priority_score"]
    assert item["priority"]["formula"]


@pytest.mark.asyncio
async def test_list_alerts_returns_same_alert_contract_as_dashboard():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.list_alerts(USER, fetcher=finance_fetcher)

    assert result["generated_at"]
    assert result["summary"]["total"] == len(result["alerts"])
    assert all(alert["id"].startswith("alert:") for alert in result["alerts"])


@pytest.mark.asyncio
async def test_dashboard_workspace_threshold_can_suppress_default_signal():
    threshold_row = {
        "id": 12,
        "cartridge_id": "replicon",
        "anomaly_type": "low_margin",
        "metric": "margen_bruto_pct",
        "warning_value": 10,
        "critical_value": 0,
        "currency": "PCT",
        "enabled": True,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
        "updated_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=[[threshold_row], []])
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(
            USER, fetcher=threshold_margin_fetcher
        )

    assert not any(item["anomaly_type"] == "low_margin" for item in result["items"])
    assert result["summary"]["thresholds"]["active"] == 1


@pytest.mark.asyncio
async def test_dashboard_keeps_active_empty_cartridges_visible_and_creates_source_items():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    cartridge_ids = {item["id"] for item in result["cartridges"]}
    assert {"sap_hcm", "replicon"} <= cartridge_ids
    replicon = next(item for item in result["cartridges"] if item["id"] == "replicon")
    assert replicon["source_status"] == "empty"
    assert all(item["kind"] != "source_state" for item in result["items"])
    assert result["omega_steps"][0]["label"] == "Senales"
    assert result["meta"]["live_mode"] == "polling"
    assert result["meta"]["refresh_interval_seconds"] == 30
    assert result["meta"]["source_count"] == len(result["sources"])
    assert result["meta"]["item_count"] == len(result["items"])
    assert result["meta"]["generated_at"]
    assert all(source["checked_at"] for source in result["sources"])


@pytest.mark.asyncio
async def test_dashboard_expands_installed_connectors_into_operational_cartridge_map():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "sap_s4hana",
                        "installation_status": "ready",
                        "label": "SAP S/4HANA",
                    },
                    {
                        "cartridge_id": "sap_successfactors",
                        "installation_status": "ready",
                        "label": "SAP SuccessFactors",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    visible_ids = {item["id"] for item in result["cartridges"]}
    assert {
        "sap_hcm_payroll",
        "sap_hcm_absences",
        "sap_successfactors_recruiting",
        "sap_s4hana_sales",
        "sap_s4hana_procurement",
        "sap_s4hana_budget",
        "replicon_finance",
        "replicon_skills",
    } <= visible_ids
    assert all(
        item["connector_id"] in USER["allowed_cartridges"]
        for item in result["cartridges"]
    )
    domain_labels = {
        domain["label"] for domain in result["domains"] if domain["modules"]
    }
    assert {
        "Recursos Humanos",
        "Nomina",
        "Finanzas",
        "Presupuestos",
        "Compras",
        "Ventas",
        "Operacion",
    } <= domain_labels
    ventas = next(domain for domain in result["domains"] if domain["label"] == "Ventas")
    assert any(module["id"] == "sap_s4hana_sales" for module in ventas["modules"])


@pytest.mark.asyncio
async def test_dashboard_hides_denied_connector_modules_from_operational_map():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    visible_ids = {item["id"] for item in result["cartridges"]}
    assert "sap_s4hana_sales" not in visible_ids
    assert "sap_successfactors_recruiting" not in visible_ids
    assert {"sap_hcm", "replicon"} <= visible_ids


@pytest.mark.asyncio
async def test_dashboard_marks_paused_connector_modules_blocked_without_fetching_sources():
    called: list[str] = []

    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        called.append(dataset)
        return _scoped_rows(dataset, SAMPLE_ROWS[dataset], _user)

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_s4hana",
                        "installation_status": "paused",
                        "label": "SAP S/4HANA",
                    },
                ]
            ),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=fetcher)

    assert called == []
    assert result["cartridges"]
    assert all(item["active"] is False for item in result["cartridges"])
    assert {item["source_status"] for item in result["cartridges"]} == {"blocked"}
    assert result["summary"]["source_states"]["blocked"] == len(result["sources"])
    assert all(source["checked_at"] for source in result["sources"])


@pytest.mark.asyncio
async def test_summary_counts_and_scopes_open_decisions_to_active_workspace():
    mock_pool = AsyncMock()
    open_decisions = AsyncMock(return_value=5)

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "count_open_business_decisions",
            new=open_decisions,
        ),
    ):
        result = await control_room_service.summary(USER, fetcher=sample_fetcher)

    assert result["total_anomalies"] == 3
    assert result["by_severity"] == {"critical": 1, "high": 1, "medium": 1, "low": 0}
    assert result["by_cartridge"] == {
        "sap_hcm": 1,
        "sap_s4hana": 1,
        "sap_successfactors": 1,
    }
    assert result["open_decisions"] == 5
    open_decisions.assert_awaited_once_with(mock_pool, USER)


@pytest.mark.asyncio
async def test_create_decision_writes_workspace_bitacora_and_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    decision_row = {
        "id": 42,
        "title": "Decision title",
        "workspace_id": "workspace-A",
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    action_row = {"id": 99, "decision_id": 42}
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            {
                "item_id": anomaly["id"],
                "status": "open",
            },
            _authoritative_item_row({**anomaly, "selected_option_id": None}),
            _authoritative_item_row({**anomaly, "selected_option_id": None}),
            decision_row,
            action_row,
            {
                "item_id": anomaly["id"],
                "owner_user_id": 7,
                "decision_id": None,
            },
            {"item_id": anomaly["id"]},
        ]
    )
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "sap_s4hana",
                        "installation_status": "ready",
                        "label": "SAP S/4HANA",
                    },
                    {
                        "cartridge_id": "sap_successfactors",
                        "installation_status": "ready",
                        "label": "SuccessFactors",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=anomaly),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.create_decision_for_anomaly(
            anomaly["id"],
            USER,
            fetcher=sample_fetcher,
        )

    assert result["decision"]["id"] == 42
    assert mock_pool.fetchrow.call_count == 7
    insert_sql = mock_pool.fetchrow.call_args_list[3].args[0]
    assert "INSERT INTO decisions" in insert_sql
    assert "workspace_id" in insert_sql
    assert mock_pool.fetchrow.call_args_list[3].args[-1] == "workspace-A"
    action_sql = mock_pool.fetchrow.call_args_list[4].args[0]
    assert "INSERT INTO decision_actions" in action_sql
    link_sql = mock_pool.fetchrow.call_args_list[6].args[0]
    assert "UPDATE control_room_items" in link_sql
    assert "RETURNING item_id" in link_sql
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.decision.create"
    assert audit_event.await_args.kwargs["metadata"]["decision_id"] == 42
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_item"


@pytest.mark.asyncio
async def test_select_item_option_persists_metadata_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            _authoritative_item_row(anomaly),
            {"item_id": anomaly["id"]},
        ]
    )
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=anomaly),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.select_item_option(
            anomaly["id"],
            "exception",
            USER,
            fetcher=sample_fetcher,
        )

    assert result["selected"] is True
    assert result["item"]["selected_option_id"] == "exception"
    assert result["item"]["status"] == "in_review"
    assert any(
        '"selected_option_id": "exception"' in str(call.args)
        for call in mock_pool.fetchrow.call_args_list
    )
    assert any(
        "selected_option_id = $4" in call.args[0]
        and "owner_user_id IS NOT DISTINCT FROM $5" in call.args[0]
        for call in mock_pool.fetchrow.call_args_list
    )
    assert any(
        "FOR UPDATE" in call.args[0] for call in mock_pool.fetchrow.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.option.select"
    assert audit_event.await_args.kwargs["metadata"]["option_id"] == "exception"


@pytest.mark.asyncio
async def test_action_preview_and_dry_run_are_persisted_and_audited():
    item = (
        await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0
    template_id = "prepare_billing_review"
    fetchrow_results = iter(
        [
            _authoritative_item_row(item),
            {
                "id": 1,
                "workspace_id": "workspace-A",
                "item_id": item["id"],
                "template_id": template_id,
                "mode": "preview",
                "status": "generated",
                "payload": {},
                "result": {},
                "created_at": datetime(2026, 5, 20, 10, 0, 0),
                "completed_at": datetime(2026, 5, 20, 10, 0, 1),
            },
            _authoritative_item_row(item),
            {
                "id": 2,
                "workspace_id": "workspace-A",
                "item_id": item["id"],
                "template_id": template_id,
                "mode": "dry_run",
                "status": "validated",
                "payload": {},
                "result": {},
                "created_at": datetime(2026, 5, 20, 10, 1, 0),
                "completed_at": datetime(2026, 5, 20, 10, 1, 1),
            },
        ]
    )

    def _fetchrow(query, *_args):
        sql = " ".join(str(query).split()).upper()
        if "FROM CONTROL_ROOM_ACTION_TEMPLATES" in sql:
            return {
                "template_id": template_id,
                "cartridge_id": "replicon",
                "label": "Preparar revision de facturacion",
                "requires_approval": True,
            }
        return next(fetchrow_results)

    mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        preview = await control_room_service.action_preview(
            item["id"], USER, template_id=template_id, fetcher=finance_fetcher
        )
        dry_run = await control_room_service.action_dry_run(
            item["id"], USER, template_id=template_id, fetcher=finance_fetcher
        )

    assert preview["execution"]["status"] == "generated"
    assert preview["item"]["execution_status"] == "preview_generated"
    assert dry_run["execution"]["status"] == "validated"
    assert dry_run["item"]["execution_status"] == "dry_run_validated"
    assert dry_run["result"]["external_write"] is False
    actions = [call.kwargs["action"] for call in audit_event.await_args_list]
    assert "control_room.action.preview" in actions
    assert "control_room.action.dry_run" in actions


@pytest.mark.asyncio
async def test_run_auto_item_executes_server_side_safe_flow_and_audits():
    item = (
        await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"][0]
    selected_item = control_room_service._with_omega(
        {**item, "selected_option_id": "remediate", "status": "in_review"}
    )  # noqa: SLF001
    decision_item = control_room_service._with_omega(
        {**selected_item, "decision_id": 42, "status": "decision_created"}
    )  # noqa: SLF001
    dry_run_item = control_room_service._with_omega(
        {**decision_item, "execution_status": "dry_run_validated"}
    )  # noqa: SLF001
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow.return_value = _authoritative_item_row(
        dry_run_item,
        decision_id=42,
        selected_option_id="remediate",
        execution_status="dry_run_validated",
    )

    with (
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service,
            "record_item_step",
            new=AsyncMock(
                side_effect=[
                    {"event_type": "investigation_reviewed"},
                    {"event_type": "control_checked"},
                ]
            ),
        ) as record_step,
        patch.object(
            control_room_service,
            "select_item_option",
            new=AsyncMock(return_value={"item": selected_item}),
        ) as select_option,
        patch.object(
            control_room_service,
            "create_decision_for_item",
            new=AsyncMock(return_value={"decision": {"id": 42}, "item": decision_item}),
        ) as create_decision,
        patch.object(
            control_room_service,
            "action_preview",
            new=AsyncMock(
                return_value={
                    "execution": {"id": 7},
                    "result": {"mode": "preview"},
                    "item": decision_item,
                }
            ),
        ) as preview,
        patch.object(
            control_room_service,
            "action_dry_run",
            new=AsyncMock(
                return_value={
                    "execution": {"id": 8},
                    "result": {"mode": "dry_run"},
                    "item": dry_run_item,
                }
            ),
        ) as dry_run,
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.run_auto_item(
            item["id"], USER, fetcher=finance_fetcher
        )

    assert result["auto_run"]["completed"] is True
    assert result["auto_run"]["stopped_before_writeback"] is True
    assert result["item"]["execution_status"] == "dry_run_validated"
    select_option.assert_awaited_once()
    create_decision.assert_awaited_once()
    preview.assert_awaited_once()
    dry_run.assert_awaited_once()
    assert record_step.await_count == 2
    assert any(
        "auto_run_completed" in str(call.args)
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.auto_run"
    assert audit_event.await_args.kwargs["critical"] is True


@pytest.mark.asyncio
async def test_get_item_activity_is_workspace_scoped_and_merges_operational_trail():
    policy_metadata = {
        "description": "Margen menor a umbral",
        "recommendation": "Revisar billing",
        "root_cause": "Costo mayor al esperado",
        "impact": "Riesgo de margen",
        "data_status": "ready",
        "source_system": "replicon",
        "metric_type": "scalar",
        "observed_value": 1,
        "observation_date": "2026-05-20T10:00:00Z",
        **runtime_row_evidence_fields(
            source_dataset="pnl_mensual",
            source_system="replicon",
            cartridge="replicon",
            tenant_id="tenant-A",
            workspace_id="workspace-A",
            source_row={"item_id": "item-activity"},
            locator_field="item_id",
            observed_at="2026-05-20T10:00:00Z",
            business_observation={
                "id": "item-activity",
                "kind": "intelligence_signal",
                "metric_type": "scalar",
                "observed_value": 1,
                "observation_date": "2026-05-20T10:00:00Z",
            },
        ),
    }
    business_item = {
        "id": "item-activity",
        "kind": "intelligence_signal",
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "cartridge": "replicon",
        "source_dataset": "pnl_mensual",
        "source_system": "replicon",
        "metadata": policy_metadata,
    }
    metadata = persistence_metadata(business_item)
    metadata[DECISION_PROVENANCE_KEY] = workflow_eligibility_provenance(
        business_item,
        stage=WorkflowStage.APPROVED,
        workspace_id="workspace-A",
        decision_id=77,
        option_id="remediate",
    )
    persisted_item = {
        "item_id": "item-activity",
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "cartridge_id": "replicon",
        "domain": "Finanzas",
        "source_dataset": "pnl_mensual",
        "item_kind": "intelligence_signal",
        "owner_user_id": 7,
        "title": "Margen bajo",
        "severity": "high",
        "status": "approved",
        "decision_id": 77,
        "entity_kind": "Proyecto",
        "entity_id": "P-1",
        "entity_label": "Proyecto Norte",
        "anomaly_type": "low_margin",
        "metadata": metadata,
        "first_seen_at": datetime(2026, 5, 20, 9, 0, 0),
        "last_seen_at": datetime(2026, 5, 20, 10, 0, 0),
        "resolved_at": None,
        "dismissed_at": None,
        "impact_estimate": 21000,
        "impact_currency": "USD",
        "confidence": 0.82,
        "priority_score": 86,
        "selected_option_id": "remediate",
        "execution_status": "dry_run_validated",
    }
    event_rows = [
        {
            "id": 11,
            "item_id": "item-activity",
            "event_type": "approved",
            "actor_email": "ops@example.com",
            "metadata": {"decision_id": 77},
            "created_at": datetime(2026, 5, 20, 10, 4, 0),
        }
    ]
    execution_rows = [
        {
            "id": 12,
            "item_id": "item-activity",
            "template_id": "prepare_billing_review",
            "mode": "dry_run",
            "status": "validated",
            "payload": {"target": "replicon"},
            "result": {
                "message": "Dry-run validado. V1 no escribe en sistemas externos."
            },
            "error": None,
            "actor_email": "ops@example.com",
            "created_at": datetime(2026, 5, 20, 10, 3, 0),
            "completed_at": datetime(2026, 5, 20, 10, 3, 1),
        }
    ]
    decision_rows = [
        {
            "id": 13,
            "decision_id": 77,
            "action_text": "Decision creada desde Sala de Control",
            "note": "Revisar billing",
            "actor": "ops@example.com",
            "ts": datetime(2026, 5, 20, 10, 2, 0),
        }
    ]
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = persisted_item

    def activity_rows(query, *_args):
        sql = str(query)
        if "FROM control_room_item_events" in sql:
            return event_rows
        if "FROM control_room_action_executions" in sql:
            return execution_rows
        if "FROM decision_actions" in sql:
            return decision_rows
        return []

    mock_pool.fetch = AsyncMock(side_effect=activity_rows)

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        result = await control_room_service.get_item_activity("item-activity", USER)

    assert result["counts"] == {
        "events": 1,
        "executions": 1,
        "decision_actions": 1,
        "action_runs": 0,
        "outcomes": 0,
        "total": 3,
    }
    assert [entry["kind"] for entry in result["activity"]] == [
        "event",
        "execution",
        "decision_action",
    ]
    assert result["activity"][0]["label"] == "Aprobacion registrada"
    assert result["activity"][1]["label"] == "Dry-run validado"
    assert result["activity"][1]["payload"] == {"target": "replicon"}
    assert result["activity"][2]["metadata"]["decision_id"] == 77
    event_call = next(
        call
        for call in mock_pool.fetch.call_args_list
        if "FROM control_room_item_events" in str(call.args[0])
    )
    event_sql, workspace_id, item_id = event_call.args
    assert "workspace_id = $1" in event_sql
    assert workspace_id == "workspace-A"
    assert item_id == "item-activity"


@pytest.mark.asyncio
async def test_record_item_step_writes_operational_event_and_audit():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow.return_value = None
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=anomaly),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.record_item_step(
            anomaly["id"],
            "investigation",
            USER,
            note="reviewed root cause",
            fetcher=sample_fetcher,
        )

    assert result["recorded"] is True
    assert result["event_type"] == "investigation_reviewed"
    assert any(
        "INSERT INTO control_room_item_events" in call.args[0]
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.step.record"
    assert audit_event.await_args.kwargs["metadata"]["step_id"] == "investigation"


@pytest.mark.asyncio
async def test_update_item_control_persists_control_state_and_audits():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow.return_value = _authoritative_item_row(anomaly)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=anomaly),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.update_item_control(
            anomaly["id"],
            "refresh",
            {
                "status": "closed",
                "owner": "ops-owner@example.com",
                "note": "validated refresh",
            },
            USER,
            fetcher=sample_fetcher,
        )

    assert result["updated"] is True
    assert result["control"]["id"] == "refresh"
    assert result["control"]["status"] == "closed"
    assert result["control"]["owner"] == "ops-owner@example.com"
    assert result["item"]["control_state"]["refresh"]["status"] == "closed"
    assert any(
        "control_state" in str(call.args[0])
        for call in mock_pool.execute.call_args_list
    )
    assert any(
        "validated refresh" in str(call.args)
        for call in mock_pool.execute.call_args_list
    )
    assert any(
        len(call.args) > 4 and call.args[4] == "control_checked"
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.control.update"
    assert audit_event.await_args.kwargs["critical"] is True


@pytest.mark.asyncio
async def test_update_item_control_rejects_invalid_control_or_status():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
    ):
        with pytest.raises(HTTPException) as missing_exc:
            await control_room_service.update_item_control(
                anomaly["id"],
                "unknown",
                {"status": "closed"},
                USER,
                fetcher=sample_fetcher,
            )
        with pytest.raises(HTTPException) as status_exc:
            await control_room_service.update_item_control(
                anomaly["id"],
                "refresh",
                {"status": "maybe"},
                USER,
                fetcher=sample_fetcher,
            )

    assert missing_exc.value.status_code == 404
    assert status_exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_item_lesson_persists_manual_lesson_and_audits():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    lesson_row = {
        "id": 91,
        "item_id": anomaly["id"],
        "cartridge_id": anomaly["cartridge"],
        "anomaly_type": anomaly["anomaly_type"],
        "rule": "Si reaparece, validar owner antes de aprobar.",
        "source_decision_id": None,
        "confidence": 0.8,
        "metadata": {"manual": True},
        "created_at": datetime(2026, 5, 20, 11, 0, 0),
    }
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow.return_value = _authoritative_item_row(anomaly)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(return_value=[lesson_row]),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.create_item_lesson(
            anomaly["id"],
            {"rule": lesson_row["rule"]},
            USER,
            fetcher=sample_fetcher,
        )

    assert result["created"] is True
    assert result["lesson"]["id"] == 91
    assert result["item"]["lesson_count"] == 1
    assert result["item"]["omega"]["lessons"]["rules"][0] == lesson_row["rule"]
    assert any(
        "INSERT INTO control_room_lessons" in call.args[0]
        for call in mock_pool.execute.call_args_list
    )
    assert any(
        "lesson_recorded" in str(call.args) for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.lesson.create"


@pytest.mark.asyncio
async def test_apply_item_lesson_persists_application_and_audits():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    lesson_row = {
        "id": 91,
        "item_id": anomaly["id"],
        "cartridge_id": anomaly["cartridge"],
        "anomaly_type": anomaly["anomaly_type"],
        "rule": "Si reaparece, validar owner antes de aprobar.",
        "source_decision_id": 42,
        "confidence": 0.82,
        "metadata": {"manual": True},
        "created_at": datetime(2026, 5, 20, 11, 0, 0),
    }
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow.return_value = _authoritative_item_row(anomaly)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(return_value=[lesson_row]),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.apply_item_lesson(
            anomaly["id"],
            91,
            {"note": "Aplicar patron en la siguiente revision"},
            USER,
            fetcher=sample_fetcher,
        )

    assert result["applied"] is True
    assert result["lesson_application"]["lesson_id"] == 91
    assert (
        result["lesson_application"]["note"]
        == "Aplicar patron en la siguiente revision"
    )
    assert result["item"]["status"] == "in_review"
    assert result["item"]["lesson_applications"][0]["lesson_id"] == 91
    assert result["item"]["omega"]["lessons"]["applied"][0]["lesson_id"] == 91
    assert result["item"]["omega"]["lessons"]["rules"][0] == lesson_row["rule"]
    assert any(
        "lesson_applications" in str(call.args)
        for call in mock_pool.execute.call_args_list
    )
    assert any(
        "lesson_applied" in str(call.args) for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.lesson.apply"
    assert audit_event.await_args.kwargs["critical"] is True


@pytest.mark.asyncio
async def test_apply_item_lesson_rejects_unrelated_pattern():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    unrelated_lesson = {
        "id": 404,
        "item_id": "other-item",
        "cartridge_id": "replicon",
        "anomaly_type": "low_margin",
        "rule": "Si cae margen, revisar billing antes de aprobar.",
        "source_decision_id": None,
        "confidence": 0.7,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 11, 0, 0),
    }
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(return_value=[unrelated_lesson]),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.apply_item_lesson(
                anomaly["id"],
                404,
                {},
                USER,
                fetcher=sample_fetcher,
            )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_execute_live_is_blocked_by_default_and_audited(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    items = (
        await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"]
    item = next(candidate for candidate in items if candidate["cartridge"] == "sap_hcm")
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0
    mock_pool.fetchrow = AsyncMock(
        side_effect=_execution_fetchrow_router(
            item,
            template_id="prepare_hcm_access_review",
            execution_status="blocked",
        )
    )

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "replicon",
                        "installation_status": "ready",
                        "label": "Replicon",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="prepare_hcm_access_review",
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 409
    assert (
        audit_event.await_args.kwargs["action"] == "control_room.action.execute.blocked"
    )


def test_writeback_factory_resolves_builtin_sap_hcm_it0008_adapter():
    adapter = control_room_service.WriteBackAdapterFactory.get_adapter("sap_hcm_it0008")

    assert isinstance(adapter, control_room_service.BaseAdapter)
    assert adapter.__class__.__name__ == "SapHcmAdapter"


def test_writeback_factory_resolves_builtin_replicon_adapters(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    for template_type in ("prepare_billing_review", "prepare_replicon_adjustment"):
        adapter = control_room_service.WriteBackAdapterFactory.get_adapter(
            template_type
        )
        capability = control_room_service._writeback_capability(  # noqa: SLF001 - registry wiring test
            control_room_service.ACTION_TEMPLATES[template_type]
        )

        assert isinstance(adapter, control_room_service.BaseAdapter)
        assert adapter.__class__.__name__ == "RepliconAdapter"
        assert capability["mode"] == "external_writeback"
        assert capability["adapter_available"] is True
        assert capability["supported"] is False
        assert capability["status"] == "external_writeback_disabled"

    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    capability = control_room_service._writeback_capability(  # noqa: SLF001 - registry wiring test
        control_room_service.ACTION_TEMPLATES["prepare_billing_review"]
    )
    assert capability["supported"] is True
    assert capability["adapter"] == "prepare_billing_review"


def test_hcm_access_template_is_wired_to_builtin_it0008_adapter(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    template = control_room_service.ACTION_TEMPLATES["prepare_hcm_access_review"]
    capability = control_room_service._writeback_capability(template)  # noqa: SLF001 - registry wiring test

    assert template["template_type"] == "sap_hcm_it0008"
    assert capability["mode"] == "external_writeback"
    assert capability["adapter_available"] is True
    assert capability["supported"] is False
    assert capability["status"] == "external_writeback_disabled"

    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    capability = control_room_service._writeback_capability(template)  # noqa: SLF001 - registry wiring test
    assert capability["supported"] is True
    assert capability["adapter"] == "sap_hcm_it0008"


@pytest.mark.asyncio
async def test_execute_live_external_template_without_adapter_blocks_before_preflight(
    monkeypatch,
):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    items = (
        await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"]
    item = _executed_item(
        next(candidate for candidate in items if candidate["cartridge"] == "sap_s4hana")
    )
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_execution_fetchrow_router(
            item,
            template_id="prepare_sap_review",
            execution_status="blocked",
        )
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service, "_record_writeback_audit_event", new=AsyncMock()
        ) as writeback_audit,
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="prepare_sap_review",
                confirm_execute=True,
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 501
    assert "adapter is not available" in str(exc.value.detail)
    assert any(
        "INSERT INTO control_room_action_executions" in call.args[0]
        for call in mock_pool.fetchrow.call_args_list
    )
    assert any(
        "action_blocked" in str(call.args) for call in mock_pool.execute.call_args_list
    )
    writeback_audit.assert_not_awaited()
    audit_event.assert_awaited_once()
    assert (
        audit_event.await_args.kwargs["action"] == "control_room.action.execute.blocked"
    )
    assert audit_event.await_args.kwargs["metadata"]["reason"] == "adapter_missing"


@pytest.mark.asyncio
async def test_execute_live_external_template_uses_registered_adapter(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")

    class ExternalBillingAdapter(control_room_service.BaseAdapter):
        supports_idempotency = True
        calls: list[bool] = []

        def execute(
            self,
            action_data: dict,
            credentials: dict,
            dry_run: bool = True,
        ) -> control_room_service.ExecutionResult:
            self.calls.append(dry_run)
            assert action_data["template_type"] == "prepare_billing_review"
            assert credentials["cartridge_id"] == "replicon"
            return control_room_service.ExecutionResult(
                ok=True,
                status="executed",
                message="External write-back ok",
                data={"external_id": "WB-1"},
            )

    monkeypatch.setattr(
        control_room_service.WriteBackAdapterFactory,
        "_registry",
        {"prepare_billing_review": ExternalBillingAdapter},
    )
    items = (
        await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"]
    item = _executed_item(
        next(
            candidate
            for candidate in items
            if candidate["source_dataset"] == "pnl_mensual"
        )
    )
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_execution_fetchrow_router(
            item, template_id="prepare_billing_review"
        )
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service, "_record_writeback_audit_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.execute_item(
            item["id"],
            USER,
            template_id="prepare_billing_review",
            confirm_execute=True,
            idempotency_key="idem-ext-1",
            fetcher=finance_fetcher,
        )

    assert result["executed"] is True
    assert result["result"]["external_write"] is True
    assert result["result"]["validation_result"]["status"] == "audit_preflight_recorded"
    assert result["result"]["adapter"] == "ExternalBillingAdapter"
    assert result["result"]["adapter_result"]["data"]["external_id"] == "WB-1"
    assert ExternalBillingAdapter.calls == [False]
    assert result["learning_lesson"]["metadata"]["autonomous_learning"] is True
    assert (
        result["item"]["omega"]["lessons"]["suggested_actions"][0]["template_id"]
        == "prepare_billing_review"
    )
    assert not any(
        "INSERT INTO decision_actions" in call.args[0]
        for call in mock_pool.fetchrow.call_args_list
    )
    assert any(
        "action_executed" in str(call.args) for call in mock_pool.execute.call_args_list
    )
    assert any(
        "INSERT INTO control_room_lessons" in call.args[0]
        for call in mock_pool.execute.call_args_list
    )
    assert audit_event.await_count == 2
    assert (
        audit_event.await_args_list[0].kwargs["action"]
        == "control_room.action.execute.external.preflight"
    )
    assert audit_event.await_args_list[0].kwargs["status"] == "pending"
    assert audit_event.await_args_list[1].kwargs["status"] == "success"
    assert audit_event.await_args_list[1].kwargs["metadata"]["target"] == "replicon"


@pytest.mark.asyncio
async def test_get_suggested_actions_reads_autonomous_learning_lessons():
    item = {
        "id": "item-new",
        "cartridge": "sap_hcm",
        "anomaly_type": "terminated_but_active",
    }
    lesson = {
        "id": 901,
        "item_id": "item-old",
        "cartridge_id": "sap_hcm",
        "anomaly_type": "terminated_but_active",
        "rule": "Para sap_hcm/terminated_but_active, sugerir IT0008.",
        "source_decision_id": 42,
        "confidence": 0.91,
        "metadata": {
            "autonomous_learning": True,
            "suggested_action": {
                "template_id": "prepare_hcm_access_review",
                "template_type": "sap_hcm_it0008",
                "label": "Preparar revision HCM acceso/nomina",
                "action_kind": "hcm_access_review",
                "target": "sap_hcm",
                "adapter": "SapHcmAdapter",
            },
        },
        "created_at": datetime(2026, 5, 20, 10, 2, 1),
    }

    with (
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(side_effect=[[lesson], []]),
        ),
        patch.object(
            control_room_service,
            "_persisted_business_items",
            new=AsyncMock(
                return_value=[
                    {
                        **_observed_anomaly_fields("item-old"),
                        "id": "item-old",
                        "kind": "anomaly",
                        "source_dataset": "employees_anomalies",
                        "evidence_refs": ["employees_anomalies:item-old"],
                    }
                ]
            ),
        ),
    ):
        result = await control_room_service.ControlRoomService().get_suggested_actions(
            USER, item
        )

    assert result["suggested_actions"][0]["template_id"] == "prepare_hcm_access_review"
    assert result["suggested_actions"][0]["template_type"] == "sap_hcm_it0008"
    assert result["suggested_actions"][0]["lesson_id"] == 901


def test_sap_hcm_adapter_dry_run_flag_still_executes_real_handshake(monkeypatch):
    from app.services.adapters import sap_hcm_adapter

    class SapResponse:
        def __init__(
            self,
            status_code: int,
            *,
            headers: dict | None = None,
            body: dict | None = None,
        ):
            self.status_code = status_code
            self.headers = headers or {}
            self._body = body or {}
            self.text = "ok"

        def json(self):
            return self._body

    calls = []

    def fake_pinned_request_sync(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if method == "GET":
            return SapResponse(200, headers={"x-csrf-token": "csrf-123"})
        return SapResponse(201, body={"d": {"id": "sap-writeback-1"}})

    monkeypatch.setattr(
        sap_hcm_adapter.egress_guard, "pinned_request_sync", fake_pinned_request_sync
    )

    result = sap_hcm_adapter.SapHcmAdapter().execute(
        {
            "template_type": "sap_hcm_it0008",
            "item": {"id": "item-hcm", "entity_id": "1001"},
            "action_payload": {"sap_hcm": {"pernr": "1001"}},
            "idempotency_key": "idem-hcm",
        },
        {"base_url": "https://sap.example", "user": "hcm-user", "password": "secret"},
        dry_run=True,
    )

    assert result.ok is True
    assert calls[0]["method"] == "GET"
    assert calls[0]["headers"]["x-csrf-token"] == "Fetch"
    assert calls[1]["method"] == "POST"
    assert calls[1]["headers"]["x-csrf-token"] == "csrf-123"


def test_sap_hcm_adapter_live_fetches_csrf_before_post(monkeypatch):
    from app.services.adapters import sap_hcm_adapter

    class SapResponse:
        def __init__(
            self,
            status_code: int,
            *,
            headers: dict | None = None,
            body: dict | None = None,
        ):
            self.status_code = status_code
            self.headers = headers or {}
            self._body = body or {}
            self.text = "ok"

        def json(self):
            return self._body

    calls = []

    def fake_pinned_request_sync(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if method == "GET":
            return SapResponse(200, headers={"x-csrf-token": "csrf-123"})
        return SapResponse(201, body={"d": {"id": "sap-writeback-1"}})

    monkeypatch.setattr(
        sap_hcm_adapter.egress_guard, "pinned_request_sync", fake_pinned_request_sync
    )

    result = sap_hcm_adapter.SapHcmAdapter().execute(
        {
            "template_type": "sap_hcm_it0008",
            "item": {"id": "item-hcm", "entity_id": "1001"},
            "action_payload": {"sap_hcm": {"pernr": "1001"}},
            "idempotency_key": "idem-hcm",
        },
        {"base_url": "https://sap.example", "user": "hcm-user", "password": "secret"},
        dry_run=False,
    )

    assert result.ok is True
    assert result.data["status_code"] == 201
    assert calls[0]["method"] == "GET"
    assert calls[0]["headers"]["x-csrf-token"] == "Fetch"
    assert calls[1]["method"] == "POST"
    assert calls[1]["headers"]["x-csrf-token"] == "csrf-123"
    assert "DRY_RUN" not in calls[1]["json_body"]


def test_replicon_adapter_posts_realistic_writeback_payload(monkeypatch):
    from app.services.adapters import replicon_adapter

    class RepliconResponse:
        status_code = 202
        text = "ok"

        def json(self):
            return {"remote_id": "replicon-writeback-1", "status": "accepted"}

    calls = []

    def fake_pinned_request_sync(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return RepliconResponse()

    monkeypatch.setattr(
        replicon_adapter.egress_guard, "pinned_request_sync", fake_pinned_request_sync
    )

    result = replicon_adapter.RepliconAdapter().execute(
        {
            "template_id": "prepare_billing_review",
            "template_type": "prepare_billing_review",
            "item_id": "item-rep",
            "entity_id": "project-1",
            "action_kind": "billing_review",
            "action_payload": {"replicon": {"project": "Omega Norte"}},
            "idempotency_key": "idem-rep",
        },
        {
            "base_url": "https://replicon.example",
            "writeback_path": "/api/omega/writeback",
            "token": "replicon-token",
        },
        dry_run=False,
    )

    assert result.ok is True
    assert result.status == "executed"
    assert result.data["external_id"] == "replicon-writeback-1"
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://replicon.example/api/omega/writeback"
    assert call["headers"]["Authorization"] == "Bearer replicon-token"
    assert call["headers"]["Idempotency-Key"] == "idem-rep"
    assert call["headers"]["X-Omega-Dry-Run"] == "false"
    assert call["json_body"]["replicon"]["project"] == "Omega Norte"
    assert call["json_body"]["dry_run"] is False


def test_sap_hcm_adapter_aborts_when_csrf_fetch_fails(monkeypatch):
    from app.services.adapters import sap_hcm_adapter

    class SapResponse:
        status_code = 403
        headers = {}
        text = "forbidden"

        def json(self):
            return {"error": "forbidden"}

    calls = []

    def fake_pinned_request_sync(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return SapResponse()

    monkeypatch.setattr(
        sap_hcm_adapter.egress_guard, "pinned_request_sync", fake_pinned_request_sync
    )

    with pytest.raises(RuntimeError, match="CSRF token fetch failed with HTTP 403"):
        sap_hcm_adapter.SapHcmAdapter().execute(
            {
                "template_type": "sap_hcm_it0008",
                "item": {"id": "item-hcm", "entity_id": "1001"},
                "action_payload": {"sap_hcm": {"pernr": "1001"}},
            },
            {"base_url": "https://sap.example", "token": "token"},
            dry_run=False,
        )

    assert [call["method"] for call in calls] == ["GET"]


@pytest.mark.asyncio
async def test_thresholds_are_workspace_scoped_and_audited():
    row = {
        "id": 5,
        "cartridge_id": "replicon",
        "anomaly_type": "low_margin",
        "metric": "margen_bruto_pct",
        "warning_value": 20,
        "critical_value": 0,
        "currency": "USD",
        "enabled": True,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
        "updated_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = row
    mock_pool.fetch.return_value = [row]

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        created = await control_room_service.upsert_threshold(
            {
                "cartridge_id": "replicon",
                "anomaly_type": "low_margin",
                "metric": "margen_bruto_pct",
                "warning_value": 20,
                "critical_value": 0,
            },
            USER,
        )
        listed = await control_room_service.list_thresholds(USER)

    assert created["threshold"]["id"] == 5
    assert listed["thresholds"][0]["cartridge_id"] == "replicon"
    assert listed["summary"]["active"] == 1
    assert listed["summary"]["by_cartridge"] == {"replicon": 1}
    sql, *args = mock_pool.fetchrow.call_args.args
    assert "workspace_id" in sql
    assert "ON CONFLICT (workspace_id, cartridge_id, anomaly_type, metric)" in sql
    assert "workspace-A" in args
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.threshold.upsert"


@pytest.mark.asyncio
async def test_threshold_upsert_rejects_denied_or_unknown_cartridge():
    with pytest.raises(HTTPException) as unknown_exc:
        await control_room_service.upsert_threshold(
            {
                "cartridge_id": "unknown",
                "anomaly_type": "low_margin",
                "metric": "margen_bruto_pct",
            },
            USER,
        )

    with pytest.raises(HTTPException) as denied_exc:
        await control_room_service.upsert_threshold(
            {
                "cartridge_id": "platform",
                "anomaly_type": "freshness",
                "metric": "age_minutes",
            },
            USER,
        )

    assert unknown_exc.value.status_code == 400
    assert denied_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_lessons_is_workspace_scoped_and_returns_summary():
    lesson_row = {
        "id": 8,
        "item_id": "item-1",
        "cartridge_id": "replicon",
        "anomaly_type": "low_margin",
        "rule": "Si margen cae por debajo del umbral, pedir revision de billing antes del refresh.",
        "source_decision_id": 77,
        "confidence": 0.9,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [lesson_row]

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_persisted_business_items",
            new=AsyncMock(
                return_value=[
                    {
                        **_observed_anomaly_fields("item-1"),
                        "id": "item-1",
                        "kind": "intelligence_signal",
                        "source_dataset": "pnl_mensual",
                        "evidence_refs": ["pnl_mensual:item-1"],
                    }
                ]
            ),
        ),
    ):
        result = await control_room_service.list_lessons(
            USER,
            cartridge_id="replicon",
            anomaly_type="low_margin",
        )

    assert result["lessons"][0]["cartridge_id"] == "replicon"
    assert result["summary"]["total"] == 1
    assert result["summary"]["top_patterns"][0]["avg_confidence"] == 0.9
    sql, *args = mock_pool.fetch.await_args_list[0].args
    assert "workspace_id = $1" in sql
    assert "cartridge_id = $2" in sql
    assert "anomaly_type = $3" in sql
    assert args[:3] == ["workspace-A", "replicon", "low_margin"]


@pytest.mark.asyncio
async def test_approve_persists_lessons_to_lessons_table():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    action = {
        "id": 100,
        "decision_id": 42,
        "action_text": "approved",
        "note": "ok",
        "actor": "ops@example.com",
        "ts": datetime(2026, 5, 20, 10, 1, 0),
    }
    decision_item = control_room_service._with_omega(
        {**anomaly, "decision_id": 42, "status": "decision_created"}
    )
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_approval_fetchrows(decision_item, action)
    )
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=decision_item),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ),
    ):
        await control_room_service.approve_anomaly(
            anomaly["id"],
            USER,
            decision_id=42,
            fetcher=sample_fetcher,
        )

    assert any(
        "INSERT INTO control_room_lessons" in call.args[0]
        for call in mock_pool.execute.call_args_list
    )


@pytest.mark.asyncio
async def test_approve_anomaly_requires_workspace_decision_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    action = {
        "id": 100,
        "decision_id": 42,
        "action_text": "approved",
        "note": "ok",
        "actor": "ops@example.com",
        "ts": datetime(2026, 5, 20, 10, 1, 0),
    }
    decision_item = control_room_service._with_omega(
        {**anomaly, "decision_id": 42, "status": "decision_created"}
    )
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_approval_fetchrows(decision_item, action)
    )
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                    {
                        "cartridge_id": "sap_s4hana",
                        "installation_status": "ready",
                        "label": "SAP S/4HANA",
                    },
                    {
                        "cartridge_id": "sap_successfactors",
                        "installation_status": "ready",
                        "label": "SuccessFactors",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=decision_item),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.approve_anomaly(
            anomaly["id"],
            USER,
            decision_id=42,
            fetcher=sample_fetcher,
        )

    assert result["approved"] is True
    assert result["decision_id"] == 42
    assert result["action"]["ts"] == "2026-05-20T10:01:00"
    visible_sql, decision_id, workspace_id = mock_pool.fetchrow.call_args_list[0].args
    assert "workspace_id = $2" in visible_sql
    assert decision_id == 42
    assert workspace_id == "workspace-A"
    link_args = next(
        call.args
        for call in mock_pool.fetchrow.call_args_list
        if "RETURNING item_id" in call.args[0]
    )
    link_sql = link_args[0]
    assert "owner_user_id IS NOT DISTINCT FROM $5" in link_sql
    assert "RETURNING item_id" in link_sql
    assert "decision_eligibility_provenance" in link_args[4]
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.approve"
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_item"


@pytest.mark.asyncio
async def test_dismiss_item_persists_state_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.side_effect = [
        None,
        {"item_id": anomaly["id"], "status": "open"},
    ]

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.dismiss_item(
            anomaly["id"],
            USER,
            reason="false positive",
            fetcher=sample_fetcher,
        )

    assert result["dismissed"] is True
    assert result["item"]["status"] == "dismissed"
    assert any(
        "UPDATE control_room_items" in call.args[0]
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.dismiss"


@pytest.mark.asyncio
async def test_reopen_item_reopens_clean_dismissal_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.return_value = None

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
        patch.object(
            control_room_service,
            "lock_authoritative_business_item",
            new=AsyncMock(
                return_value={
                    "status": "dismissed",
                    "decision_id": None,
                    "selected_option_id": None,
                    "execution_status": "not_started",
                    "metadata": {},
                }
            ),
        ),
    ):
        result = await control_room_service.reopen_item(
            anomaly["id"],
            USER,
            reason="e2e reset",
            fetcher=sample_fetcher,
        )

    assert result["reopened"] is True
    assert result["item"]["status"] == "open"
    assert result["item"]["decision_id"] is None
    assert any(
        "UPDATE control_room_items" in call.args[0]
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.reopen"


@pytest.mark.asyncio
async def test_acknowledge_alert_persists_alert_state_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.return_value = None

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.acknowledge_alert(
            anomaly["id"],
            USER,
            body={"note": "triage started"},
            fetcher=sample_fetcher,
        )

    assert result["item"]["status"] == "in_review"
    assert result["item"]["alert_state"]["state"] == "acknowledged"
    assert result["alert"]["status"] == "acknowledged"
    assert result["alert"]["delivery"]["status"] == "acknowledged"
    metadata_payloads = [
        arg
        for call in mock_pool.execute.call_args_list
        for arg in call.args
        if isinstance(arg, str) and "alert_state" in arg
    ]
    assert any("acknowledged" in payload for payload in metadata_payloads)
    assert any(
        len(call.args) > 4 and call.args[4] == "alert_acknowledged"
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.alert.acknowledge"
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_alert"


@pytest.mark.asyncio
async def test_snooze_and_assign_alert_update_delivery_contract():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.return_value = None

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        snoozed = await control_room_service.snooze_alert(
            anomaly["id"],
            USER,
            body={"hours": 24},
            fetcher=sample_fetcher,
        )
        assigned = await control_room_service.assign_alert(
            anomaly["id"],
            USER,
            body={"owner_email": "owner@example.com"},
            fetcher=sample_fetcher,
        )

    assert snoozed["alert"]["status"] == "snoozed"
    assert snoozed["alert"]["push_ready"] is False
    assert snoozed["alert"]["delivery"]["status"] == "snoozed"
    assert snoozed["alert"]["snoozed_until"]
    assert assigned["alert"]["status"] == "assigned"
    assert assigned["alert"]["owner"] == "owner@example.com"
    assert assigned["alert"]["delivery"]["status"] == "assigned"
    assert audit_event.await_count == 2
    assert [call.kwargs["action"] for call in audit_event.await_args_list] == [
        "control_room.alert.snooze",
        "control_room.alert.assign",
    ]


@pytest.mark.asyncio
async def test_false_positive_alert_dismisses_item_and_removes_alert():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))[
        "anomalies"
    ][0]
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.side_effect = [
        None,
        {"item_id": anomaly["id"], "status": "open"},
    ]

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.mark_alert_false_positive(
            anomaly["id"],
            USER,
            body={"reason": "validated duplicate signal"},
            fetcher=sample_fetcher,
        )

    assert result["item"]["status"] == "dismissed"
    assert result["item"]["alert_state"]["state"] == "false_positive"
    assert result["alert"] is None
    assert any(
        len(call.args) > 4 and call.args[4] == "alert_false_positive"
        for call in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert (
        audit_event.await_args.kwargs["action"] == "control_room.alert.false_positive"
    )
    assert audit_event.await_args.kwargs["critical"] is True
    assert audit_event.await_args.kwargs["connection"] is mock_pool


# --- Fase 3 P0: Workforce Trends (fuente unica) ------------------------------
