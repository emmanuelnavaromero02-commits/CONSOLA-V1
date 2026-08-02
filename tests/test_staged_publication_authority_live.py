from __future__ import annotations

import hashlib
import io
import json
import uuid
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg2
import pytest

from refinement.app.publication_contract import PublicationIdentity
from refinement.app.publication_inputs import resolve_input_state
from tests.staged_publication_canaries import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)
from tests.staged_publication_live import LiveStack, valid_catalog, valid_lineage
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import staged_publication_live_stack


def _parquet_bytes(value: int) -> bytes:
    sink = io.BytesIO()
    pq.write_table(
        pa.table(
            {
                "tenant_id": [TENANT_A],
                "workspace_id": [WORKSPACE_A],
                "value": [value],
            }
        ),
        sink,
    )
    return sink.getvalue()


def _put_pending_silver(
    stack: LiveStack, dataset: str, run: uuid.UUID, raw: bytes
) -> tuple[str, str]:
    checksum = hashlib.sha256(raw).hexdigest()
    key = (
        f"silver/acceptance/{dataset}/tenant_id={TENANT_A}/"
        f"workspace_id={WORKSPACE_A}/_pending/{run.hex}/{checksum}.parquet"
    )
    stack.s3.put_object(
        Bucket="lakehouse",
        Key=key,
        Body=raw,
        Metadata={"omega-sha256": checksum},
    )
    return f"s3://lakehouse/{key}", checksum


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _reserve_silver(stack: LiveStack, dataset: str, run: uuid.UUID) -> None:
    stack.sql(
        stack.publisher_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT * FROM omega_publication.reserve_materialization("
        "%s,%s,%s,%s,'silver',%s,%s,NULL)",
        (str(run), TENANT_A, WORKSPACE_A, dataset, "a" * 64, "b" * 64),
    )


def test_gold_compatibility_relation_is_physical_scope_identity(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    dataset = {
        "name": "same_visible_dataset",
        "layer": "gold",
        "cartridge": "acceptance",
        "sources": [],
    }
    engine.materialize({**dataset, "sql_def": "SELECT 1::INTEGER AS value"}, _scope())
    engine.materialize(
        {**dataset, "sql_def": "SELECT 'dos'::TEXT AS value"},
        _scope(TENANT_B, WORKSPACE_B),
    )
    mappings = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT tenant_id::text,workspace_id::text,relation_name "
        "FROM omega_publication.dataset_gold_relations WHERE dataset=%s "
        "ORDER BY tenant_id,workspace_id",
        (dataset["name"],),
    )
    assert len(mappings) == 2
    assert len({row[2] for row in mappings}) == 2
    assert all(len(row[2].encode()) <= 63 for row in mappings)


def test_silver_prepare_rejects_caller_owned_fake_evidence(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run = uuid.uuid4()
    _reserve_silver(stack, "caller_evidence_probe", run)
    uri, checksum = _put_pending_silver(
        stack, "caller_evidence_probe", run, _parquet_bytes(7)
    )
    forged_catalog = json.dumps([{"name": "forged", "type": "TEXT"}])
    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.publisher_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT * FROM omega_publication.mark_prepared("
            "%s,%s,%s,999,NULL,NULL,%s::jsonb,%s::jsonb)",
            (str(run), uri, checksum, valid_lineage(), forged_catalog),
        )
    assert stack.head("caller_evidence_probe") is None


def test_publisher_cannot_mint_server_attestation(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run = uuid.uuid4()
    _reserve_silver(stack, "attestation_privilege_probe", run)
    uri, checksum = _put_pending_silver(
        stack, "attestation_privilege_probe", run, _parquet_bytes(3)
    )
    lineage = stack.bound_lineage(run, valid_lineage())
    with pytest.raises(psycopg2.Error) as denied:
        stack.sql(
            stack.publisher_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT omega_publication.record_attestation("
            "%s,%s,%s,1,%s::jsonb,%s::jsonb)",
            (str(run), uri, checksum, lineage, valid_catalog()),
        )
    assert denied.value.pgcode == "42501"
    assert stack.head("attestation_privilege_probe") is None


@pytest.mark.parametrize("authority_state", ["expired", "invalidated"])
def test_publish_rechecks_live_attestation_authority(
    staged_publication_live_stack: LiveStack,
    authority_state: str,
) -> None:
    stack = staged_publication_live_stack
    dataset = f"attestation_{authority_state}_probe"
    run = uuid.uuid4()
    stack.reserve(dataset, run)
    stack.prepare(dataset, run, 5)
    assignment = (
        "expires_at=clock_timestamp()-interval '1 second'"
        if authority_state == "expired"
        else "invalidated_at=clock_timestamp()"
    )
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        f"UPDATE omega_publication.materialization_attestations "
        f"SET {assignment} WHERE materialization_run_id=%s",
        (str(run),),
        fetch=False,
    )
    with pytest.raises(psycopg2.Error) as denied:
        stack.publish(run, None)
    assert denied.value.pgcode == "23514"
    assert stack.head(dataset) is None


@pytest.mark.asyncio
async def test_console_rejects_mutated_bytes_for_published_head(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    dataset = {
        "name": "mutable_head_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": "SELECT 1::INTEGER AS value",
        "sources": [],
    }
    engine.materialize(dataset, _scope())
    uri = _text(stack.public_object("mutable_head_probe"))
    key = urlsplit(uri).path.lstrip("/")
    assert key
    stack.s3.put_object(Bucket="lakehouse", Key=key, Body=_parquet_bytes(99))
    monkeypatch.setenv("GOLD_DATABASE_URL", stack.reader_dsn)
    from app.services.publication_heads import verified_published_object

    raw = await verified_published_object(
        stack.s3,
        key,
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
        "lakehouse",
    )
    assert raw is None


def test_corrupt_prepared_run_becomes_recoverable_instead_of_stuck(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    dataset = {
        "name": "prepared_recovery_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": "SELECT 8::INTEGER AS value",
        "sources": [],
    }
    state = resolve_input_state(engine, dataset, _scope())
    identity = PublicationIdentity.build(dataset, _scope(), input_state=state)
    engine._publication_store.reserve(identity)
    uri, checksum = _put_pending_silver(
        stack, dataset["name"], identity.materialization_run_id, _parquet_bytes(8)
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
        object_checksum=checksum,
        row_count=1,
        staging_table=None,
        lineage=lineage,
        catalog=catalog,
    )
    stack.s3.delete_object(Bucket="lakehouse", Key=urlsplit(uri).path.lstrip("/"))

    with pytest.raises(RuntimeError, match="recoverable"):
        engine.materialize(dataset, _scope())
    status = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status FROM omega_publication.materialization_runs "
        "WHERE materialization_run_id=%s",
        (str(identity.materialization_run_id),),
    )[0][0]
    assert status == "recoverable_failed"
    result = engine.materialize(dataset, _scope())
    assert result == {"name": dataset["name"], "layer": "silver", "row_count": 1}
    assert stack.head(dataset["name"])[1] == 1
    counts = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT "
        "(SELECT count(*) FROM omega_publication.materialization_receipts "
        " WHERE materialization_run_id=%s),"
        "(SELECT count(*) FROM omega_publication.materialization_evidence "
        " WHERE materialization_run_id=%s),"
        "(SELECT count(*) FROM omega_publication.materialization_recovery_events "
        " WHERE materialization_run_id=%s)",
        tuple([str(identity.materialization_run_id)] * 3),
    )[0]
    assert counts == (1, 1, 1)
