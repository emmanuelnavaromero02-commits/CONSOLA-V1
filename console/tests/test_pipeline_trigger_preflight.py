from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from fastapi import HTTPException


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
OWN = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
USER = {
    "id": 9,
    "email": "ops@example.com",
    "role": "admin",
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
}


def _tp():
    return import_module("app.domains.pipeline.trigger_preflight")


@pytest.fixture(autouse=True)
def _fresh_cache():
    _tp().reset_preflight_cache()
    yield
    _tp().reset_preflight_cache()


def _describe(**overrides):
    payload = {
        "dag_id": "sap_successfactors_extract",
        "found": True,
        "is_paused": True,
        "is_active": True,
        "has_import_errors": False,
        "schedule_kind": "manual",
        "scheduler_healthy": True,
        "runs": [],
        "foreign": {"queued": 0, "running": 0, "stale_queued": 0},
    }
    payload.update(overrides)
    return payload


class Airflow:
    def __init__(self, *describes, unpause=None, raise_describe=False):
        self.describes = list(describes)
        self.unpause = unpause or {"unpaused": True, "was_paused": True, "reason": "unpaused"}
        self.raise_describe = raise_describe
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, server, tool, args, *, user=None):
        assert server == "infra"
        self.calls.append((tool, dict(args)))
        if tool == "airflow_describe_dag":
            if self.raise_describe:
                raise HTTPException(502, "MCP transport failed")
            return self.describes.pop(0) if len(self.describes) > 1 else self.describes[0]
        if tool == "airflow_unpause_manual_dag":
            if isinstance(self.unpause, Exception):
                raise self.unpause
            return self.unpause
        raise AssertionError(tool)

    def tools(self):
        return [tool for tool, _args in self.calls]


class Audit:
    def __init__(self):
        self.events: list[dict] = []

    async def __call__(self, **payload):
        self.events.append(payload)


async def _check(dag_id, airflow, **kwargs):
    kwargs.setdefault("record_event", Audit())
    kwargs.setdefault("now", NOW)
    return await _tp().ensure_dag_ready_for_manual_trigger(
        dag_id, USER, invoke=airflow, **kwargs
    )


def _reason(exc: pytest.ExceptionInfo) -> str:
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert set(detail) == {"reason", "message", "public_message"}
    assert detail["public_message"]
    return detail["reason"]


@pytest.mark.anyio
async def test_paused_manual_dag_is_resumed_with_an_audit_row():
    airflow = Airflow(_describe())
    audit = Audit()
    result = await _check("sap_successfactors_extract", airflow, record_event=audit)
    assert result.was_paused is True
    assert result.unpaused is True
    assert result.schedule_kind == "manual"
    assert result.message_es == _tp().UNPAUSED_MESSAGE_ES
    assert result.automation() == {"was_paused": True, "unpaused": True, "message_es": _tp().UNPAUSED_MESSAGE_ES}
    assert airflow.tools() == ["airflow_describe_dag", "airflow_unpause_manual_dag"]
    event = audit.events[0]
    assert event["action"] == "pipeline.dag.unpause_for_manual_trigger"
    assert event["resource_type"] == "airflow_dag"
    assert event["resource_id"] == "sap_successfactors_extract"
    assert event["metadata"]["tenant_id"] == TENANT
    assert event["metadata"]["workspace_id"] == WORKSPACE


@pytest.mark.anyio
async def test_scheduled_dag_paused_by_operator_is_never_resumed():
    airflow = Airflow(_describe(dag_id="sap_b1_extract", schedule_kind="scheduled"))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_b1_extract", airflow)
    assert _reason(exc) == "dag_paused_by_operator"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
async def test_scheduled_dag_running_normally_is_left_alone():
    airflow = Airflow(_describe(schedule_kind="scheduled", is_paused=False))
    result = await _check("sap_successfactors_extract", airflow)
    assert result.unpaused is False
    assert result.was_paused is False
    assert airflow.tools() == ["airflow_describe_dag"]


@pytest.mark.anyio
@pytest.mark.parametrize("overrides", [{"found": False}, {"is_active": False}, {"has_import_errors": True}])
async def test_unavailable_dag_is_an_honest_conflict(overrides):
    airflow = Airflow(_describe(**overrides))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow)
    assert _reason(exc) == "dag_unavailable"


@pytest.mark.anyio
async def test_foreign_backlog_blocks_resume():
    airflow = Airflow(_describe(foreign={"queued": 3, "running": 0, "stale_queued": 3}))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow)
    assert _reason(exc) == "foreign_backlog_requires_platform_recovery"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
async def test_own_stale_backlog_requires_recovery_before_resume():
    stale = {"dag_run_id": "manual__old", "state": "queued", "queued_at": (NOW - timedelta(hours=5)).isoformat(), "stale": True, "conf": OWN}
    airflow = Airflow(_describe(runs=[stale]))

    async def known(_dag_id, run_ids):
        return set(run_ids)

    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow, console_run_ids=known)
    assert _reason(exc) == "stale_runs_require_recovery"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
async def test_own_stale_backlog_without_console_rows_is_foreign():
    stale = {"dag_run_id": "manual__orphan", "state": "queued", "stale": True, "conf": OWN}
    airflow = Airflow(_describe(runs=[stale]))

    async def known(_dag_id, _run_ids):
        return set()

    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow, console_run_ids=known)
    assert _reason(exc) == "foreign_backlog_requires_platform_recovery"


@pytest.mark.anyio
async def test_auto_neutralize_recovers_own_backlog_then_resumes():
    stale = {"dag_run_id": "manual__old", "state": "queued", "stale": True, "conf": OWN}
    airflow = Airflow(_describe(runs=[stale]), _describe(runs=[]))
    recovered: list[str] = []

    async def recover(dag_id):
        recovered.append(dag_id)

    result = await _check("sap_successfactors_extract", airflow, recover=recover, auto_neutralize=True)
    assert recovered == ["sap_successfactors_extract"]
    assert result.unpaused is True
    assert airflow.tools() == ["airflow_describe_dag", "airflow_describe_dag", "airflow_unpause_manual_dag"]


@pytest.mark.anyio
async def test_auto_neutralize_that_leaves_backlog_still_blocks():
    stale = {"dag_run_id": "manual__old", "state": "queued", "stale": True, "conf": OWN}
    airflow = Airflow(_describe(runs=[stale]), _describe(runs=[stale]))

    async def recover(_dag_id):
        return None

    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow, recover=recover, auto_neutralize=True)
    assert _reason(exc) == "stale_runs_require_recovery"


@pytest.mark.anyio
async def test_auto_unpause_can_be_disabled():
    airflow = Airflow(_describe())
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow, auto_unpause=False)
    assert _reason(exc) == "auto_unpause_disabled"


@pytest.mark.anyio
async def test_unpause_failure_is_reported_as_unavailable():
    airflow = Airflow(_describe(), unpause=HTTPException(502, "down"))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow)
    assert _reason(exc) == "dag_unavailable"


@pytest.mark.anyio
async def test_aggregate_reuses_the_live_run_instead_of_piling_up():
    runs = [
        {"dag_run_id": "manual__queued", "state": "queued", "queued_at": (NOW - timedelta(minutes=1)).isoformat(), "stale": False, "conf": {**OWN, "mode": "incremental", "target": "all"}},
        {"dag_run_id": "manual__running", "state": "running", "queued_at": (NOW - timedelta(hours=2)).isoformat(), "stale": True, "conf": {**OWN, "mode": "incremental", "target": "all"}},
    ]
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", is_paused=False, runs=runs))
    result = await _check("sap_successfactors_extract_all", airflow, mode="incremental", target="all")
    assert result.reuse_run_id == "manual__running"
    assert airflow.tools() == ["airflow_describe_dag"]


@pytest.mark.anyio
async def test_aggregate_does_not_reuse_a_run_with_other_parameters():
    runs = [{"dag_run_id": "manual__full", "state": "running", "conf": {**OWN, "mode": "full", "target": "all"}}]
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", is_paused=False, runs=runs))
    result = await _check("sap_successfactors_extract_all", airflow, mode="incremental", target="all")
    assert result.reuse_run_id is None


@pytest.mark.anyio
async def test_paused_aggregate_resumes_and_reuses_its_recent_queued_run():
    runs = [{"dag_run_id": "manual__recent", "state": "queued", "queued_at": (NOW - timedelta(minutes=2)).isoformat(), "stale": False, "conf": OWN}]
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", runs=runs))
    result = await _check("sap_successfactors_extract_all", airflow)
    assert result.unpaused is True
    assert result.reuse_run_id == "manual__recent"


@pytest.mark.anyio
async def test_describe_failure_fails_open_without_touching_airflow():
    airflow = Airflow(_describe(), raise_describe=True)
    result = await _check("sap_successfactors_extract", airflow)
    assert result.checked is False
    assert result.unpaused is False
    assert airflow.tools() == ["airflow_describe_dag"]


@pytest.mark.anyio
@pytest.mark.parametrize("dag_id", ["entity_scheduler", "agent_runner", "replicon_timeentry_full", "sap_b1_refresh", "Extract", ""])
async def test_only_manual_extract_dags_are_eligible(dag_id):
    airflow = Airflow(_describe())
    result = await _check(dag_id, airflow)
    assert result.checked is False
    assert airflow.calls == []


@pytest.mark.anyio
async def test_ready_entity_dag_is_cached_briefly():
    airflow = Airflow(_describe(is_paused=False))
    ticks = iter([100.0, 110.0, 200.0, 200.0])
    await _check("sap_successfactors_extract", airflow, clock=lambda: next(ticks))
    await _check("sap_successfactors_extract", airflow, clock=lambda: next(ticks))
    assert airflow.tools() == ["airflow_describe_dag"]
    await _check("sap_successfactors_extract", airflow, clock=lambda: next(ticks))
    assert airflow.tools() == ["airflow_describe_dag", "airflow_describe_dag"]


def test_env_policy_defaults(monkeypatch):
    for name in ("PIPELINE_MANUAL_DAG_AUTO_UNPAUSE", "PIPELINE_AUTO_RECOVERY_NEUTRALIZE", "PIPELINE_STUCK_RUN_THRESHOLD_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert _tp().manual_dag_auto_unpause_enabled() is True
    assert _tp().auto_recovery_neutralize_enabled() is False
    assert _tp().stuck_run_threshold_seconds() == 900
    monkeypatch.setenv("PIPELINE_MANUAL_DAG_AUTO_UNPAUSE", "false")
    monkeypatch.setenv("PIPELINE_AUTO_RECOVERY_NEUTRALIZE", "true")
    monkeypatch.setenv("PIPELINE_STUCK_RUN_THRESHOLD_SECONDS", "60")
    assert _tp().manual_dag_auto_unpause_enabled() is False
    assert _tp().auto_recovery_neutralize_enabled() is True
    assert _tp().stuck_run_threshold_seconds() == 900
    monkeypatch.setenv("PIPELINE_STUCK_RUN_THRESHOLD_SECONDS", "1800")
    assert _tp().stuck_run_threshold_seconds() == 1800


@pytest.mark.anyio
@pytest.mark.real_pipeline_automation
async def test_console_entry_point_applies_env_policy(monkeypatch):
    monkeypatch.setenv("PIPELINE_MANUAL_DAG_AUTO_UNPAUSE", "false")
    airflow = Airflow(_describe())

    async def get_pool():
        raise AssertionError("no backlog means no database read")

    async def refresh(row, user=None):
        return row

    with pytest.raises(HTTPException) as exc:
        await _tp().run_manual_trigger_preflight(
            "sap_successfactors_extract",
            USER,
            invoke=airflow,
            get_db_pool=get_pool,
            refresh_dag_run_status=refresh,
        )
    assert _reason(exc) == "auto_unpause_disabled"


@pytest.mark.anyio
async def test_test_suite_stub_keeps_airflow_out_of_unrelated_tests():
    result = await _tp().run_manual_trigger_preflight(
        "sap_successfactors_extract",
        USER,
        invoke=None,
        get_db_pool=None,
        refresh_dag_run_status=None,
    )
    assert result.checked is False


def _prod_stuck_extract_all_run():
    """The production shape: manual, queued 07:57, started 08:17, no TI state since."""
    return {
        "dag_run_id": "manual__2026-09-26T07:57:00+00:00",
        "state": "running",
        "queued_at": "2026-09-26T07:57:00+00:00",
        "start_date": "2026-09-26T08:17:00+00:00",
        "stale": True,
        "conf": {**OWN, "mode": "incremental", "target": "all"},
    }


@pytest.mark.anyio
async def test_paused_dag_never_resumes_an_hours_old_running_run():
    prod_now = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", is_paused=True, runs=[_prod_stuck_extract_all_run()]))

    async def known(_dag_id, run_ids):
        return set(run_ids)

    with pytest.raises(HTTPException) as exc:
        await _check(
            "sap_successfactors_extract_all",
            airflow,
            now=prod_now,
            auto_unpause=True,
            auto_neutralize=False,
            mode="incremental",
            target="all",
            console_run_ids=known,
        )
    assert _reason(exc) == "stale_runs_require_recovery"
    assert airflow.tools() == ["airflow_describe_dag"]


@pytest.mark.anyio
async def test_old_running_run_is_backlog_even_without_the_tool_stale_flag():
    prod_now = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
    run = {**_prod_stuck_extract_all_run(), "stale": False}
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", runs=[run]))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract_all", airflow, now=prod_now)
    assert _reason(exc) == "stale_runs_require_recovery"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
async def test_prod_shape_without_a_console_row_is_platform_backlog():
    prod_now = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", runs=[_prod_stuck_extract_all_run()]))

    async def known(_dag_id, _run_ids):
        return set()

    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract_all", airflow, now=prod_now, console_run_ids=known)
    assert _reason(exc) == "foreign_backlog_requires_platform_recovery"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
async def test_auto_neutralize_clears_the_stuck_running_run_before_resuming():
    prod_now = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
    airflow = Airflow(
        _describe(dag_id="sap_successfactors_extract_all", runs=[_prod_stuck_extract_all_run()]),
        _describe(dag_id="sap_successfactors_extract_all", runs=[]),
    )
    recovered: list[str] = []

    async def recover(dag_id):
        recovered.append(dag_id)

    result = await _check("sap_successfactors_extract_all", airflow, now=prod_now, recover=recover, auto_neutralize=True)
    assert recovered == ["sap_successfactors_extract_all"]
    assert result.unpaused is True
    assert result.reuse_run_id is None


@pytest.mark.anyio
async def test_recent_running_run_on_a_paused_dag_may_resume_and_be_reused():
    run = {
        "dag_run_id": "manual__just_now",
        "state": "running",
        "queued_at": (NOW - timedelta(minutes=3)).isoformat(),
        "stale": False,
        "conf": {**OWN, "mode": "incremental", "target": "all"},
    }
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", runs=[run]))
    result = await _check("sap_successfactors_extract_all", airflow, mode="incremental", target="all")
    assert result.unpaused is True
    assert result.reuse_run_id == "manual__just_now"


@pytest.mark.anyio
async def test_old_running_run_on_an_unpaused_dag_is_a_long_extraction_to_reuse():
    runs = [{**_prod_stuck_extract_all_run(), "dag_run_id": "manual__long"}]
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", is_paused=False, runs=runs))
    result = await _check("sap_successfactors_extract_all", airflow, mode="incremental", target="all")
    assert result.reuse_run_id == "manual__long"
    assert result.unpaused is False


@pytest.mark.anyio
async def test_foreign_stale_running_runs_block_resume():
    airflow = Airflow(_describe(foreign={"queued": 0, "running": 1, "stale_queued": 0, "stale_running": 1}))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow)
    assert _reason(exc) == "foreign_backlog_requires_platform_recovery"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
@pytest.mark.parametrize("flag", ["running_truncated", "queued_truncated"])
async def test_truncated_pending_runs_on_a_paused_dag_block_resume(flag):
    airflow = Airflow(_describe(**{flag: True}))
    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract", airflow)
    assert _reason(exc) == "foreign_backlog_requires_platform_recovery"
    assert "airflow_unpause_manual_dag" not in airflow.tools()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "conf",
    [
        {"cartridge_id": "sap_successfactors", "mode": "incremental", "target": "all"},
        {"tenant_id": TENANT, "mode": "incremental", "target": "all"},
        {"tenant_id": TENANT, "workspace_id": "55555555-5555-5555-5555-555555555555"},
        {},
    ],
)
async def test_only_runs_scoped_to_the_caller_are_reused_or_counted_as_own(conf):
    live = {"dag_run_id": "manual__someone_else", "state": "running", "queued_at": (NOW - timedelta(minutes=1)).isoformat(), "stale": False, "conf": conf}
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", is_paused=False, runs=[live]))
    result = await _check("sap_successfactors_extract_all", airflow, mode="incremental", target="all")
    assert result.reuse_run_id is None

    stale = {**live, "stale": True, "queued_at": (NOW - timedelta(hours=3)).isoformat()}
    paused = Airflow(_describe(dag_id="sap_successfactors_extract_all", runs=[stale]))

    async def known(_dag_id, run_ids):
        return set(run_ids)

    with pytest.raises(HTTPException) as exc:
        await _check("sap_successfactors_extract_all", paused, console_run_ids=known)
    assert _reason(exc) == "foreign_backlog_requires_platform_recovery"


def test_preflight_recovery_can_close_stuck_running_runs_and_orphans():
    classes = _tp().PREFLIGHT_RECOVERY_CLASSES
    assert {"stalled_running_no_tasks", "stalled_queued_paused_dag", "airflow_orphan"} <= classes


@pytest.mark.anyio
async def test_run_ownership_ignores_uuid_case_like_mcp_infra():
    live = {
        "dag_run_id": "manual__upper",
        "state": "running",
        "queued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "stale": False,
        "conf": {"tenant_id": TENANT.upper(), "workspace_id": WORKSPACE.upper(), "mode": "incremental", "target": "all"},
    }
    airflow = Airflow(_describe(dag_id="sap_successfactors_extract_all", is_paused=False, runs=[live]))
    result = await _check("sap_successfactors_extract_all", airflow, mode="incremental", target="all")
    assert result.reuse_run_id == "manual__upper"
