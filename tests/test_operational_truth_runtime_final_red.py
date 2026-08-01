from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from airflow.dags import dataset_refresh_graph, dataset_refresh_idempotency
from airflow.dags.runtime_security_context import build_materialize_context
from console.app.services import agent_scheduler
from console.app.services.monitor_alert_policy import monitor_should_alert
from console.app.services.scheduled_runtime import _fire_in_window


KEY = "runtime-signing-key-that-is-long-and-isolated-123456"


def test_hmac_v2_binds_run_body_and_fresh_256_bit_jti(monkeypatch) -> None:
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)
    context = build_materialize_context(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        cartridge_id="replicon",
        dataset_name="employees",
        run_id="scheduled__2026-08-01T00:00:00Z",
        now=1000,
    )

    assert context["_signature_version"] == "hmac-sha256-v2"
    assert len(context["jti"]) == 64
    assert len(bytes.fromhex(context["jti"])) == 32
    assert context["run_id"] == "scheduled__2026-08-01T00:00:00Z"
    assert len(context["body_digest"]) == 64


@pytest.mark.parametrize("payload", [{"ok": False}, {}, ["partial"]])
def test_materialization_http_outcome_fails_closed(payload) -> None:
    from airflow.dags.dataset_refresh_outcome import (
        require_successful_materialization_response,
    )

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return payload

    with pytest.raises(RuntimeError, match="unavailable"):
        require_successful_materialization_response(
            Response(), expected_name="employees"
        )


def test_materialization_http_outcome_accepts_real_refinement_success() -> None:
    from airflow.dags.dataset_refresh_outcome import (
        require_successful_materialization_response,
    )

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "name": "employees",
                "layer": "silver",
                "row_count": 3,
            }

    assert require_successful_materialization_response(
        Response(), expected_name="employees"
    ) == {"name": "employees", "layer": "silver", "row_count": 3}


@pytest.mark.parametrize("case", ["unknown_raw", "cycle", "truncated"])
def test_graph_planning_rejects_unknown_cycles_and_depth_truncation(case: str) -> None:
    planner = getattr(dataset_refresh_graph, "_build_plan")
    graph = {
        "silver_a": {
            "layer": "silver",
            "cartridge": "replicon",
            "sources": ["raw/replicon/users"],
        },
        "gold_b": {"layer": "gold", "cartridge": "replicon", "sources": ["silver_a"]},
    }
    seed_raw = "raw/replicon/missing"
    maximum_depth = 10
    if case == "cycle":
        graph["silver_a"]["sources"] = ["gold_b"]
        seed_raw = ""
    elif case == "truncated":
        seed_raw = "raw/replicon/users"
        maximum_depth = 1
    with pytest.raises(ValueError):
        planner(
            graph,
            seed_raw=seed_raw,
            seed_dataset="silver_a" if case == "cycle" else "",
            maximum_depth=maximum_depth,
        )


def test_materialization_slot_contract_has_lease_heartbeat_and_fencing() -> None:
    reserve = inspect.signature(dataset_refresh_idempotency.reserve_materialization)
    finish = inspect.signature(dataset_refresh_idempotency.finish_materialization)
    assert "lease_seconds" in reserve.parameters
    assert "lease_token" in finish.parameters
    assert hasattr(dataset_refresh_idempotency, "heartbeat_materialization")


def test_scheduled_agent_slot_has_durable_lease_and_fencing() -> None:
    reserve = inspect.signature(agent_scheduler.reserve_scheduled_run)
    finish = inspect.signature(agent_scheduler.finish_scheduled_run)
    assert "lease_seconds" in reserve.parameters
    assert "fencing_token" in finish.parameters
    assert hasattr(agent_scheduler, "heartbeat_scheduled_run")
    migration = (
        Path(__file__).resolve().parents[1] / "infra/init/99u_agent_schedule_runs.sql"
    ).read_text(encoding="utf-8")
    for field in ("lease_expires_at", "heartbeat_at", "fencing_token"):
        assert field in migration


def test_signal_count_cannot_claim_items_that_do_not_exist() -> None:
    assert not monitor_should_alert(
        {"threshold": {"min_signal_count": 1}},
        {
            "status": "ready",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "signals": {"count": 1, "items": []},
        },
    )


@pytest.mark.parametrize(
    "schedule",
    [
        {"enabled": True, "cron": "not a cron", "tz": "UTC"},
        {"enabled": True, "cron": "* * * * *", "tz": "Not/AZone"},
    ],
)
def test_invalid_schedule_is_an_explicit_operational_error(schedule) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="schedule"):
        _fire_in_window(schedule, start, start + timedelta(minutes=5))


def test_scheduled_endpoint_never_trusts_schedule_key_from_body() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "console/app/routers/v1/agents.py"
    ).read_text(encoding="utf-8")
    source = source.split("async def api_agents_invoke_scheduled", 1)[1].split(
        "@router", 1
    )[0]
    assert 'body.get("schedule_key")' not in source


def test_intelligence_failure_cannot_leave_pipeline_green(monkeypatch) -> None:
    from airflow.dags.dataset_refresh_finalization import finalize_pipeline_status

    saved_statuses: list[str] = []

    def fail_intelligence() -> None:
        raise OSError("private backend detail")

    with pytest.raises(RuntimeError, match="intelligence"):
        finalize_pipeline_status(
            final_status="success",
            should_trigger_intelligence=True,
            save_status=saved_statuses.append,
            trigger_intelligence=fail_intelligence,
        )
    assert saved_statuses == ["running", "failed"]
