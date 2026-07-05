from __future__ import annotations

import pytest

from app.domains.pipeline.airflow_trigger import trigger_airflow_extract_dag


@pytest.mark.asyncio
async def test_trigger_airflow_extract_dag_returns_success_without_retry():
    calls: list[dict] = []

    async def airflow_trigger(args, _user):
        calls.append(args)
        return {"dag_run_id": "run-1"}

    async def sleep(_seconds):
        raise AssertionError("sleep should not be called")

    result = await trigger_airflow_extract_dag(
        "sap_successfactors_extract",
        {"entity": "User"},
        {"id": 1},
        scoped_conf_builder=lambda conf, _user: {**conf, "tenant_id": "t1"},
        airflow_trigger=airflow_trigger,
        transient_error_checker=lambda _error: True,
        sleep=sleep,
    )

    assert result == {"dag_run_id": "run-1"}
    assert calls == [
        {
            "dag_id": "sap_successfactors_extract",
            "conf": {"entity": "User", "tenant_id": "t1"},
        }
    ]


@pytest.mark.asyncio
async def test_trigger_airflow_extract_dag_retries_transient_errors():
    calls = 0
    sleeps: list[float] = []

    async def airflow_trigger(_args, _user):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"error": "Connection refused"}
        return {"dag_run_id": "run-2"}

    async def sleep(seconds):
        sleeps.append(seconds)

    result = await trigger_airflow_extract_dag(
        "sap_successfactors_extract",
        {"entity": "User"},
        {"id": 1},
        "idempotent-run",
        scoped_conf_builder=lambda conf, _user: dict(conf),
        airflow_trigger=airflow_trigger,
        transient_error_checker=lambda error: "Connection" in error,
        sleep=sleep,
    )

    assert result == {"dag_run_id": "run-2"}
    assert calls == 2
    assert sleeps == [4]
