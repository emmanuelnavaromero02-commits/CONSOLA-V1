from __future__ import annotations

import pytest

from app.domains.pipeline import orchestrator_health


@pytest.fixture(autouse=True)
def _clear_cache():
    orchestrator_health.reset_orchestrator_status_cache()
    yield
    orchestrator_health.reset_orchestrator_status_cache()


def test_sync_extract_all_dag_id_prefers_overrides_and_normalizes():
    overrides = {"sap_successfactors": "sap_successfactors_extract_all"}
    assert (
        orchestrator_health.sync_extract_all_dag_id("sap_successfactors", overrides)
        == "sap_successfactors_extract_all"
    )
    assert (
        orchestrator_health.sync_extract_all_dag_id("sap-b1", {})
        == "sap_b1_extract_all"
    )


@pytest.mark.anyio
async def test_sync_orchestrator_status_reports_describe_result():
    async def invoke(server, tool, args, *, user):
        assert (server, tool) == ("infra", "airflow_describe_dag")
        assert args == {"dag_id": "sap_successfactors_extract_all"}
        return {
            "dag_id": args["dag_id"],
            "found": True,
            "is_paused": True,
            "scheduler_healthy": False,
        }

    status = await orchestrator_health.sync_orchestrator_status(
        cartridge="sap_successfactors",
        dag_id="sap_successfactors_extract_all",
        invoke=invoke,
        user={"sub": "user-1"},
    )

    assert status == {
        "airflow_available": True,
        "scheduler_healthy": False,
        "dag_paused": True,
    }


@pytest.mark.anyio
async def test_sync_orchestrator_status_missing_dag_leaves_pause_unknown():
    async def invoke(server, tool, args, *, user):
        return {"dag_id": args["dag_id"], "found": False, "runs": []}

    status = await orchestrator_health.sync_orchestrator_status(
        cartridge="banxico",
        dag_id="banxico_extract_all",
        invoke=invoke,
        user=None,
    )

    assert status == {
        "airflow_available": True,
        "scheduler_healthy": None,
        "dag_paused": None,
    }


@pytest.mark.anyio
async def test_sync_orchestrator_status_failure_reports_unavailable():
    async def invoke(server, tool, args, *, user):
        raise RuntimeError("mcp down")

    status = await orchestrator_health.sync_orchestrator_status(
        cartridge="sap_successfactors",
        dag_id="sap_successfactors_extract_all",
        invoke=invoke,
        user=None,
    )

    assert status == {
        "airflow_available": False,
        "scheduler_healthy": None,
        "dag_paused": None,
    }


@pytest.mark.anyio
async def test_sync_orchestrator_status_caches_per_cartridge():
    calls = []
    clock = {"value": 100.0}

    async def invoke(server, tool, args, *, user):
        calls.append(args["dag_id"])
        return {"dag_id": args["dag_id"], "found": True, "is_paused": False}

    async def fetch():
        return await orchestrator_health.sync_orchestrator_status(
            cartridge="sap_successfactors",
            dag_id="sap_successfactors_extract_all",
            invoke=invoke,
            user=None,
            now=lambda: clock["value"],
        )

    first = await fetch()
    second = await fetch()
    clock["value"] += orchestrator_health.ORCHESTRATOR_CACHE_TTL_SECONDS + 1
    third = await fetch()

    assert first == second == third
    assert len(calls) == 2
