from __future__ import annotations

import inspect
import json

from scripts import sap_successfactors_aws_live_max as runner


def test_airflow_run_state_reads_dag_run_id() -> None:
    output = json.dumps(
        [
            {"dag_run_id": "manual__live", "state": "success"},
            {"dag_run_id": "manual__other", "state": "failed"},
        ]
    )

    assert runner._airflow_run_state_from_list_runs(output, "manual__live") == "success"


def test_airflow_run_state_reads_legacy_run_id() -> None:
    output = json.dumps([{"run_id": "scheduled__live", "state": "running"}])

    assert runner._airflow_run_state_from_list_runs(output, "scheduled__live") == "running"


def test_airflow_run_state_handles_bad_json() -> None:
    assert runner._airflow_run_state_from_list_runs("not-json", "manual__live") == "unknown"


def test_json_marker_uses_last_valid_marker() -> None:
    output = "\n".join(
        [
            'DB_SUMMARY_JSON=" + json.dumps(out)',
            'DB_SUMMARY_JSON={"status":"first"}',
            'DB_SUMMARY_JSON={bad-json',
            'DB_SUMMARY_JSON={"status":"last"}',
        ]
    )

    assert runner._parse_json_marker(output, "DB_SUMMARY_JSON") == {"status": "last"}


def test_trigger_extract_all_has_timeout_and_metadata_db_fallback() -> None:
    source = inspect.getsource(runner.trigger_extract_all)

    assert 'timeout "$AIRFLOW_TIMEOUT"' in source
    assert "dag_run_id" in source
    assert "read_state_db" in source
    assert "airflow_state_db_fallback" in source


def test_copilot_live_runner_executes_real_turns() -> None:
    source = inspect.getsource(runner.validate_copilot)

    assert "live chat token/session not available" not in source
    assert "copilot_service.create_conversation" in source
    assert "copilot_service.run_turn" in source
    assert "async def run_prompt" in source
    assert "failed_secret_leak" in source


def test_gold_missing_reasons_are_explicit() -> None:
    assert runner.GOLD_MISSING_REASONS["sap_successfactors_compensation_full"][0] == "SUCCESSFACTORS_PERMISSION"
    assert runner.GOLD_MISSING_REASONS["sap_successfactors_turnover_by_period"][0] == "AUTH_SCOPE_BLOCKED"
