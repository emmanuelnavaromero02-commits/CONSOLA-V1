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
