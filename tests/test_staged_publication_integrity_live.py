from __future__ import annotations

import io
from urllib.parse import urlsplit

import pyarrow.parquet as pq
import pytest

from tests.staged_publication_canaries import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)
from tests.staged_publication_live import LiveStack
from tests.test_staged_publication_live import staged_publication_live_stack


def _engine(
    stack: LiveStack, monkeypatch: pytest.MonkeyPatch, bucket: str = "lakehouse"
):
    for name, value in {
        "GOLD_DATABASE_URL": stack.reader_dsn,
        "GOLD_PUBLISHER_DATABASE_URL": stack.publisher_dsn,
        "MINIO_ENDPOINT": stack.minio_endpoint.removeprefix("http://"),
        "MINIO_ACCESS_KEY": "minio",
        "MINIO_SECRET_KEY": "minio-secret",
        "MINIO_BUCKET": bucket,
        "MINIO_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    from refinement.app.staged_publication_engine import StagedPublicationEngine

    return StagedPublicationEngine()


def _scope(tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"tenant_id": tenant, "workspace_id": workspace}


def _object_key(uri: str) -> str:
    return urlsplit(uri).path.lstrip("/")


def test_gold_and_parquet_share_one_frozen_volatile_result(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    result = engine.materialize(
        {
            "name": "volatile_result_probe",
            "layer": "gold",
            "cartridge": "acceptance",
            "sql_def": "SELECT random() AS value",
            "sources": [],
        },
        _scope(),
    )
    gold_value = stack.rows("volatile_result_probe")[0][0]
    raw = stack.s3.get_object(
        Bucket="lakehouse",
        Key=_object_key(stack.public_object("volatile_result_probe")),
    )["Body"].read()
    parquet_value = pq.read_table(io.BytesIO(raw)).to_pylist()[0]["value"]
    assert gold_value == parquet_value
    assert result == {"name": "volatile_result_probe", "layer": "gold", "row_count": 1}


def test_published_object_tamper_is_detected_from_bytes(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    dataset = {
        "name": "object_integrity_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": "SELECT 1 AS value",
        "sources": [],
    }
    engine.materialize(dataset, _scope())
    uri = stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        """SELECT r.object_uri FROM omega_publication.dataset_publication_heads h
             JOIN omega_publication.materialization_runs r
               ON r.materialization_run_id=h.materialization_run_id
            WHERE h.dataset=%s AND h.layer='silver'""",
        ("object_integrity_probe",),
    )[0][0]
    checksum = stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT object_checksum FROM omega_publication.materialization_runs "
        "WHERE materialization_run_id=%s",
        (stack.head("object_integrity_probe")[0],),
    )[0][0]
    stack.s3.put_object(
        Bucket="lakehouse",
        Key=_object_key(uri),
        Body=b"tampered",
        Metadata={"omega-sha256": checksum},
    )
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        engine.materialize(dataset, _scope())


def test_same_dataset_name_can_publish_independent_workspace_schemas(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    base = {
        "name": "shared_schema_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sources": [],
    }
    engine.materialize({**base, "sql_def": "SELECT 1::INTEGER AS a"}, _scope())
    head_a = stack.head("shared_schema_probe")
    engine.materialize(
        {**base, "sql_def": "SELECT 2::INTEGER AS b"},
        _scope(TENANT_B, WORKSPACE_B),
    )
    assert stack.head("shared_schema_probe") == head_a
    assert stack.head("shared_schema_probe", (TENANT_B, WORKSPACE_B))[1] == 1
    rows_a = stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT a FROM " + stack.gold_table("shared_schema_probe"),
    )
    rows_b = stack.sql(
        stack.reader_dsn,
        (TENANT_B, WORKSPACE_B),
        "SELECT b FROM "
        + stack.gold_table("shared_schema_probe", (TENANT_B, WORKSPACE_B)),
    )
    assert rows_a == [(1,)] and rows_b == [(2,)]


def test_legacy_superset_relation_and_head_advance_in_one_snapshot(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    base = {
        "name": "superset_compat_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sources": [],
    }
    engine.materialize({**base, "sql_def": "SELECT 1::INTEGER AS value"}, _scope())
    first_head = stack.head("superset_compat_probe")[0]
    assert stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT value FROM public.gold_superset_compat_probe",
    ) == [(1,)]
    engine.materialize({**base, "sql_def": "SELECT 2::INTEGER AS value"}, _scope())
    assert stack.rows("superset_compat_probe") == [(2,)]
    assert stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT value FROM public.gold_superset_compat_probe",
    ) == [(2,)]
    assert stack.head("superset_compat_probe")[0] != first_head


def test_raw_input_is_pinned_and_change_is_rejected(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    prefix = f"raw/probe/events/tenant_id={TENANT_A}/workspace_id={WORKSPACE_A}"
    first = f"{prefix}/load_date=2026-07-31/batch_id=a/first.parquet"
    stack.s3.put_object(Bucket="lakehouse", Key=first, Body=b"first")
    dataset = {
        "name": "input_pin_probe",
        "layer": "silver",
        "sources": ["raw/probe/events"],
    }
    from refinement.app.publication_inputs import resolve_input_state

    captured = resolve_input_state(engine, dataset, _scope())
    engine._publication_local.state = {"input_state": captured}
    try:
        sql = engine._scope_storage_sql(
            "SELECT * FROM read_parquet('s3://lakehouse/raw/probe/events/**/*.parquet')",
            dataset["sources"],
            _scope(),
        )
        assert first in sql and "**" not in sql
        second = f"{prefix}/load_date=2026-07-31/batch_id=b/second.parquet"
        stack.s3.put_object(Bucket="lakehouse", Key=second, Body=b"second")
        with pytest.raises(RuntimeError, match="dependencies changed"):
            engine._assert_inputs_unchanged(dataset, _scope())
    finally:
        engine._publication_local.state = None


def test_real_minio_failure_marks_run_failed_and_retry_is_safe(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "minio_failure_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sql_def": "SELECT 41::INTEGER AS value",
        "sources": [],
    }
    with pytest.raises(Exception):
        _engine(stack, monkeypatch, "missing-publication-bucket").materialize(
            dataset, _scope()
        )
    state = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status,staging_table FROM omega_publication.materialization_runs "
        "WHERE dataset='minio_failure_probe'",
    )
    assert state == [("failed", None)]
    assert stack.head("minio_failure_probe") is None
    result = _engine(stack, monkeypatch).materialize(dataset, _scope())
    assert result["row_count"] == 1
    assert stack.rows("minio_failure_probe") == [(41,)]
