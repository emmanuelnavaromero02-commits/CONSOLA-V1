from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.domains.pipeline import sync_state


def test_sync_state_clean_mode_and_target_raise_http_errors():
    assert sync_state.sync_clean_mode(" full ") == "full"
    assert sync_state.sync_clean_target(" talent ") == "talent"

    with pytest.raises(HTTPException) as mode_exc:
        sync_state.sync_clean_mode("bad")
    assert mode_exc.value.status_code == 400
    assert "mode must be incremental or full" in str(mode_exc.value.detail)

    with pytest.raises(HTTPException) as target_exc:
        sync_state.sync_clean_target("bad")
    assert target_exc.value.status_code == 400
    assert "target must be all, foundation or talent" in str(target_exc.value.detail)


def test_pipeline_extract_all_validation_errors_are_http_errors():
    with pytest.raises(HTTPException) as exc:
        sync_state.pipeline_extract_all_mode_target(
            {"mode": "incremental", "target": "unknown"}
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid extract target"


def test_active_extract_run_payload_keeps_legacy_job_fields():
    payload = sync_state.active_extract_run_payload(
        row={
            "run_id": "pipeline-run-1",
            "airflow_dag_run_id": "dag-run-1",
            "status": "RUNNING",
        },
        cartridge="sap_successfactors",
        entity="EmpJob",
        dag_id="sap_successfactors_extract",
        conf={"entity": "EmpJob"},
        reason="active_entity_run",
    )

    assert payload == {
        "triggered": False,
        "reused": True,
        "cartridge": "sap_successfactors",
        "entity": "EmpJob",
        "dag_id": "sap_successfactors_extract",
        "job_id": "dag-run-1",
        "run_id": "dag-run-1",
        "dag_run_id": "dag-run-1",
        "state": "running",
        "reason": "active_entity_run",
        "conf": {"entity": "EmpJob"},
    }


def test_sync_now_lock_key_uses_security_scope():
    lock_key = sync_state.sync_now_lock_key(
        cartridge="sap_successfactors",
        mode="incremental",
        target="talent",
        conn_id="femsa_sf",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
    )

    assert lock_key == (
        "sync-now:tenant-1:workspace-1:sap_successfactors:incremental:talent:femsa_sf"
    )


def test_active_sync_run_lookup_parts_include_optional_columns():
    lookup = sync_state.active_sync_run_lookup_parts(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        has_mode=True,
        has_extra=True,
        has_started_at=True,
    )

    assert lookup["args"][0] == "sap_successfactors"
    assert lookup["args"][2:] == ["incremental", "all"]
    assert "COALESCE(mode, $3)=$3" in lookup["clauses"]
    assert "COALESCE(extra->>'target', 'all')=$4" in lookup["clauses"]
    assert any("started_at >" in clause for clause in lookup["clauses"])
    assert lookup["order_sql"] == "started_at DESC NULLS LAST"


def test_active_sync_run_lookup_parts_tolerate_legacy_schema():
    lookup = sync_state.active_sync_run_lookup_parts(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        has_mode=False,
        has_extra=False,
        has_started_at=False,
    )

    joined = "\n".join(lookup["clauses"])
    assert lookup["args"][0] == "sap_successfactors"
    assert len(lookup["args"]) == 2
    assert "COALESCE(mode" not in joined
    assert "extra->>" not in joined
    assert "started_at >" not in joined
    assert lookup["order_sql"] == "run_id DESC"


def test_sync_run_working_state_extracts_triggered_child_ids():
    working_state = sync_state.sync_run_working_state(
        {
            "extra": {
                "steps": [{"id": "bronze"}],
                "triggered_entities": [
                    {"entity": "EmpJob", "dag_run_id": " dag-run-1 "},
                    {"entity": "User", "job_id": "job-run-2"},
                    {"entity": "Ignored", "dag_run_id": "   "},
                    "not-a-dict",
                ],
                "errors": [{"entity": "EmpJob", "error": "partial"}],
                "target": "talent",
            }
        }
    )

    assert working_state["extra"]["target"] == "talent"
    assert working_state["steps"] == [{"id": "bronze"}]
    assert [
        item["entity"]
        for item in working_state["triggered"]
        if isinstance(item, dict)
    ] == [
        "EmpJob",
        "User",
        "Ignored",
    ]
    assert working_state["triggered"][-1] == "not-a-dict"
    assert working_state["errors"] == [{"entity": "EmpJob", "error": "partial"}]
    assert working_state["child_run_ids"] == ["dag-run-1", "job-run-2"]


def test_sync_run_working_state_defaults_invalid_extra_shapes():
    working_state = sync_state.sync_run_working_state(
        {"extra": {"steps": "bad", "triggered_entities": {}, "errors": "bad"}}
    )

    assert [step["id"] for step in working_state["steps"]] == [
        "connection",
        "bronze",
        "silver_gold",
        "control_room",
        "agents_intelligence",
    ]
    assert working_state["triggered"] == []
    assert working_state["errors"] == []
    assert working_state["child_run_ids"] == []


def test_sync_start_step_updates_marks_connection_running():
    steps = sync_state.merge_sync_steps(
        sync_state.initial_sync_steps(),
        sync_state.sync_start_step_updates(),
    )

    connection = next(step for step in steps if step["id"] == "connection")
    assert connection["status"] == "running"
    assert connection["detail"] == "Validando scope y conexión del cartucho."


def test_sync_dataset_seed_failure_step_updates_fail_downstream_steps():
    updates = sync_state.sync_dataset_seed_failure_step_updates("seed exploded")

    assert updates["connection"]["status"] == "success"
    assert updates["silver_gold"]["status"] == "failed"
    assert updates["silver_gold"]["error"] == "seed exploded"
    assert updates["control_room"]["status"] == "failed"
    assert updates["agents_intelligence"]["status"] == "failed"


def test_sync_running_extra_uses_expected_contract():
    extra = sync_state.sync_running_extra(
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        request_id="request-1",
        steps=[{"id": "connection", "status": "running"}],
    )

    assert extra == {
        "mode": "incremental",
        "target": "all",
        "conn_id": "femsa_sf",
        "request_id": "request-1",
        "steps": [{"id": "connection", "status": "running"}],
        "triggered_entities": [],
        "errors": [],
        "control_room_ready": False,
    }


def test_sync_dataset_seed_failure_extra_keeps_error_and_dataset_seed():
    extra = sync_state.sync_dataset_seed_failure_extra(
        mode="full",
        target="talent",
        conn_id=None,
        request_id=None,
        steps=[{"id": "silver_gold", "status": "failed"}],
        message="RuntimeError: seed exploded",
    )

    assert extra["mode"] == "full"
    assert extra["target"] == "talent"
    assert extra["triggered_entities"] == []
    assert extra["control_room_ready"] is False
    assert extra["errors"] == [
        {
            "entity": "__dataset_seed__",
            "error": "RuntimeError: seed exploded",
            "reason": "packaged_dataset_seed_failed",
        }
    ]
    assert extra["dataset_seed"] == {
        "status": "failed",
        "reason": "packaged_dataset_seed_failed",
        "error": "RuntimeError: seed exploded",
    }


def test_sync_extract_all_trigger_extra_defaults_trigger_strategy():
    extra = sync_state.sync_extract_all_trigger_extra(
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        request_id="request-1",
        steps=[{"id": "bronze", "status": "running"}],
        triggered_entities=[{"entity": "__extract_all__"}],
        errors=[],
        result={"triggered": [{"entity": "__extract_all__"}]},
        dataset_seed={"status": "success"},
    )

    assert extra["trigger_strategy"] == "fanout"
    assert extra["triggered_entities"] == [{"entity": "__extract_all__"}]
    assert extra["dataset_seed"] == {"status": "success"}
    assert extra["extract_all_result"] == {"triggered": [{"entity": "__extract_all__"}]}


def test_sync_extract_all_result_state_normalizes_shapes():
    state = sync_state.sync_extract_all_result_state(
        {"triggered": [{"entity": "EmpJob"}], "errors": "bad"}
    )

    assert state["triggered_entities"] == [{"entity": "EmpJob"}]
    assert state["errors"] == []
    assert state["bronze_status"] == "running"


def test_sync_extract_all_trigger_step_updates_explain_empty_trigger():
    updates = sync_state.sync_extract_all_trigger_step_updates(
        triggered_entities=[],
        errors=[{"entity": "EmpJob", "error": "offline"}],
        attempts=3,
    )

    assert updates["connection"]["status"] == "partial"
    assert updates["connection"]["attempts"] == 3
    assert updates["bronze"]["status"] == "failed"
    assert updates["silver_gold"]["status"] == "failed"
    assert updates["control_room"]["status"] == "failed"
    assert updates["agents_intelligence"]["status"] == "failed"


def test_sync_extract_all_error_message_uses_first_three_errors():
    assert sync_state.sync_extract_all_error_message(
        [
            {"error": "one"},
            {"error": "two"},
            {"error": "three"},
            {"error": "four"},
        ]
    ) == "one; two; three"


@pytest.mark.anyio
async def test_run_sync_extract_all_with_retries_uses_aggregate_result_first():
    aggregate_calls = []
    fallback_calls = []

    async def trigger_sync_aggregate_extract_all(**kwargs):
        aggregate_calls.append(kwargs)
        return {"triggered": [{"entity": "__extract_all__"}], "errors": []}

    async def call_with_optional_user(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return {"triggered": [], "errors": []}

    result = await sync_state.run_sync_extract_all_with_retries(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        run_id="sync-1",
        extract_body={"idempotency_key": "sync-1"},
        user={"sub": "user-1"},
        trigger_sync_aggregate_extract_all=trigger_sync_aggregate_extract_all,
        call_with_optional_user=call_with_optional_user,
        api_pipeline_extract_all=object(),
        sleep=None,
    )

    assert result == {
        "result": {"triggered": [{"entity": "__extract_all__"}], "errors": []},
        "attempts": 1,
    }
    assert aggregate_calls == [
        {
            "cartridge": "sap_successfactors",
            "mode": "incremental",
            "target": "all",
            "conn_id": "femsa_sf",
            "run_id": "sync-1",
            "user": {"sub": "user-1"},
        }
    ]
    assert fallback_calls == []


@pytest.mark.anyio
async def test_run_sync_extract_all_with_retries_falls_back_and_retries():
    fallback_results = [
        {"triggered": [], "errors": [{"error": "airflow unavailable"}]},
        {"triggered": [{"entity": "EmpJob"}], "errors": []},
    ]
    fallback_calls = []
    sleeps = []

    async def trigger_sync_aggregate_extract_all(**_kwargs):
        return None

    async def call_with_optional_user(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return fallback_results.pop(0)

    async def sleep(delay):
        sleeps.append(delay)

    result = await sync_state.run_sync_extract_all_with_retries(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id=None,
        run_id="sync-2",
        extract_body={"idempotency_key": "sync-2"},
        user={"sub": "user-1"},
        trigger_sync_aggregate_extract_all=trigger_sync_aggregate_extract_all,
        call_with_optional_user=call_with_optional_user,
        api_pipeline_extract_all="api",
        retryable_errors=lambda errors: bool(errors),
        sleep=sleep,
    )

    assert result == {
        "result": {"triggered": [{"entity": "EmpJob"}], "errors": []},
        "attempts": 2,
    }
    assert sleeps == [2]
    assert len(fallback_calls) == 2
    assert fallback_calls[0] == (
        ("api", "sap_successfactors", {"idempotency_key": "sync-2"}),
        {"user": {"sub": "user-1"}},
    )


@pytest.mark.anyio
async def test_maybe_trigger_aggregate_extract_all_returns_none_for_plain_cartridge():
    async def should_not_call(*_args, **_kwargs):
        raise AssertionError("plain cartridges must fall back to fanout")

    result = await sync_state.maybe_trigger_aggregate_extract_all(
        cartridge="replicon",
        body={"mode": "incremental"},
        user={"sub": "user-1"},
        sync_extract_all_dags={"sap_successfactors": "sap_successfactors_extract_all"},
        pipeline_extract_all_mode_target_func=lambda _body: ("incremental", "all"),
        resolve_pipeline_sync_conn_id_func=should_not_call,
        pipeline_extract_all_run_id_func=lambda **_kwargs: "run-1",
        trigger_sync_aggregate_extract_all_func=should_not_call,
        pipeline_extract_all_public_response_func=lambda payload: payload,
    )

    assert result is None


@pytest.mark.anyio
async def test_maybe_trigger_aggregate_extract_all_builds_and_wraps_result():
    calls = {}

    async def resolve_pipeline_sync_conn_id(cartridge, requested_conn_id, user):
        calls["resolve"] = (cartridge, requested_conn_id, user)
        return "vault_conn"

    def pipeline_extract_all_run_id(**kwargs):
        calls["run_id"] = kwargs
        return "extract-all-run"

    async def trigger_sync_aggregate_extract_all(**kwargs):
        calls["trigger"] = kwargs
        return {"triggered": [{"entity": "__extract_all__"}], "errors": []}

    result = await sync_state.maybe_trigger_aggregate_extract_all(
        cartridge="sap_successfactors",
        body={"mode": "full", "target": "talent", "connection_id": "requested"},
        user={"sub": "user-1"},
        sync_extract_all_dags={"sap_successfactors": "sap_successfactors_extract_all"},
        pipeline_extract_all_mode_target_func=lambda body: (body["mode"], body["target"]),
        resolve_pipeline_sync_conn_id_func=resolve_pipeline_sync_conn_id,
        pipeline_extract_all_run_id_func=pipeline_extract_all_run_id,
        trigger_sync_aggregate_extract_all_func=trigger_sync_aggregate_extract_all,
        pipeline_extract_all_public_response_func=lambda payload: {
            "public": payload["triggered"]
        },
    )

    assert result == {"public": [{"entity": "__extract_all__"}]}
    assert calls["resolve"] == (
        "sap_successfactors",
        "requested",
        {"sub": "user-1"},
    )
    assert calls["run_id"]["mode"] == "full"
    assert calls["run_id"]["target"] == "talent"
    assert calls["run_id"]["conn_id"] == "vault_conn"
    assert calls["trigger"] == {
        "cartridge": "sap_successfactors",
        "mode": "full",
        "target": "talent",
        "conn_id": "vault_conn",
        "run_id": "extract-all-run",
        "user": {"sub": "user-1"},
    }


def test_sync_child_runtime_state_counts_progress_rows():
    runtime = sync_state.sync_child_runtime_state(
        row={"status": "running"},
        child_rows=[
            {
                "entity": "__extract_all__",
                "status": "success",
                "extra": {
                    "gold_refresh": {
                        "status": "success",
                        "materialized": 1,
                        "total": 1,
                    }
                },
            },
            {"entity": "EmpJob", "status": "success", "row_count": 3},
            {"entity": "CareerWorksheet", "status": "blocked", "row_count": 0},
        ],
        child_run_ids=["aggregate-run"],
        triggered=[{"entity": "__extract_all__"}],
        errors=[],
        stale_after_seconds=3600,
    )

    assert runtime["running_children"] is False
    assert runtime["success_children"] == 1
    assert runtime["blocked_children"] == 1
    assert runtime["terminal_children"] == 2
    assert runtime["child_total"] == 2
    assert runtime["entity_summary"]["counts"]["success"] == 1
    assert runtime["entity_summary"]["counts"]["blocked"] == 1
    assert runtime["gold_refresh_summary"]["status"] == "success"


def test_sync_child_runtime_state_marks_stale_running_children_failed():
    runtime = sync_state.sync_child_runtime_state(
        row={
            "status": "running",
            "started_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
        },
        child_rows=[{"entity": "EmpJob", "status": "running"}],
        child_run_ids=["entity-run"],
        triggered=[{"entity": "EmpJob"}],
        errors=[],
        stale_after_seconds=1,
    )

    assert runtime["running_children"] is False
    assert runtime["failed_children"] == 1
    assert runtime["errors"] == [
        {
            "entity": "__sync_now__",
            "status_code": 504,
            "error": "Airflow sync run timed out before completing; start a new sync.",
        }
    ]


def test_sync_materialization_state_casts_pipeline_counts():
    state = sync_state.sync_materialization_state(
        [
            {
                "bronze": {"status": "fresh"},
                "silver": [{"status": "fresh"}, {"status": "missing"}],
                "gold": [{"status": "fresh"}, {"status": "missing"}],
            }
        ],
        {"status": "partial", "materialized": 3, "total": 4},
    )

    assert state["bronze_ready"] == 1
    assert state["silver_ready"] == 1
    assert state["gold_ready"] == 3
    assert state["gold_total"] == 4
    assert state["gold_partial"] is True
    assert state["materialization"]["gold_ready"] == 3


def test_sync_core_step_updates_builds_connection_bronze_and_silver_gold():
    child_runtime = sync_state.sync_child_runtime_state(
        row={"status": "running"},
        child_rows=[
            {"entity": "EmpJob", "status": "success", "row_count": 3},
            {
                "entity": "CareerWorksheet",
                "status": "blocked",
                "row_count": 0,
                "extra": {"reason": "entity_not_exposed_in_sap"},
            },
        ],
        child_run_ids=["run-1", "run-2"],
        triggered=[{"entity": "EmpJob"}, {"entity": "CareerWorksheet"}],
        errors=[],
        stale_after_seconds=3600,
    )
    materialization_state = {
        "bronze_ready": 2,
        "silver_ready": 1,
        "gold_ready": 1,
        "gold_total": 2,
        "gold_partial": True,
    }

    updates = sync_state.sync_core_step_updates(
        triggered=[{"entity": "EmpJob"}, {"entity": "CareerWorksheet"}],
        child_rows=[
            {"entity": "EmpJob", "status": "success"},
            {"entity": "CareerWorksheet", "status": "blocked"},
        ],
        child_runtime=child_runtime,
        materialization_state=materialization_state,
        errors=[],
    )

    assert updates["connection"]["status"] == "success"
    assert updates["bronze"]["status"] == "partial"
    assert updates["bronze"]["completed"] == 2
    assert updates["bronze"]["total"] == 2
    assert updates["silver_gold"]["status"] == "partial"
    assert updates["silver_gold"]["completed"] == 1
    assert updates["silver_gold"]["total"] == 2


@pytest.mark.anyio
async def test_fetch_active_sync_run_uses_scoped_lookup():
    class FakeConn:
        def __init__(self):
            self.calls = []

        async def fetchrow(self, query, *args):
            self.calls.append((query, args))
            return {"run_id": "sync-now-1", "status": "running"}

    class FakeScope:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn, "tenant-1", "workspace-1"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def get_db_pool():
        return object()

    async def table_has_column(table, column, **kwargs):
        assert table == "pipeline_runs"
        assert kwargs == {"refresh": True}
        return column in {"mode", "extra", "started_at"}

    async def scope_predicate(user, start_index, **kwargs):
        assert user == {"sub": "user-1"}
        assert start_index == 5
        assert kwargs == {"refresh_columns": True}
        return "AND workspace_id=$5", ["workspace-1"]

    conn = FakeConn()

    row = await sync_state.fetch_active_sync_run(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        user={"sub": "user-1"},
        get_db_pool=get_db_pool,
        table_has_column=table_has_column,
        pipeline_runs_scope_predicate=scope_predicate,
        scoped_db_for_user=lambda pool, user: FakeScope(conn),
    )

    assert row == {"run_id": "sync-now-1", "status": "running"}
    query, args = conn.calls[0]
    assert "FROM pipeline_runs" in query
    assert "COALESCE(mode, $3)=$3" in query
    assert "COALESCE(extra->>'target', 'all')=$4" in query
    assert "AND workspace_id=$5" in query
    assert "ORDER BY started_at DESC NULLS LAST" in query
    assert args == (
        "sap_successfactors",
        list(sync_state.SYNC_TERMINAL_STATUSES),
        "incremental",
        "all",
        "workspace-1",
    )


@pytest.mark.anyio
async def test_fetch_active_sync_run_returns_none_on_query_error():
    class BrokenConn:
        async def fetchrow(self, *_args, **_kwargs):
            raise RuntimeError("schema drift")

    class FakeScope:
        async def __aenter__(self):
            return BrokenConn(), None, None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def get_db_pool():
        return object()

    async def table_has_column(*_args, **_kwargs):
        return False

    async def scope_predicate(*_args, **_kwargs):
        return "", []

    warnings = []

    row = await sync_state.fetch_active_sync_run(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        user=None,
        get_db_pool=get_db_pool,
        table_has_column=table_has_column,
        pipeline_runs_scope_predicate=scope_predicate,
        scoped_db_for_user=lambda pool, user: FakeScope(),
        logger_warning=lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    assert row is None
    assert warnings
    assert warnings[0][1] == {"exc_info": True}


@pytest.mark.anyio
async def test_fetch_sync_run_uses_scoped_lookup():
    class FakeConn:
        def __init__(self):
            self.calls = []

        async def fetchrow(self, query, *args):
            self.calls.append((query, args))
            return {"run_id": "sync-now-1", "status": "partial"}

    class FakeScope:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn, "tenant-1", "workspace-1"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def get_db_pool():
        return object()

    async def scope_predicate(user, start_index, **kwargs):
        assert user == {"sub": "user-1"}
        assert start_index == 3
        assert kwargs == {"refresh_columns": True}
        return "AND workspace_id=$3", ["workspace-1"]

    conn = FakeConn()

    row = await sync_state.fetch_sync_run(
        cartridge="sap_successfactors",
        run_id="sync-now-1",
        user={"sub": "user-1"},
        get_db_pool=get_db_pool,
        pipeline_runs_scope_predicate=scope_predicate,
        scoped_db_for_user=lambda pool, user: FakeScope(conn),
    )

    assert row == {"run_id": "sync-now-1", "status": "partial"}
    query, args = conn.calls[0]
    assert "FROM pipeline_runs" in query
    assert "cartridge_id=$1" in query
    assert "run_id=$2" in query
    assert "entity='__sync_now__'" in query
    assert "AND workspace_id=$3" in query
    assert args == ("sap_successfactors", "sync-now-1", "workspace-1")


@pytest.mark.anyio
async def test_fetch_sync_run_returns_none_without_row():
    class FakeConn:
        async def fetchrow(self, *_args):
            return None

    class FakeScope:
        async def __aenter__(self):
            return FakeConn(), None, None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def get_db_pool():
        return object()

    async def scope_predicate(*_args, **_kwargs):
        return "", []

    row = await sync_state.fetch_sync_run(
        cartridge="sap_successfactors",
        run_id="missing",
        user=None,
        get_db_pool=get_db_pool,
        pipeline_runs_scope_predicate=scope_predicate,
        scoped_db_for_user=lambda pool, user: FakeScope(),
    )

    assert row is None


@pytest.mark.anyio
async def test_fetch_sync_child_runs_returns_empty_without_run_ids():
    async def fail_get_db_pool():
        raise AssertionError("should not open the database for an empty run list")

    rows = await sync_state.fetch_sync_child_runs(
        cartridge="sap_successfactors",
        run_ids=[],
        user=None,
        get_db_pool=fail_get_db_pool,
        pipeline_runs_scope_predicate=None,
        scoped_db_for_user=None,
        refresh_dag_run_status=None,
    )

    assert rows == []


@pytest.mark.anyio
async def test_fetch_sync_child_runs_refreshes_scoped_rows():
    class FakeConn:
        def __init__(self):
            self.calls = []

        async def fetch(self, query, *args):
            self.calls.append((query, args))
            return [
                {
                    "run_id": "entity-run-1",
                    "airflow_dag_run_id": "airflow-run-1",
                    "status": "running",
                },
                {
                    "run_id": "entity-run-2",
                    "airflow_dag_run_id": "airflow-run-2",
                    "status": "success",
                },
            ]

    class FakeScope:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn, "tenant-1", "workspace-1"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def get_db_pool():
        return object()

    async def scope_predicate(user, start_index, **kwargs):
        assert user == {"sub": "user-1"}
        assert start_index == 3
        assert kwargs == {"refresh_columns": True}
        return "AND tenant_id=$3", ["tenant-1"]

    refreshed = []

    async def refresh_dag_run_status(row, user):
        refreshed.append((row["run_id"], user))
        return {**row, "status": "refreshed"}

    conn = FakeConn()

    rows = await sync_state.fetch_sync_child_runs(
        cartridge="sap_successfactors",
        run_ids=["entity-run-1", "airflow-run-2"],
        user={"sub": "user-1"},
        get_db_pool=get_db_pool,
        pipeline_runs_scope_predicate=scope_predicate,
        scoped_db_for_user=lambda pool, user: FakeScope(conn),
        refresh_dag_run_status=refresh_dag_run_status,
    )

    assert [row["status"] for row in rows] == ["refreshed", "refreshed"]
    assert refreshed == [
        ("entity-run-1", {"sub": "user-1"}),
        ("entity-run-2", {"sub": "user-1"}),
    ]
    query, args = conn.calls[0]
    assert "run_id = ANY($1::text[])" in query
    assert "airflow_dag_run_id = ANY($1::text[])" in query
    assert "cartridge_id=$2" in query
    assert "entity <> '__sync_now__'" in query
    assert "AND tenant_id=$3" in query
    assert "ORDER BY started_at ASC" in query
    assert args == (
        ["entity-run-1", "airflow-run-2"],
        "sap_successfactors",
        "tenant-1",
    )


def test_sync_state_public_payload_uses_project_terminal_statuses():
    payload = sync_state.sync_public_payload(
        {"run_id": "r1", "status": "partial", "cartridge_id": "sap_successfactors"},
        {"steps": [{"id": "connection", "status": "success"}]},
    )

    assert payload["active"] is False
    assert payload["progress_percent"] == 100
