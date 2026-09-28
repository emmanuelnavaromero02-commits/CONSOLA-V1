from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import HTTPException


USER = {
    "id": 17,
    "email": "studio-user@example.test",
    "role": "user",
    "workspace_role": "analyst",
    "active_tenant_id": "00000000-0000-0000-0000-000000000001",
    "active_workspace_id": "00000000-0000-0000-0000-000000000002",
    "allowed_cartridges": ["acme"],
}


@pytest.fixture()
def studio(monkeypatch):
    module = importlib.import_module("app.routers.studio")
    module.reset_dags_health_cache()

    async def get_cartridge(cartridge_id):
        return {"id": cartridge_id, "dags": []}

    monkeypatch.setattr(module.cartridge_service, "get_cartridge", get_cartridge)
    yield module
    module.reset_dags_health_cache()


def _invoke_factory(responses, calls):
    async def invoke(server, tool, args, *, user):
        calls.append((tool, dict(args)))
        result = responses[tool]
        if callable(result):
            result = result(args)
        if isinstance(result, Exception):
            raise result
        return result

    return invoke


@pytest.mark.asyncio
async def test_dags_health_reports_last_run_and_scheduler(studio, monkeypatch):
    calls: list = []
    responses = {
        "airflow_list_dags": {
            "dags": [
                {"dag_id": "acme_extract_all", "is_paused": False},
                {"dag_id": "acme_cleanup", "is_paused": True},
            ]
        },
        "airflow_describe_dag": {
            "dag_id": "acme_extract_all",
            "found": True,
            "is_paused": False,
            "scheduler_healthy": True,
        },
        "airflow_list_dag_runs": lambda args: {
            "dag_id": args["dag_id"],
            "found": True,
            "runs": [
                {
                    "dag_run_id": f"{args['dag_id']}__run",
                    "state": "success",
                    "start_date": "2026-09-27T10:00:00+00:00",
                    "end_date": "2026-09-27T10:05:30+00:00",
                }
            ],
        },
    }
    monkeypatch.setattr(studio.mcp_registry, "invoke", _invoke_factory(responses, calls))

    result = await studio.dags_health(cartridge="acme", user=USER)

    assert result.airflow_available is True
    assert result.scheduler_healthy is True
    assert result.total == 2
    first = result.dags[0]
    assert first.dag_id == "acme_cleanup"
    assert first.is_paused is True
    assert first.last_run.state == "success"
    assert first.last_run.duration_seconds == 330.0
    assert first.failed_task_id is None
    assert first.error_es is None
    describe_calls = [call for call in calls if call[0] == "airflow_describe_dag"]
    assert len(describe_calls) == 1


@pytest.mark.asyncio
async def test_dags_health_failed_run_names_task_and_spanish_error(studio, monkeypatch):
    calls: list = []
    responses = {
        "airflow_list_dags": {"dags": [{"dag_id": "acme_extract_all"}]},
        "airflow_describe_dag": {
            "dag_id": "acme_extract_all",
            "found": True,
            "scheduler_healthy": False,
        },
        "airflow_list_dag_runs": {
            "found": True,
            "runs": [
                {
                    "dag_run_id": "manual__1",
                    "state": "failed",
                    "start_date": "2026-09-27T10:00:00+00:00",
                    "end_date": "2026-09-27T10:00:20+00:00",
                }
            ],
        },
        "airflow_list_task_instances": {
            "found": True,
            "tasks": [
                {"task_id": "load", "state": "failed", "end_date": "2026-09-27T10:00:19"},
                {
                    "task_id": "extract_timeout_probe",
                    "state": "failed",
                    "end_date": "2026-09-27T10:00:10",
                },
                {"task_id": "prepare", "state": "success", "end_date": None},
            ],
        },
    }
    monkeypatch.setattr(studio.mcp_registry, "invoke", _invoke_factory(responses, calls))

    result = await studio.dags_health(cartridge="acme", user=USER)

    dag = result.dags[0]
    assert result.scheduler_healthy is False
    assert dag.last_run.state == "failed"
    assert dag.failed_task_id == "extract_timeout_probe"
    assert dag.error_es == "Tiempo de espera agotado al conectar con el origen"
    assert ("airflow_list_task_instances", {
        "dag_id": "acme_extract_all",
        "dag_run_id": "manual__1",
    }) in calls


@pytest.mark.asyncio
async def test_dags_health_airflow_down_degrades_to_unavailable(studio, monkeypatch):
    async def broken_invoke(server, tool, args, *, user):
        raise RuntimeError("airflow down")

    monkeypatch.setattr(studio.mcp_registry, "invoke", broken_invoke)

    result = await studio.dags_health(cartridge="acme", user=USER)

    assert result.airflow_available is False
    assert result.scheduler_healthy is None
    assert result.dags == []
    assert result.total == 0


@pytest.mark.asyncio
async def test_dags_health_caches_payload_per_cartridge(studio, monkeypatch):
    calls: list = []
    responses = {
        "airflow_list_dags": {"dags": [{"dag_id": "acme_extract_all"}]},
        "airflow_describe_dag": {"found": True, "scheduler_healthy": True},
        "airflow_list_dag_runs": {"found": True, "runs": []},
    }
    monkeypatch.setattr(studio.mcp_registry, "invoke", _invoke_factory(responses, calls))

    first = await studio.dags_health(cartridge="acme", user=USER)
    list_calls = len([call for call in calls if call[0] == "airflow_list_dags"])
    second = await studio.dags_health(cartridge="acme", user=USER)

    assert first == second
    assert list_calls == 1
    assert len([call for call in calls if call[0] == "airflow_list_dags"]) == 1


@pytest.mark.asyncio
async def test_dags_health_rejects_hidden_cartridge_before_downstream(studio, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("hidden cartridge must fail before downstream I/O")

    monkeypatch.setattr(studio.mcp_registry, "invoke", forbidden)
    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", forbidden)

    with pytest.raises(HTTPException) as exc:
        await studio.dags_health(cartridge="other", user=USER)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_dags_health_response_model_forbids_unknown_fields(studio):
    with pytest.raises(Exception):
        studio.StudioDagsHealthResponse(cartridge="acme", raw_logs="nope")


@pytest.mark.asyncio
async def test_dags_health_registered_only_dags_skip_airflow_lookups(studio, monkeypatch):
    calls: list = []

    async def get_cartridge(cartridge_id):
        return {"id": cartridge_id, "dags": [{"dag_id": "acme_manifest_only"}]}

    responses = {
        "airflow_list_dags": {"dags": []},
        "airflow_describe_dag": {"found": True, "scheduler_healthy": True},
        "airflow_list_dag_runs": {"found": True, "runs": []},
    }
    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(studio.mcp_registry, "invoke", _invoke_factory(responses, calls))

    result = await studio.dags_health(cartridge="acme", user=USER)

    assert result.total == 1
    assert result.dags[0].registered_only is True
    assert result.dags[0].last_run is None
    assert all(call[0] == "airflow_list_dags" for call in calls)
