from __future__ import annotations

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

