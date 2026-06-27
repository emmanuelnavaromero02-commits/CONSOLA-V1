from __future__ import annotations

import inspect
import json

from scripts import sap_successfactors_aws_live_max as runner


def _ctx() -> runner.Context:
    return runner.Context(
        run_id="test-run",
        timestamp="20260627T000000Z",
        evidence_dir=runner.REPO / "tmp" / "test-run",
        instance_id="i-test",
        region="us-east-1",
        console_url="https://console.example.test",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000002",
        conn_id="sf",
        bucket="bucket",
        trigger_extract=False,
        max_wait_seconds=1,
    )


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


def test_public_http_follows_https_redirects() -> None:
    source = inspect.getsource(runner._http)

    assert '"curl"' in source
    assert '"-L"' in source


def test_catalog_dataset_coverage_counts_talent_gold() -> None:
    ctx = _ctx()

    runner._apply_catalog_dataset_coverage(
        ctx,
        "sap_successfactors_talent_operational_features",
        1,
    )

    cov = ctx.coverage["talent_operational_features"]
    assert cov.gold_dataset == "sap_successfactors_talent_operational_features"
    assert cov.rows_extracted == 1
    assert cov.status == "gold-ready"
    assert cov.app_visible == "yes"


def test_final_status_allows_expected_optional_warnings_when_required_gold_exists() -> None:
    ctx = _ctx()
    for dataset in runner.REQUIRED_GOLD_DATASETS:
        runner._apply_catalog_dataset_coverage(ctx, dataset, 1)
    runner._record(ctx, "Gold dataset sap_successfactors_turnover_by_period", "WARN", "AUTH_SCOPE_BLOCKED", "evidence")

    assert runner._final_status(ctx) == "GREEN"


def test_final_status_warns_when_required_gold_is_missing() -> None:
    ctx = _ctx()
    runner._record(ctx, "Gold dataset sap_successfactors_turnover_by_period", "WARN", "AUTH_SCOPE_BLOCKED", "evidence")

    assert runner._final_status(ctx) == "YELLOW"
