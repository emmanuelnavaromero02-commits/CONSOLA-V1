from __future__ import annotations

import uuid

import psycopg2
import pytest

from refinement.app.publication_inputs import resolve_input_state
from refinement.app.publication_snapshot import PublicationSnapshotResolver
from tests.staged_publication_canaries import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)
from tests.staged_publication_live import LiveStack, valid_catalog, valid_lineage
from tests.test_staged_publication_authority_live import (
    _parquet_bytes,
    _put_pending_silver,
    _reserve_silver,
)
from tests.test_staged_publication_live import staged_publication_live_stack
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_recovery_matrix_live import (
    _identity_after_head,
    _prepare_silver,
)


def _candidate(stack: LiveStack, dataset: str) -> tuple[uuid.UUID, uuid.UUID, str, str]:
    run = uuid.uuid4()
    _reserve_silver(stack, dataset, run)
    uri, checksum = _put_pending_silver(stack, dataset, run, _parquet_bytes(4))
    lineage = stack.bound_lineage(run, valid_lineage())
    candidate = stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT omega_publication.submit_verification_candidate("
        "%s,%s,%s,%s,1,%s::jsonb,%s::jsonb)",
        (
            str(run),
            uri,
            stack.object_version(uri),
            checksum,
            lineage,
            valid_catalog(),
        ),
    )[0][0]
    return run, candidate, uri, checksum


def test_reader_and_publisher_cannot_mint_and_verifier_cannot_publish(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run, candidate, _, _ = _candidate(stack, "role_boundary_probe")
    for dsn in (stack.reader_dsn, stack.publisher_dsn):
        with pytest.raises(psycopg2.Error) as denied:
            stack.sql(
                dsn,
                (TENANT_A, WORKSPACE_A),
                "SELECT omega_publication.record_attestation(%s)",
                (candidate,),
            )
        assert denied.value.pgcode == "42501"
    stack.sql(
        stack.verifier_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT omega_publication.record_attestation(%s)",
        (candidate,),
    )
    with pytest.raises(psycopg2.Error) as replay:
        stack.sql(
            stack.verifier_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT omega_publication.record_attestation(%s)",
            (candidate,),
        )
    assert replay.value.pgcode == "23514"
    with pytest.raises(psycopg2.Error) as denied:
        stack.sql(
            stack.verifier_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT * FROM omega_publication.publish_materialization(%s,NULL)",
            (str(run),),
        )
    assert denied.value.pgcode == "42501"
    assert stack.head("role_boundary_probe") is None


def test_publisher_functions_reject_cross_scope_run_capabilities(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run, _, uri, checksum = _candidate(stack, "cross_scope_capability_probe")
    calls = (
        (
            "SELECT omega_publication.submit_verification_candidate("
            "%s,%s,%s,%s,1,%s::jsonb,%s::jsonb)",
            (
                str(run),
                uri,
                stack.object_version(uri),
                checksum,
                valid_lineage(),
                valid_catalog(),
            ),
        ),
        (
            "SELECT omega_publication.abandon_materialization(%s)",
            (str(run),),
        ),
    )
    for query, params in calls:
        with pytest.raises(psycopg2.Error) as denied:
            stack.sql(
                stack.publisher_dsn,
                (TENANT_B, WORKSPACE_B),
                query,
                params,
            )
        assert denied.value.pgcode == "42501"
    status = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status FROM omega_publication.materialization_runs "
        "WHERE materialization_run_id=%s",
        (str(run),),
    )[0][0]
    assert status == "reserved"


def test_evidence_mismatch_leaves_recoverable_state_then_converges(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "prepared_evidence_mismatch_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": "SELECT 7::INTEGER AS value",
        "sources": [],
    }
    engine = _engine(stack, monkeypatch)
    identity = _prepare_silver(engine, stack, dataset)
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "UPDATE omega_publication.materialization_evidence "
        "SET object_checksum=%s WHERE materialization_run_id=%s",
        ("d" * 64, str(identity.materialization_run_id)),
        fetch=False,
    )
    with pytest.raises(RuntimeError, match="recovery is unavailable"):
        engine.materialize(dataset, _scope())
    status = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status FROM omega_publication.materialization_runs "
        "WHERE materialization_run_id=%s",
        (str(identity.materialization_run_id),),
    )[0][0]
    assert status == "recoverable_failed"
    assert engine.materialize(dataset, _scope())["row_count"] == 1


def test_prepared_cas_loser_is_quarantined_and_next_run_converges(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    baseline_ds = {
        "name": "prepared_cas_loser_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": "SELECT 1::INTEGER AS value",
        "sources": [],
    }
    baseline = _prepare_silver(engine, stack, baseline_ds)
    engine._publication_store.publish(baseline, None)
    baseline_run = str(baseline.materialization_run_id)
    loser_ds = {**baseline_ds, "sql_def": "SELECT 2::INTEGER AS value"}
    loser = _identity_after_head(engine, loser_ds, baseline_run)
    _prepare_silver(engine, stack, loser_ds, loser)
    winner_ds = {**baseline_ds, "sql_def": "SELECT 3::INTEGER AS value"}
    winner = _identity_after_head(engine, winner_ds, baseline_run)
    _prepare_silver(engine, stack, winner_ds, winner)
    engine._publication_store.publish(winner, baseline_run)
    input_state = resolve_input_state(engine, loser_ds, _scope())
    with engine._publication_store.run_lock(loser):
        with pytest.raises(Exception, match="publication head conflict"):
            engine._materialize_locked(loser_ds, _scope(), loser, input_state)
    status = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status FROM omega_publication.materialization_runs "
        "WHERE materialization_run_id=%s",
        (str(loser.materialization_run_id),),
    )[0][0]
    assert status == "recoverable_failed"
    assert engine.materialize(loser_ds, _scope())["row_count"] == 1


def test_receipt_generation_mismatch_is_not_a_published_snapshot(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "receipt_generation_mismatch_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sql_def": "SELECT 11::INTEGER AS value",
        "sources": [],
    }
    engine = _engine(stack, monkeypatch)
    engine.materialize(dataset, _scope())
    run_id, generation = stack.head(dataset["name"])
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "UPDATE omega_publication.materialization_receipts "
        "SET generation=generation+100 WHERE materialization_run_id=%s",
        (str(run_id),),
        fetch=False,
    )

    resolver = PublicationSnapshotResolver(engine.storage, stack.reader_dsn)
    with pytest.raises(RuntimeError, match="authority is incomplete"):
        resolver.published_snapshot(dataset, _scope())
    assert stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT count(*) FROM omega_publication.published_lineage "
        "WHERE dataset=%s AND generation=%s",
        (dataset["name"], generation),
    ) == [(0,)]
