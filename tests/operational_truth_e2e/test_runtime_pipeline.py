from __future__ import annotations

import uuid

from .clients import (
    assert_airflow_replay_rejected,
    assert_refinement_rejects_bad_signatures,
    login_and_control_room_diagnostics,
    trigger_dataset_chain,
    trigger_file_ingest,
    wait_chain_for_workspace,
    wait_dag_run,
    wait_for_airflow_contract,
)
from .evidence import (
    assert_airflow_role_isolation,
    assert_real_parquet_chain,
    control_room_diagnostic_count,
    durable_counts,
    item_ids,
)
from .faults import (
    assert_chain_status,
    lineage_count,
    seed_completed_run_without_signal,
    temporarily_unavailable_table,
)
from .seed import PASSWORD, seed_two_workspaces, upload_csv


def _assert_failed_table_canary(scope: dict[str, str], table: str) -> None:
    before = lineage_count(scope)
    with temporarily_unavailable_table(table):
        run_id = trigger_dataset_chain(scope)
        wait_dag_run("dataset_refresh_chain", run_id, expected_state="failed")
    assert_chain_status(scope, run_id, "failed")
    if table in {"silver_lineage", "data_catalog"}:
        assert lineage_count(scope) == before


def test_real_operational_truth_pipeline_two_workspaces():
    wait_for_airflow_contract()
    scopes = seed_two_workspaces()
    for scope in scopes:
        upload_csv(scope)

    run_ids = []
    chain_runs = []
    for scope in scopes:
        run_id = trigger_file_ingest(scope)
        run_ids.append(run_id)
        wait_dag_run("file_ingest", run_id)
        chain_runs.append(wait_chain_for_workspace(scope["workspace_id"]))

    assert len({run["dag_run_id"] for run in chain_runs}) == 2
    for scope in scopes:
        assert_real_parquet_chain(scope)
        counts = durable_counts(scope)
        assert counts["lineage"] == 2
        assert counts["catalog"] >= 2
        assert counts["slots"] == 2
        assert counts["chain"] == 1
        assert counts["intelligence"] == 1
        assert counts["signals"] >= 1
        assert counts["items"] >= 1
        assert counts["gold_rows"] == 3

    own_ids = [item_ids(scope) for scope in scopes]
    assert own_ids[0]
    assert own_ids[1]
    assert own_ids[0].isdisjoint(own_ids[1])
    diagnostics = [
        login_and_control_room_diagnostics(scope["email"], PASSWORD) for scope in scopes
    ]
    for payload, item_ids_for_scope in zip(diagnostics, own_ids, strict=True):
        assert control_room_diagnostic_count(payload) >= len(item_ids_for_scope)
        assert "item_id" not in str(payload)

    assert_airflow_role_isolation(scopes[0], scopes[1])
    assert_refinement_rejects_bad_signatures(scopes[0])
    assert_airflow_replay_rejected(run_ids[0], scopes[0])

    for table in ("silver_lineage", "data_catalog", "intelligence_runs"):
        _assert_failed_table_canary(scopes[0], table)

    no_signal_run = f"operational_truth_no_signal__{uuid.uuid4().hex}"
    seed_completed_run_without_signal(scopes[0], no_signal_run)
    trigger_dataset_chain(scopes[0], run_id=no_signal_run)
    wait_dag_run("dataset_refresh_chain", no_signal_run, expected_state="failed")
    assert_chain_status(scopes[0], no_signal_run, "failed")
