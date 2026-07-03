from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domains.pipeline import run_state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("queued", "queued"),
        ("RUNNING", "running"),
        ("success", "success"),
        ("failed", "failed"),
        ("partial", "partial"),
        ("skipped", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalize_airflow_state(raw, expected):
    assert run_state.normalize_airflow_state(raw) == expected


def test_pipeline_run_extra_accepts_dict_and_json_object():
    assert run_state.pipeline_run_extra({"extra": {"result_status": "partial"}}) == {
        "result_status": "partial"
    }
    assert run_state.pipeline_run_extra({"extra": '{"rows": 12}'}) == {"rows": 12}
    assert run_state.pipeline_run_extra({"extra": "[1,2]"}) == {}
    assert run_state.pipeline_run_extra({"extra": "not-json"}) == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, True),
        ("0", True),
        (0.0, True),
        (False, False),
        (None, False),
        ("", False),
        ("12", False),
    ],
)
def test_pipeline_is_zero_count(value, expected):
    assert run_state.pipeline_is_zero_count(value) is expected


def test_airflow_log_task_ids_prefer_known_extract_task_when_available():
    tasks = [{"task_id": "start"}, {"task_id": "trigger_extract_all"}]

    assert run_state.airflow_log_task_ids(
        "sap_successfactors_extract_all", tasks
    ) == ["trigger_extract_all"]
    assert run_state.airflow_log_task_ids("unknown_dag", tasks) == [
        "start",
        "trigger_extract_all",
    ]
    assert run_state.airflow_log_task_ids("sap_successfactors_extract_all", []) == [
        "trigger_extract_all"
    ]


def test_duration_seconds_uses_iso_dates():
    assert run_state.duration_seconds(
        "2026-07-03T10:00:00+00:00",
        "2026-07-03T10:00:05+00:00",
    ) == 5.0
    assert run_state.duration_seconds("bad", "2026-07-03T10:00:05+00:00") is None


def test_pipeline_freshness_status_uses_thresholds():
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

    assert (
        run_state.pipeline_freshness_status(
            "2026-07-03T11:00:00+00:00", threshold_h=24, now=now
        )
        == "fresh"
    )
    assert (
        run_state.pipeline_freshness_status(
            "2026-07-01T11:00:00+00:00", threshold_h=24, now=now
        )
        == "stale"
    )
    assert run_state.pipeline_freshness_status(None, now=now) == "never"
    assert run_state.pipeline_freshness_status("bad", now=now) == "unknown"


def test_pipeline_dataset_status_prioritizes_failed_and_empty():
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

    assert (
        run_state.pipeline_dataset_status(
            {"row_count": 10, "last_refresh": "2026-07-03T11:00:00+00:00"},
            failed=True,
            now=now,
        )
        == "stale"
    )
    assert (
        run_state.pipeline_dataset_status(
            {"row_count": 0, "last_refresh": "2026-07-03T11:00:00+00:00"},
            failed=False,
            now=now,
        )
        == "empty"
    )


def test_pipeline_silver_and_gold_dependency_helpers():
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    silver = [
        {
            "name": "employee_profile",
            "sources": ["raw/sap/User", "raw/sap/EmpJob"],
            "row_count": 10,
            "last_refresh": "2026-07-03T11:00:00+00:00",
        },
        {"name": "empty", "sources": []},
    ]
    gold = [
        {
            "name": "talent_profile",
            "sql": "select * from silver_employee_profile",
            "sources": [],
            "row_count": 5,
            "last_refresh": "2026-07-03T11:00:00+00:00",
        },
        {
            "name": "talent_signals",
            "sql": "",
            "sources": ["silver/sap_successfactors/employee_profile"],
            "row_count": 0,
            "last_refresh": "2026-07-03T11:00:00+00:00",
        },
        {"name": "other", "sql": "select 1", "sources": ["silver/other/employee_profile"]},
    ]

    by_source = run_state.pipeline_silver_datasets_by_source(silver)
    assert by_source["raw/sap/User"] == [silver[0]]
    assert run_state.pipeline_gold_dependencies_for_silver(
        "employee_profile", gold, cartridge="sap_successfactors"
    ) == [gold[0], gold[1]]
    silver_nodes, gold_nodes = run_state.pipeline_silver_gold_nodes(
        source="raw/sap/User",
        silver_by_source=by_source,
        gold_datasets=gold,
        failed=False,
        cartridge="sap_successfactors",
        now=now,
    )
    assert silver_nodes == [
        {
            "name": "employee_profile",
            "layer": "silver",
            "row_count": 10,
            "last_refresh": "2026-07-03T11:00:00+00:00",
            "status": "fresh",
        }
    ]
    assert gold_nodes == [
        {
            "name": "talent_profile",
            "layer": "gold",
            "row_count": 5,
            "last_refresh": "2026-07-03T11:00:00+00:00",
            "status": "fresh",
        },
        {
            "name": "talent_signals",
            "layer": "gold",
            "row_count": 0,
            "last_refresh": "2026-07-03T11:00:00+00:00",
            "status": "empty",
        },
    ]


def test_pipeline_jobs_by_entity_keeps_first_job_per_entity():
    jobs = [
        {"id": 1, "args": {"entity": "User"}},
        {"id": 2, "args": {"entity": "User"}},
        {"id": 3, "args": {"entity": "EmpJob"}},
        {"id": 4, "args": {}},
    ]

    assert run_state.pipeline_jobs_by_entity(jobs) == {
        "User": jobs[0],
        "EmpJob": jobs[2],
    }


def test_pipeline_bronze_date_count_prefers_airflow_then_done_job():
    assert run_state.pipeline_bronze_date_count(
        {"finished_at": "2026-07-03T11:22:33+00:00", "record_count": 12},
        {"status": "done", "result": {"record_count": 99}},
    ) == ("2026-07-03", 12)
    assert run_state.pipeline_bronze_date_count(
        None,
        {
            "status": "done",
            "finished_at": "2026-07-02T10:00:00+00:00",
            "created_at": "2026-07-01T10:00:00+00:00",
            "result": {"record_count": 0, "total_records": 7},
        },
    ) == ("2026-07-02", 7)
    assert run_state.pipeline_bronze_date_count(None, {"status": "failed"}) == (
        None,
        None,
    )


def test_pipeline_bronze_status_orders_operational_states():
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

    assert (
        run_state.pipeline_bronze_status(
            dag_run={"status": "failed"},
            dag_status="failed",
            bronze_date=None,
            bronze_count=None,
            has_partial_reasons=False,
            now=now,
        )
        == "error"
    )
    assert (
        run_state.pipeline_bronze_status(
            dag_run=None,
            dag_status="running",
            bronze_date=None,
            bronze_count=None,
            has_partial_reasons=False,
            now=now,
        )
        == "running"
    )
    assert (
        run_state.pipeline_bronze_status(
            dag_run=None,
            dag_status="partial",
            bronze_date="2026-07-03",
            bronze_count=10,
            has_partial_reasons=False,
            now=now,
        )
        == "partial"
    )
    assert (
        run_state.pipeline_bronze_status(
            dag_run=None,
            dag_status=None,
            bronze_date="2026-07-03",
            bronze_count=0,
            has_partial_reasons=False,
            now=now,
        )
        == "empty"
    )
    assert (
        run_state.pipeline_bronze_status(
            dag_run=None,
            dag_status=None,
            bronze_date=None,
            bronze_count=None,
            has_partial_reasons=True,
            now=now,
        )
        == "unknown"
    )
    assert (
        run_state.pipeline_bronze_status(
            dag_run=None,
            dag_status=None,
            bronze_date="2026-07-03",
            bronze_count=3,
            has_partial_reasons=False,
            now=now,
        )
        == "fresh"
    )
    assert (
        run_state.pipeline_bronze_status(
            dag_run=None,
            dag_status=None,
            bronze_date=None,
            bronze_count=None,
            has_partial_reasons=False,
            now=now,
        )
        == "never"
    )


def test_pipeline_last_run_payload_helpers_preserve_viewer_contract():
    airflow_info, dag_status = run_state.pipeline_airflow_last_run_info(
        {
            "dag_id": "sap_successfactors_extract",
            "run_id": "run-1",
            "airflow_dag_run_id": "dag-run-1",
            "status": "success",
            "mode": "incremental",
            "started_at": "2026-07-03T10:00:00+00:00",
            "finished_at": "2026-07-03T10:02:00+00:00",
            "duration_seconds": 120,
            "record_count": 1288,
            "extra": {
                "result_status": "partial",
                "empty_result": False,
                "silver_refresh": {"status": "success"},
            },
        }
    )

    assert dag_status == "success"
    assert airflow_info == {
        "source": "airflow",
        "dag_id": "sap_successfactors_extract",
        "dag_run_id": "dag-run-1",
        "run_id": "dag-run-1",
        "status": "success",
        "result_status": "partial",
        "silver_refresh_status": "success",
        "empty_result": False,
        "extra": {
            "result_status": "partial",
            "empty_result": False,
            "silver_refresh": {"status": "success"},
        },
        "mode": "incremental",
        "triggered_at": "2026-07-03T10:00:00+00:00",
        "started_at": "2026-07-03T10:00:00+00:00",
        "finished_at": "2026-07-03T10:02:00+00:00",
        "duration_sec": 120.0,
        "error": None,
    }

    assert run_state.pipeline_job_last_run_info(
        {
            "job_id": "job-1",
            "status": "done",
            "finished_at": "2026-07-03T10:02:00+00:00",
            "message": "ok",
        }
    ) == {
        "source": "jobs",
        "job_id": "job-1",
        "status": "done",
        "finished_at": "2026-07-03T10:02:00+00:00",
        "message": "ok",
    }
    assert run_state.pipeline_bronze_last_run_info(
        bronze_date="2026-07-03", bronze_count=0, mode="full"
    ) == {
        "source": "bronze",
        "status": "empty",
        "mode": "full",
        "finished_at": "2026-07-03T00:00:00+00:00",
        "message": "Bronze materializado; corrida no registrada en pipeline_runs",
        "record_count": 0,
    }
    assert run_state.pipeline_legacy_last_job(airflow_info) == {
        "job_id": None,
        "dag_id": "sap_successfactors_extract",
        "dag_run_id": "dag-run-1",
        "status": "success",
        "mode": "incremental",
        "triggered_at": "2026-07-03T10:00:00+00:00",
        "finished_at": "2026-07-03T10:02:00+00:00",
        "duration_sec": 120.0,
        "created_at": "2026-07-03T10:02:00+00:00",
        "message": None,
    }
    assert run_state.pipeline_legacy_last_job(None) is None
