from __future__ import annotations

import uuid

from .clients import (
    assert_airflow_replay_rejected,
    login_and_control_room_diagnostics,
    task_instance_states,
    trigger_dataset_chain,
    trigger_file_ingest,
    wait_chain_for_workspace,
    wait_dag_run,
    wait_for_airflow_contract,
)
from .evidence import (
    assert_airflow_role_isolation,
    assert_publication_reader_isolation,
    assert_real_parquet_chain,
    control_room_diagnostic_count,
    durable_counts,
    item_ids,
    publication_snapshot,
)
from .faults import (
    assert_real_http_ok_false_fails_closed,
    assert_reclaimed_slot,
    bump_dataset_contract,
    intelligence_count,
    pipeline_status,
    prepared_runs,
    put_unpublished_object,
    rewrite_raw_parquet,
    seed_completed_run_without_signal,
    seed_expired_materialization_slot,
    unavailable_intelligence_runs,
    unavailable_publication_table,
)
from .security import (
    assert_mcp_raw_cross_tenant_denied,
    assert_mcp_unpublished_object_hidden,
    assert_refinement_hmac_replay_rejected,
    assert_refinement_rejects_bad_signatures,
)
from .seed import PASSWORD, seed_two_workspaces, upload_csv


def _run(scope: dict[str, str], *, seed: str = "pnl_mensual") -> str:
    run_id = f"operational_truth_canary__{uuid.uuid4().hex}"
    trigger_dataset_chain(scope, run_id=run_id, seed_dataset=seed)
    return run_id


def _assert_initial_truth(scopes: list[dict[str, str]]) -> None:
    for scope in scopes:
        assert_real_parquet_chain(scope)
        counts = durable_counts(scope)
        assert counts["publication_runs"] == 2
        assert counts["heads"] == 2
        assert counts["receipts"] == 2
        assert counts["evidence"] == 2
        assert counts["slots"] == 2
        assert counts["chain"] == 1
        assert counts["intelligence"] == 1
        assert counts["signals"] >= 1
        assert counts["items"] >= 1
        assert counts["gold_rows"] == 3


def _assert_prepared_retry(scope: dict[str, str]) -> None:
    before = publication_snapshot(scope)
    bump_dataset_contract(scope, "pnl_mensual")
    with unavailable_publication_table("materialization_receipts"):
        failed_run = _run(scope)
        wait_dag_run("dataset_refresh_chain", failed_run, expected_state="failed")
    assert publication_snapshot(scope) == before
    assert pipeline_status(scope, failed_run) == "failed"
    prepared = prepared_runs(scope)
    assert len(prepared) == 1

    retry_run = _run(scope)
    wait_dag_run("dataset_refresh_chain", retry_run)
    after = publication_snapshot(scope)
    assert after["operational_truth_silver"] == before["operational_truth_silver"]
    assert after["pnl_mensual"][0] == prepared[0]
    assert after["pnl_mensual"][1] == before["pnl_mensual"][1] + 1


def _assert_cas_race(scope: dict[str, str]) -> None:
    before = publication_snapshot(scope)
    rewrite_raw_parquet(scope, 2)
    first = _run(scope, seed="operational_truth_silver")
    second = _run(scope, seed="operational_truth_silver")
    wait_dag_run("dataset_refresh_chain", first)
    wait_dag_run("dataset_refresh_chain", second)
    after = publication_snapshot(scope)
    for dataset in ("operational_truth_silver", "pnl_mensual"):
        assert after[dataset][0] != before[dataset][0]
        assert after[dataset][1] == before[dataset][1] + 1
    assert pipeline_status(scope, first) == "success"
    assert pipeline_status(scope, second) == "success"


def _assert_expired_lease_recovery(scope: dict[str, str]) -> None:
    run_id = f"operational_truth_lease__{uuid.uuid4().hex}"
    slot = seed_expired_materialization_slot(
        scope, airflow_run_id=run_id, dataset="pnl_mensual"
    )
    trigger_dataset_chain(scope, run_id=run_id)
    wait_dag_run("dataset_refresh_chain", run_id)
    assert_reclaimed_slot(scope, slot)


def _assert_downstream_failure_never_green(scope: dict[str, str]) -> None:
    before = publication_snapshot(scope)
    with unavailable_intelligence_runs():
        failed = _run(scope)
        wait_dag_run("dataset_refresh_chain", failed, expected_state="failed")
    assert publication_snapshot(scope) == before
    assert pipeline_status(scope, failed) == "failed"

    no_signal = f"operational_truth_no_signal__{uuid.uuid4().hex}"
    seed_completed_run_without_signal(scope, no_signal)
    trigger_dataset_chain(scope, run_id=no_signal)
    wait_dag_run("dataset_refresh_chain", no_signal, expected_state="failed")
    assert pipeline_status(scope, no_signal) == "failed"


def _assert_pre_xcom_failure(scope: dict[str, str]) -> None:
    before = intelligence_count(scope)
    run_id = _run(scope, seed=f"missing_{uuid.uuid4().hex}")
    wait_dag_run("dataset_refresh_chain", run_id, expected_state="failed")
    states = task_instance_states("dataset_refresh_chain", run_id)
    assert states["resolve_chain"] == "failed"
    assert states["materialize_in_order"] == "upstream_failed"
    assert pipeline_status(scope, run_id) == "failed"
    assert intelligence_count(scope) == before


def test_real_operational_truth_pipeline_two_workspaces() -> None:
    wait_for_airflow_contract()
    scopes = seed_two_workspaces()
    for scope in scopes:
        upload_csv(scope)

    file_runs = []
    chain_runs = []
    for scope in scopes:
        file_run = trigger_file_ingest(scope)
        file_runs.append(file_run)
        wait_dag_run("file_ingest", file_run)
        chain_runs.append(wait_chain_for_workspace(scope["workspace_id"]))
    assert len({run["dag_run_id"] for run in chain_runs}) == 2
    _assert_initial_truth(scopes)

    own_ids = [item_ids(scope) for scope in scopes]
    assert own_ids[0] and own_ids[1] and own_ids[0].isdisjoint(own_ids[1])
    for scope, expected_ids in zip(scopes, own_ids, strict=True):
        payload = login_and_control_room_diagnostics(scope["email"], PASSWORD)
        assert control_room_diagnostic_count(payload) >= len(expected_ids)
        assert "item_id" not in str(payload)

    assert_airflow_role_isolation(scopes[0], scopes[1])
    assert_publication_reader_isolation(scopes[0], scopes[1])
    assert_mcp_raw_cross_tenant_denied(scopes[0], scopes[1])
    assert_refinement_rejects_bad_signatures(scopes[0])
    assert_refinement_hmac_replay_rejected(scopes[0])
    assert_airflow_replay_rejected(file_runs[0], scopes[0])

    intelligence_before = intelligence_count(scopes[0])
    assert_real_http_ok_false_fails_closed()
    assert intelligence_count(scopes[0]) == intelligence_before

    _assert_prepared_retry(scopes[0])
    _assert_cas_race(scopes[0])
    _assert_expired_lease_recovery(scopes[0])
    _assert_downstream_failure_never_green(scopes[0])
    _assert_pre_xcom_failure(scopes[0])

    unpublished = put_unpublished_object(scopes[0])
    assert_mcp_unpublished_object_hidden(scopes[0], unpublished)
    assert_real_parquet_chain(scopes[0])
