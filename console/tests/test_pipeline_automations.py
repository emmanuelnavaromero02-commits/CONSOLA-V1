from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, get_args, get_origin
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

from app.dependencies import require_authenticated
from app.route_surface_registry import classify_route_surface
from app.routers import pipeline_automations as router_module
from app.schemas.pipeline_automations import Automation, AutomationsResponse
from app.services import pipeline_automations as automations


TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WORKSPACE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ADMIN = {
    "id": 1,
    "role": "super_admin",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["*"],
}
ANALYST = {
    "id": 5,
    "role": "analyst",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_b1", "sap_successfactors"],
}
ROWS = [
    {"cartridge_id": "platform", "dag_id": "entity_scheduler", "description": "Meta-scheduler", "trigger": "scheduled"},
    {"cartridge_id": "sap_b1", "dag_id": "sap_b1_refresh", "description": "Refresco SAP B1", "trigger": "scheduled"},
    {"cartridge_id": "sap_successfactors", "dag_id": "sap_sf_extract", "description": "Extracción SF", "trigger": "on-demand"},
    {"cartridge_id": "sap_successfactors", "dag_id": "sap_sf_rebuild", "description": "", "trigger": "on-demand"},
    {"cartridge_id": "sap_b1", "dag_id": "sap_b1_orphan", "description": "Sin despliegue", "trigger": "on-demand"},
]
_WRITES = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP)\b|FOR\s+UPDATE", re.I)


class CatalogConn:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        runs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows
        self.runs = runs or []
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        statement = " ".join(query.split())
        assert not _WRITES.search(statement)
        self.statements.append((statement, args))
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        statement = " ".join(query.split())
        assert not _WRITES.search(statement)
        self.statements.append((statement, args))
        if "FROM pipeline_runs" in statement:
            assert args[:2] == (TENANT_ID, WORKSPACE_ID)
            dags = set(args[2])
            runs = [run for run in self.runs if run["dag_id"] in dags]
            if "count(*)" in statement:
                counts: dict[str, int] = {}
                for run in runs:
                    if run["status"].lower() in args[3]:
                        counts[run["dag_id"]] = counts.get(run["dag_id"], 0) + 1
                return [{"dag_id": dag, "active_runs": count} for dag, count in counts.items()]
            latest: dict[str, dict[str, Any]] = {}
            for run in sorted(runs, key=lambda run: run["started_at"], reverse=True):
                latest.setdefault(run["dag_id"], run)
            return list(latest.values())
        cartridges = args[0]
        return [row for row in self.rows if cartridges is None or row["cartridge_id"] in cartridges]


def _airflow(dags: dict[str, dict[str, Any]], fail: bool = False):
    calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(tool: str, args: dict[str, Any], _user: Any) -> Any:
        calls.append((tool, args))
        if tool == "airflow_list_dags":
            if fail:
                raise RuntimeError("airflow down")
            return {"dags": [{"dag_id": dag_id, **values} for dag_id, values in dags.items()]}
        raise AssertionError(f"unexpected tool {tool}")

    return invoke, calls


async def _list(user: dict[str, Any], conn: CatalogConn, invoke) -> AutomationsResponse:
    with patch.object(automations.auth, "pool", new=AsyncMock(return_value=conn)):
        return await automations.list_automations(user, invoke=invoke)


@pytest.mark.parametrize(
    ("expression", "expected"),
    (
        ("*/5 * * * *", "Cada 5 minutos"),
        ("0 */2 * * *", "Cada 2 horas, en el minuto 0"),
        ("15 * * * *", "Cada hora, en el minuto 15"),
        ("30 9 * * *", "Todos los días a las 09:30 (hora de Airflow)"),
        ("0 17 * * FRI", "Cada viernes a las 17:00 (hora de Airflow)"),
        ("0 9 * * 1", "Cada lunes a las 09:00 (hora de Airflow)"),
        ("@daily", "Todos los días a las 00:00 (hora de Airflow)"),
        ("1 2 3 4 5", "Programación cron: 1 2 3 4 5"),
        ("", None),
    ),
)
def test_cron_descriptions_are_spanish_or_show_the_real_expression(expression, expected):
    assert automations.describe_cron(expression) == expected


def test_schedule_payload_shapes():
    assert automations.describe_schedule({"__type": "CronExpression", "value": "*/10 * * * *"}) == "Cada 10 minutos"
    assert automations.describe_schedule({"__type": "TimeDelta", "days": 0, "seconds": 3600}) == "Cada 1 horas"
    assert automations.describe_schedule(None) is None
    assert automations.describe_schedule("None") is None


@pytest.mark.asyncio
async def test_states_and_notes_come_from_airflow_and_the_registered_trigger():
    invoke, calls = _airflow(
        {
            "entity_scheduler": {
                "is_paused": False,
                "is_active": True,
                "schedule_kind": "scheduled",
                "schedule_interval": {"__type": "CronExpression", "value": "*/5 * * * *"},
            },
            "sap_b1_refresh": {"is_paused": True, "is_active": True, "schedule_kind": "scheduled"},
            "sap_sf_extract": {"is_paused": True, "is_active": True, "schedule_kind": "manual"},
            "sap_sf_rebuild": {"is_paused": True, "is_active": False},
        }
    )
    started = datetime(2026, 9, 26, 10, tzinfo=UTC)
    runs = [
        {"dag_id": "sap_sf_extract", "status": "queued", "started_at": started, "finished_at": None},
        {"dag_id": "sap_sf_extract", "status": "RUNNING", "started_at": started - timedelta(minutes=5), "finished_at": None},
        {"dag_id": "sap_sf_extract", "status": "success", "started_at": started - timedelta(days=1), "finished_at": started - timedelta(days=1)},
    ]
    conn = CatalogConn(ROWS, runs)
    response = await _list(ADMIN, conn, invoke)
    by_id = {automation.dag_id: automation for automation in response.automations}

    assert response.airflow_available is True
    scheduler = by_id["entity_scheduler"]
    assert (scheduler.state, scheduler.kind, scheduler.cartridge_id) == ("active", "scheduled", None)
    assert scheduler.schedule_description == "Cada 5 minutos"
    assert scheduler.runs_known is False
    assert scheduler.active_runs is None and scheduler.last_run is None
    refresh = by_id["sap_b1_refresh"]
    assert refresh.state == "paused_by_operator"
    assert refresh.state_note_es == (
        "En pausa por un operador de plataforma; no se ejecutará en su horario"
    )
    assert refresh.runs_known is True
    assert refresh.active_runs == 0 and refresh.last_run is None
    extract = by_id["sap_sf_extract"]
    assert extract.state == "paused_manual"
    assert extract.state_note_es == "Se activa automáticamente al pulsar Extraer"
    assert extract.active_runs == 2
    assert extract.last_run.status == "queued"
    assert extract.last_run.started_at == started
    rebuild = by_id["sap_sf_rebuild"]
    assert rebuild.state == "unavailable" and rebuild.label == "sap_sf_rebuild"
    orphan = by_id["sap_b1_orphan"]
    assert orphan.state == "unavailable"
    assert orphan.state_note_es.startswith("No aparece en Airflow")
    assert [tool for tool, _args in calls] == ["airflow_list_dags"]
    statements = [statement for statement, _args in conn.statements]
    assert statements[1] == automations.READ_ONLY_TRANSACTION_SQL
    run_args = [args for statement, args in conn.statements if "FROM pipeline_runs" in statement]
    assert run_args and all("entity_scheduler" not in args[2] for args in run_args)


@pytest.mark.asyncio
async def test_airflow_schedule_kind_overrides_the_registered_trigger():
    invoke, _calls = _airflow(
        {"sap_sf_extract": {"is_paused": True, "is_active": True, "schedule_kind": "scheduled"}}
    )
    response = await _list(ADMIN, CatalogConn([ROWS[2]]), invoke)
    (automation,) = response.automations
    assert automation.kind == "scheduled" and automation.state == "paused_by_operator"


@pytest.mark.asyncio
async def test_non_admins_see_only_their_data_sources_without_platform_dags():
    invoke, _calls = _airflow({})
    conn = CatalogConn(ROWS)
    response = await _list(ANALYST, conn, invoke)

    args = next(args for statement, args in conn.statements if "FROM cartridge_dags" in statement)
    assert args[0] == ["sap_b1", "sap_successfactors"]
    assert {automation.cartridge_id for automation in response.automations} == {
        "sap_b1",
        "sap_successfactors",
    }
    assert "entity_scheduler" not in {automation.dag_id for automation in response.automations}

    empty = CatalogConn(ROWS)
    none = await _list({**ANALYST, "allowed_cartridges": []}, empty, invoke)
    assert none.automations == [] and empty.statements == []


@pytest.mark.asyncio
async def test_unreachable_airflow_is_reported_without_guessing_runs():
    invoke, calls = _airflow({}, fail=True)
    response = await _list(ADMIN, CatalogConn(ROWS), invoke)
    assert response.airflow_available is False
    assert {automation.state for automation in response.automations} == {"unavailable"}
    platform = [a for a in response.automations if a.cartridge_id is None]
    assert platform and all(a.active_runs is None and not a.runs_known for a in platform)
    assert [tool for tool, _args in calls] == ["airflow_list_dags"]


def test_response_models_are_strict():
    for model in (AutomationsResponse, Automation):
        assert model.model_config.get("extra") == "forbid"
        for field in model.model_fields.values():
            nodes = [field.annotation, *get_args(field.annotation)]
            assert Any not in nodes
            assert all(get_origin(node) is not dict for node in nodes)


def _client(user: dict | None) -> httpx.AsyncClient:
    app = FastAPI()
    if user is not None:

        @app.middleware("http")
        async def _inject(request: Request, call_next):
            request.state.user = user
            return await call_next(request)

        app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(router_module.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_route_is_read_only_and_requires_pipelines_read():
    listing = AsyncMock(
        return_value=AutomationsResponse(
            checked_at=datetime.now(UTC),
            airflow_available=True,
            automations=[],
        )
    )
    with patch.object(router_module, "list_automations", listing):
        async with _client(None) as client:
            assert (await client.get("/api/pipelines/automations")).status_code == 401
        async with _client({**ANALYST, "role": "workspace_user"}) as client:
            assert (await client.get("/api/pipelines/automations")).status_code == 403
        async with _client(ANALYST) as client:
            ok = await client.get("/api/pipelines/automations")
            assert ok.status_code == 200
            assert (await client.post("/api/pipelines/automations")).status_code == 405
    assert ok.json()["schema_version"] == "pipeline-automations/v1"
    methods = {method for route in router_module.router.routes for method in route.methods}
    assert methods == {"GET"}


def test_prefix_is_registered_for_rbac_and_the_route_surface():
    from app import main

    assert classify_route_surface("/api/pipelines/automations") == "frontend"
    assert main._uses_rbac_dependency("/api/pipelines/automations") is True
