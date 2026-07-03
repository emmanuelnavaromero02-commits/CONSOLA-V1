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
    silver = [
        {"name": "employee_profile", "sources": ["raw/sap/User", "raw/sap/EmpJob"]},
        {"name": "empty", "sources": []},
    ]
    gold = [
        {
            "name": "talent_profile",
            "sql": "select * from silver_employee_profile",
            "sources": [],
        },
        {
            "name": "talent_signals",
            "sql": "",
            "sources": ["silver/sap_successfactors/employee_profile"],
        },
        {"name": "other", "sql": "select 1", "sources": ["silver/other/employee_profile"]},
    ]

    by_source = run_state.pipeline_silver_datasets_by_source(silver)
    assert by_source["raw/sap/User"] == [silver[0]]
    assert run_state.pipeline_gold_dependencies_for_silver(
        "employee_profile", gold, cartridge="sap_successfactors"
    ) == [gold[0], gold[1]]
