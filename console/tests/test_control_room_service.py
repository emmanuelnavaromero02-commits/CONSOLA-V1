from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
    "allowed_cartridges": ["sap_hcm", "sap_s4hana", "sap_successfactors", "replicon"],
}


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
    "consultor_timesheet_semanal": [],
    "pnl_mensual": [],
    "analytic_skill_gap_by_manager": [],
}

for _source in control_room_service._all_sources():  # noqa: SLF001 - registry contract test fixture
    SAMPLE_ROWS.setdefault(_source.dataset, [])


async def sample_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
    return SAMPLE_ROWS[dataset]


class _FakeDatasetResponse:
    status_code = 200

    def json(self):
        return {
            "code": "source_files_missing",
            "error": "No hay archivos Parquet para la fuente seleccionada.",
        }


class _FakeDatasetClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def post(self, *_args, **_kwargs):
        return _FakeDatasetResponse()


@pytest.mark.asyncio
async def test_query_dataset_rows_treats_refinement_error_payload_as_unavailable(monkeypatch):
    monkeypatch.setattr(control_room_service.httpx, "AsyncClient", _FakeDatasetClient)

    with pytest.raises(HTTPException) as exc:
        await control_room_service.query_dataset_rows("pnl_mensual", USER)

    assert exc.value.status_code == 503
    assert "No hay archivos Parquet" in str(exc.value.detail)


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
        source["dataset"]: {key: source[key] for key in ("cartridge", "status", "count")}
        for source in result["sources"]
    }
    assert source_summary["employees_anomalies"] == {"cartridge": "sap_hcm", "status": "ok", "count": 1}
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
    expected_empty = {source.dataset for source in control_room_service._all_sources()} - anomaly_sources  # noqa: SLF001
    actual_empty = {item["dataset"] for item in result["sources"] if item["status"] == "empty"}
    assert expected_empty <= actual_empty


@pytest.mark.asyncio
async def test_list_anomalies_marks_missing_dataset_unavailable_without_failing():
    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "business_partner_anomalies":
            raise HTTPException(404, "dataset not found")
        return SAMPLE_ROWS[dataset]

    result = await control_room_service.list_anomalies(USER, fetcher=fetcher)

    assert len(result["anomalies"]) == 2
    failed_source = next(item for item in result["sources"] if item["dataset"] == "business_partner_anomalies")
    assert failed_source["status"] == "missing"
    assert failed_source["count"] == 0
    assert "dataset not found" in failed_source["error"]


@pytest.mark.asyncio
async def test_dashboard_exposes_real_financial_metrics_from_available_sources():
    async def finance_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
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
        rows["open_sales_orders"] = [{"customer_code": "C-1", "open_value": 12000, "open_orders": 3, "oldest_age_days": 75}]
        rows["purchase_spend_by_supplier"] = [{"supplier_code": "S-1", "total_spend": 15000}]
        return rows[dataset]

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_s4hana", "installation_status": "ready", "label": "SAP S/4HANA"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
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
    return rows[dataset]


async def threshold_margin_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
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
    return rows[dataset]


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
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=finance_fetcher)

    pnl_item = next(item for item in result["items"] if item["anomaly_type"] == "low_margin")
    assert pnl_item["impact_status"] == "ok"
    assert pnl_item["impact_estimate"] == 21000
    assert pnl_item["impact_currency"] == "USD"
    assert pnl_item["priority_score"] > 0
    hcm_item = next(item for item in result["items"] if item["source_dataset"] == "employees_anomalies")
    assert hcm_item["impact_status"] == "unavailable"
    assert hcm_item["impact_estimate"] is None


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
            new=AsyncMock(return_value=[
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=threshold_margin_fetcher)

    item = next(item for item in result["items"] if item["anomaly_type"] == "low_margin")
    assert item["severity"] == "critical"
    assert item["threshold_state"] == "critical"
    assert item["thresholds_applied"][0]["source"] == "workspace"
    assert item["thresholds_applied"][0]["warning_value"] == 30
    assert item["priority_score"] >= 80
    assert result["summary"]["thresholds"]["active"] == 1
    assert result["summary"]["thresholds"]["items_with_thresholds"] >= 1


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
            new=AsyncMock(return_value=[
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=threshold_margin_fetcher)

    assert not any(item["anomaly_type"] == "low_margin" for item in result["items"])
    assert result["summary"]["thresholds"]["active"] == 1


@pytest.mark.asyncio
async def test_dashboard_surfaces_persisted_lessons_by_pattern():
    lesson_row = {
        "id": 31,
        "item_id": "historic-item",
        "cartridge_id": "sap_hcm",
        "anomaly_type": "terminated_but_active",
        "rule": "Cuando un empleado terminado sigue activo, bloquear acceso antes del cierre de nomina.",
        "source_decision_id": 42,
        "confidence": 0.86,
        "metadata": {"source_dataset": "employees_anomalies"},
        "created_at": datetime(2026, 5, 21, 9, 30, 0),
    }
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=[[], [], [lesson_row]])
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    item = next(item for item in result["items"] if item["anomaly_type"] == "terminated_but_active")
    assert item["lesson_count"] == 1
    assert item["related_lessons"][0]["source_decision_id"] == 42
    assert "bloquear acceso" in item["omega"]["lessons"]["rules"][0]
    assert result["summary"]["lessons"]["total"] == 1
    assert result["summary"]["lessons"]["by_cartridge"] == {"sap_hcm": 1}
    assert result["summary"]["lessons"]["top_patterns"][0]["anomaly_type"] == "terminated_but_active"


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
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    cartridge_ids = {item["id"] for item in result["cartridges"]}
    assert {"sap_hcm", "replicon"} <= cartridge_ids
    replicon = next(item for item in result["cartridges"] if item["id"] == "replicon")
    assert replicon["source_status"] == "empty"
    assert any(item["kind"] == "source_state" and item["cartridge"] == "replicon" for item in result["items"])
    assert result["omega_steps"][0]["label"] == "Senales"


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
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "sap_s4hana", "installation_status": "ready", "label": "SAP S/4HANA"},
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "label": "SAP SuccessFactors",
                },
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
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
    assert all(item["connector_id"] in USER["allowed_cartridges"] for item in result["cartridges"])
    domain_labels = {domain["label"] for domain in result["domains"] if domain["modules"]}
    assert {"Recursos Humanos", "Nomina", "Finanzas", "Presupuestos", "Compras", "Ventas", "Operacion"} <= domain_labels
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
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
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
        return SAMPLE_ROWS[dataset]

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_s4hana", "installation_status": "paused", "label": "SAP S/4HANA"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=fetcher)

    assert called == []
    assert result["cartridges"]
    assert all(item["active"] is False for item in result["cartridges"])
    assert {item["source_status"] for item in result["cartridges"]} == {"blocked"}
    assert result["summary"]["source_states"]["blocked"] == len(result["sources"])


@pytest.mark.asyncio
async def test_summary_counts_and_scopes_open_decisions_to_active_workspace():
    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = 5

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        result = await control_room_service.summary(USER, fetcher=sample_fetcher)

    assert result["total_anomalies"] == 3
    assert result["by_severity"] == {"critical": 1, "high": 1, "medium": 1, "low": 0}
    assert result["by_cartridge"] == {"sap_hcm": 1, "sap_s4hana": 1, "sap_successfactors": 1}
    assert result["open_decisions"] == 5
    sql, workspace_id = mock_pool.fetchval.call_args[0]
    assert "workspace_id = $1" in sql
    assert workspace_id == "workspace-A"


@pytest.mark.asyncio
async def test_create_decision_writes_workspace_bitacora_and_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    decision_row = {
        "id": 42,
        "title": "Decision title",
        "workspace_id": "workspace-A",
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    action_row = {"id": 99, "decision_id": 42}
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=[None, decision_row, action_row])
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "sap_s4hana", "installation_status": "ready", "label": "SAP S/4HANA"},
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "label": "SuccessFactors",
                },
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        result = await control_room_service.create_decision_for_anomaly(
            anomaly["id"],
            USER,
            fetcher=sample_fetcher,
        )

    assert result["decision"]["id"] == 42
    assert mock_pool.fetchrow.call_count == 3
    insert_sql = mock_pool.fetchrow.call_args_list[1].args[0]
    assert "INSERT INTO decisions" in insert_sql
    assert "workspace_id" in insert_sql
    assert mock_pool.fetchrow.call_args_list[1].args[-1] == "workspace-A"
    action_sql = mock_pool.fetchrow.call_args_list[2].args[0]
    assert "INSERT INTO decision_actions" in action_sql
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.decision.create"
    assert audit_event.await_args.kwargs["metadata"]["decision_id"] == 42
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_item"


@pytest.mark.asyncio
async def test_select_item_option_persists_metadata_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
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
    assert any('"selected_option_id": "exception"' in str(call.args) for call in mock_pool.execute.call_args_list)
    assert any("selected_option_id = $5" in call.args[0] for call in mock_pool.execute.call_args_list)
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.option.select"
    assert audit_event.await_args.kwargs["metadata"]["option_id"] == "exception"


@pytest.mark.asyncio
async def test_action_preview_and_dry_run_are_persisted_and_audited():
    item = (await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
        USER,
        fetcher=finance_fetcher,
        include_source_state_items=True,
        persist=False,
        use_catalog=False,
    ))["items"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0
    mock_pool.fetchrow = AsyncMock(side_effect=[
        None,
        {
            "id": 1,
            "workspace_id": "workspace-A",
            "item_id": item["id"],
            "template_id": "request_owner_review",
            "mode": "preview",
            "status": "generated",
            "payload": {},
            "result": {},
            "created_at": datetime(2026, 5, 20, 10, 0, 0),
            "completed_at": datetime(2026, 5, 20, 10, 0, 1),
        },
        None,
        {
            "id": 2,
            "workspace_id": "workspace-A",
            "item_id": item["id"],
            "template_id": "request_owner_review",
            "mode": "dry_run",
            "status": "validated",
            "payload": {},
            "result": {},
            "created_at": datetime(2026, 5, 20, 10, 1, 0),
            "completed_at": datetime(2026, 5, 20, 10, 1, 1),
        },
    ])

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        preview = await control_room_service.action_preview(item["id"], USER, fetcher=finance_fetcher)
        dry_run = await control_room_service.action_dry_run(item["id"], USER, fetcher=finance_fetcher)

    assert preview["execution"]["status"] == "generated"
    assert preview["item"]["execution_status"] == "preview_generated"
    assert dry_run["execution"]["status"] == "validated"
    assert dry_run["item"]["execution_status"] == "dry_run_validated"
    assert dry_run["result"]["external_write"] is False
    actions = [call.kwargs["action"] for call in audit_event.await_args_list]
    assert "control_room.action.preview" in actions
    assert "control_room.action.dry_run" in actions


@pytest.mark.asyncio
async def test_execute_live_is_blocked_by_default_and_audited(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    item = (await control_room_service._collect_items(  # noqa: SLF001 - targeted service unit test
        USER,
        fetcher=finance_fetcher,
        include_source_state_items=True,
        persist=False,
        use_catalog=False,
    ))["items"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0
    mock_pool.fetchrow = AsyncMock(side_effect=[
        None,
        {
            "id": 3,
            "workspace_id": "workspace-A",
            "item_id": item["id"],
            "template_id": "request_owner_review",
            "mode": "execute_live",
            "status": "blocked",
            "payload": {},
            "result": {},
            "created_at": datetime(2026, 5, 20, 10, 2, 0),
            "completed_at": datetime(2026, 5, 20, 10, 2, 1),
        },
    ])

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(item["id"], USER, fetcher=finance_fetcher)

    assert exc.value.status_code == 409
    assert audit_event.await_args.kwargs["action"] == "control_room.action.execute.blocked"


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
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
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
    sql, *args = mock_pool.fetchrow.call_args.args
    assert "workspace_id" in sql
    assert "ON CONFLICT (workspace_id, cartridge_id, anomaly_type, metric)" in sql
    assert "workspace-A" in args
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.threshold.upsert"


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

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        result = await control_room_service.list_lessons(
            USER,
            cartridge_id="replicon",
            anomaly_type="low_margin",
        )

    assert result["lessons"][0]["cartridge_id"] == "replicon"
    assert result["summary"]["total"] == 1
    assert result["summary"]["top_patterns"][0]["avg_confidence"] == 0.9
    sql, *args = mock_pool.fetch.call_args.args
    assert "workspace_id = $1" in sql
    assert "cartridge_id = $2" in sql
    assert "anomaly_type = $3" in sql
    assert args[:3] == ["workspace-A", "replicon", "low_margin"]


@pytest.mark.asyncio
async def test_approve_persists_lessons_to_lessons_table():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            {"id": 42},
            {
                "id": 100,
                "decision_id": 42,
                "action_text": "approved",
                "note": "ok",
                "actor": "ops@example.com",
                "ts": datetime(2026, 5, 20, 10, 1, 0),
            },
        ]
    )
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()),
    ):
        await control_room_service.approve_anomaly(
            anomaly["id"],
            USER,
            decision_id=42,
            fetcher=sample_fetcher,
        )

    assert any("INSERT INTO control_room_lessons" in call.args[0] for call in mock_pool.execute.call_args_list)


@pytest.mark.asyncio
async def test_approve_anomaly_requires_workspace_decision_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            None,
            {"id": 42},
            {
                "id": 100,
                "decision_id": 42,
                "action_text": "approved",
                "note": "ok",
                "actor": "ops@example.com",
                "ts": datetime(2026, 5, 20, 10, 1, 0),
            },
        ]
    )
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "sap_s4hana", "installation_status": "ready", "label": "SAP S/4HANA"},
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "label": "SuccessFactors",
                },
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
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
    visible_sql, decision_id, workspace_id = mock_pool.fetchrow.call_args_list[1].args
    assert "workspace_id = $2" in visible_sql
    assert decision_id == 42
    assert workspace_id == "workspace-A"
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.approve"
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_item"


@pytest.mark.asyncio
async def test_dismiss_item_persists_state_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        result = await control_room_service.dismiss_item(
            anomaly["id"],
            USER,
            reason="false positive",
            fetcher=sample_fetcher,
        )

    assert result["dismissed"] is True
    assert result["item"]["status"] == "dismissed"
    assert any("UPDATE control_room_items" in call.args[0] for call in mock_pool.execute.call_args_list)
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.dismiss"


@pytest.mark.asyncio
async def test_reopen_item_resets_terminal_state_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
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
    assert any("UPDATE control_room_items" in call.args[0] for call in mock_pool.execute.call_args_list)
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.reopen"
