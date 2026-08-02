from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

import pytest

from refinement.app.publication_contract import PublicationIdentity
from refinement.app.publication_inputs import resolve_input_state
from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.staged_publication_live import LiveStack, valid_lineage
from tests.test_staged_publication_authority_live import (
    _parquet_bytes,
    _put_pending_silver,
)
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import staged_publication_live_stack


def _identity(engine, dataset: dict) -> PublicationIdentity:
    state = resolve_input_state(engine, dataset, _scope())
    return PublicationIdentity.build(dataset, _scope(), input_state=state)


def _prepare_silver(
    engine,
    stack: LiveStack,
    dataset: dict,
    identity: PublicationIdentity | None = None,
) -> PublicationIdentity:
    identity = identity or _identity(engine, dataset)
    engine._publication_store.reserve(identity)
    raw = _parquet_bytes(7)
    uri, checksum = _put_pending_silver(
        stack, dataset["name"], identity.materialization_run_id, raw
    )
    lineage = json.loads(
        stack.bound_lineage(identity.materialization_run_id, valid_lineage())
    )
    catalog = [
        {"name": "tenant_id", "type": "string"},
        {"name": "workspace_id", "type": "string"},
        {"name": "value", "type": "int64"},
    ]
    stack.attest(
        identity.materialization_run_id,
        uri=uri,
        checksum=checksum,
        row_count=1,
        lineage=json.dumps(lineage),
        catalog=json.dumps(catalog),
    )
    engine._publication_store.mark_prepared(
        identity,
        object_uri=uri,
        object_version=stack.object_version(uri),
        object_checksum=checksum,
        row_count=1,
        staging_table=None,
        lineage=lineage,
        catalog=catalog,
    )
    return identity


def _identity_after_head(
    engine, dataset: dict, head_run_id: str
) -> PublicationIdentity:
    state = resolve_input_state(engine, dataset, _scope())
    return PublicationIdentity.build(
        dataset,
        _scope(),
        input_state=state,
        expected_head_run_id=head_run_id,
    )


def _expire_attestation(stack: LiveStack, identity: PublicationIdentity) -> None:
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "UPDATE omega_publication.materialization_attestations "
        "SET expires_at=clock_timestamp()-interval '1 second' "
        "WHERE materialization_run_id=%s",
        (str(identity.materialization_run_id),),
        fetch=False,
    )


def test_expired_prepared_authority_recovers_once_under_concurrent_workers(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "expired_concurrent_recovery_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": "SELECT 7::INTEGER AS value",
        "sources": [],
    }
    first = _engine(stack, monkeypatch)
    identity = _prepare_silver(first, stack, dataset)
    _expire_attestation(stack, identity)
    engines = [first, _engine(stack, monkeypatch)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(lambda item: item.materialize(dataset, _scope()), engines)
        )
    assert [item["row_count"] for item in results] == [1, 1]
    counts = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT (SELECT count(*) FROM omega_publication.materialization_receipts "
        "WHERE materialization_run_id=%s),"
        "(SELECT count(*) FROM omega_publication.materialization_evidence "
        "WHERE materialization_run_id=%s),"
        "(SELECT count(*) FROM omega_publication.dataset_publication_heads "
        "WHERE materialization_run_id=%s)",
        tuple([str(identity.materialization_run_id)] * 3),
    )[0]
    assert counts == (1, 1, 1)


def test_missing_gold_stage_is_rebuilt_from_exact_versioned_parquet(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "missing_gold_stage_recovery_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sql_def": "SELECT 11::BIGINT AS value",
        "sources": [],
    }
    engine = _engine(stack, monkeypatch)
    identity = _identity(engine, dataset)
    engine._publication_store.reserve(identity)
    sql_catalog = [
        {"name": "tenant_id", "type": "TEXT"},
        {"name": "workspace_id", "type": "TEXT"},
        {"name": "value", "type": "BIGINT"},
    ]
    stage = stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT omega_publication.create_gold_stage(%s,%s::jsonb)",
        (str(identity.materialization_run_id), json.dumps(sql_catalog)),
    )[0][0]
    stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        f'INSERT INTO omega_publication_stage."{stage}" VALUES(%s,%s,11)',
        (TENANT_A, WORKSPACE_A),
        fetch=False,
    )
    raw = _parquet_bytes(11)
    checksum = hashlib.sha256(raw).hexdigest()
    key = (
        f"gold/acceptance/{dataset['name']}/tenant_id={TENANT_A}/"
        f"workspace_id={WORKSPACE_A}/_pending/"
        f"{identity.materialization_run_id.hex}/{checksum}.parquet"
    )
    stack.s3.put_object(Bucket="lakehouse", Key=key, Body=raw)
    uri = f"s3://lakehouse/{key}"
    lineage = json.loads(
        stack.bound_lineage(identity.materialization_run_id, valid_lineage())
    )
    parquet_catalog = [
        {"name": "tenant_id", "type": "string"},
        {"name": "workspace_id", "type": "string"},
        {"name": "value", "type": "int64"},
    ]
    stack.attest(
        identity.materialization_run_id,
        uri=uri,
        checksum=checksum,
        row_count=1,
        lineage=json.dumps(lineage),
        catalog=json.dumps(parquet_catalog),
    )
    engine._publication_store.mark_prepared(
        identity,
        object_uri=uri,
        object_version=stack.object_version(uri),
        object_checksum=checksum,
        row_count=1,
        staging_table=stage,
        lineage=lineage,
        catalog=parquet_catalog,
    )
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        f'DROP TABLE omega_publication_stage."{stage}"',
        fetch=False,
    )
    assert engine.materialize(dataset, _scope())["row_count"] == 1
    assert stack.rows(dataset["name"]) == [(11,)]


def test_publish_failure_keeps_exact_revalidated_prepared_state_for_retry(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "prepared_publish_retry_probe",
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
        "CREATE FUNCTION omega_publication.reject_test_receipt() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'receipt unavailable'; END $$",
        fetch=False,
    )
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "CREATE TRIGGER reject_test_receipt BEFORE INSERT ON "
        "omega_publication.materialization_receipts FOR EACH ROW "
        "EXECUTE FUNCTION omega_publication.reject_test_receipt()",
        fetch=False,
    )
    try:
        with pytest.raises(RuntimeError, match="retry is required"):
            engine.materialize(dataset, _scope())
    finally:
        stack.sql(
            stack.admin_dsn,
            (TENANT_A, WORKSPACE_A),
            "DROP TRIGGER reject_test_receipt ON "
            "omega_publication.materialization_receipts",
            fetch=False,
        )
        stack.sql(
            stack.admin_dsn,
            (TENANT_A, WORKSPACE_A),
            "DROP FUNCTION omega_publication.reject_test_receipt()",
            fetch=False,
        )
    run_id = str(identity.materialization_run_id)
    prepared = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status,object_uri,object_version,object_checksum,row_count "
        "FROM omega_publication.materialization_runs WHERE materialization_run_id=%s",
        (run_id,),
    )[0]
    assert prepared[0] == "prepared"
    assert all(value not in (None, "") for value in prepared[1:])
    assert engine.materialize(dataset, _scope())["row_count"] == 1
    counts = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT (SELECT count(*) FROM omega_publication.materialization_evidence "
        "WHERE materialization_run_id=%s),(SELECT count(*) FROM "
        "omega_publication.materialization_receipts WHERE materialization_run_id=%s),"
        "(SELECT count(*) FROM omega_publication.dataset_publication_heads "
        "WHERE materialization_run_id=%s)",
        (run_id, run_id, run_id),
    )[0]
    assert counts == (1, 1, 1)
