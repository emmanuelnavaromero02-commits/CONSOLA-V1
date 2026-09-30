from __future__ import annotations

import importlib
import io
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from cryptography.fernet import Fernet

import refinement.app.successfactors_exposure_materializer as exposure
from refinement.tests.test_partitioned_publication import (
    CONTEXT,
    TENANT,
    WORKSPACE,
    FakeCandidateStore,
    FakePublicationStore,
    FakeVerifierClient,
    LocalLakehouse,
    LocalS3Connection,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SQL = (
    REPO_ROOT
    / "cartridges/sap_successfactors/datasets"
    / "sap_successfactors_talent_attrition_exposure.sql"
)


class _Attestations:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def execute(self, *_args):
        return None


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    spe = importlib.import_module("app.staged_publication_engine")
    worker = importlib.import_module("app.publication_verifier_worker")
    scope_class = spe.PublicationIdentity.build.__func__.__globals__["PublicationScope"]
    lake = LocalLakehouse(tmp_path / "lake")
    store = FakePublicationStore()
    store.staged = {}

    def stage_gold(identity, columns, rows):
        frozen = [tuple(row) for row in rows]
        stage = f"stage_{identity.materialization_run_id.hex}"
        store.staged[stage] = frozen
        return stage, len(frozen), frozen

    store.stage_gold = stage_gold

    class Resolver:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def published_snapshot(self, ds, context):
            scope = scope_class(
                context["tenant_id"], context["workspace_id"],
                str(ds.get("name")), str(ds.get("layer") or "silver"),
            )
            head = store.head(scope)
            return SimpleNamespace(head=head) if head else None

    candidates = FakeCandidateStore()
    for name in ("DATABASE_URL", "GOLD_DATABASE_URL", "GOLD_PUBLISHER_DATABASE_URL"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("GOLD_VERIFIER_DATABASE_URL", "postgresql://verifier@unused/gold")
    monkeypatch.setitem(
        spe.DuckDBEngine.__init__.__globals__, "storage_from_env", lambda bucket=None: lake
    )
    monkeypatch.setitem(
        spe.resolve_input_state.__globals__, "PublicationSnapshotResolver", Resolver
    )
    monkeypatch.setitem(
        spe.PublicationInputBindingMixin._latest_materialized_uri.__globals__,
        "PublicationSnapshotResolver",
        Resolver,
    )
    monkeypatch.setattr(worker, "_load_candidate", candidates.rows.__getitem__)
    monkeypatch.setattr(worker, "storage_from_env", lambda: lake)
    monkeypatch.setattr(worker.psycopg2, "connect", lambda *_a, **_k: _Attestations())
    engine = spe.StagedPublicationEngine()
    connection = LocalS3Connection(lake.root)
    engine._conn = lambda: connection

    def no_database():
        raise RuntimeError("no database in unit tests")

    engine._pg_conn = no_database
    engine._publication_store_instance = store
    engine._candidate_store_instance = candidates
    engine._publication_verifier_instance = FakeVerifierClient(worker)
    engine._evidence_store_instance = object.__new__(spe.PublicationEvidenceStore)
    return SimpleNamespace(engine=engine, store=store, lake=lake, scope=scope_class)


def _parquet(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _publish_input(staged, source: str, table: pa.Table) -> None:
    layer, _cartridge, name = source.split("/")
    key = f"{source}/tenant_id={TENANT}/workspace_id={WORKSPACE}/_snapshots/input.parquet"
    put = staged.lake.put_bytes(key, _parquet(table))
    staged.store.heads[staged.scope(TENANT, WORKSPACE, name, layer)] = {
        "materialization_run_id": f"00000000-0000-4000-8000-{len(staged.store.heads):012d}",
        "generation": 1,
        "object_uri": staged.engine._storage_uri(key),
        "object_version": put.version,
        "object_checksum": put.checksum_sha256,
        "row_count": table.num_rows,
        "status": "published",
        "gold_table": None,
    }


def _inputs(fernet: Fernet, salaries: dict[str, str]) -> tuple[pa.Table, pa.Table]:
    today = exposure._utc_today()
    users = list(salaries)
    risk = pa.table(
        {
            "user_id": users,
            "department_name": ["Ventas"] * len(users),
            "risk_band": ["high"] * len(users),
            "invalid_score_input": [False] * len(users),
            "retention_risk_score": [80.0] * len(users),
        }
    )
    recurring = pa.table(
        {
            "user_id": users,
            "pay_component": ["BASE"] * len(users),
            "paycomp_value": [fernet.encrypt(v.encode()).decode() for v in salaries.values()],
            "frequency": ["ANN"] * len(users),
            "currency": ["MXN"] * len(users),
            "start_date": pa.array([today - timedelta(days=30)] * len(users), pa.date32()),
            "end_date": pa.array([None] * len(users), pa.date32()),
        }
    )
    return risk, recurring


def _dataset() -> dict:
    return {
        "name": exposure.EXPOSURE_DATASET,
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": list(exposure.EXPOSURE_SOURCES),
        "sql_def": CONTRACT_SQL.read_text(encoding="utf-8"),
    }


def test_exposure_publishes_through_the_staged_publication_engine(staged, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key)
    salaries = {f"u-{i}": str(40000 + 1111 * i) for i in range(7)}
    risk, recurring = _inputs(Fernet(key.encode()), salaries)
    _publish_input(staged, exposure.RISK_SOURCE, risk)
    _publish_input(staged, exposure.RECURRING_SOURCE, recurring)

    result = staged.engine.materialize(_dataset(), CONTEXT)

    assert result == {
        "name": exposure.EXPOSURE_DATASET,
        "layer": "gold",
        "row_count": 1,
        "status": "published",
        "degraded": False,
    }
    head = staged.store.heads[
        staged.scope(TENANT, WORKSPACE, exposure.EXPOSURE_DATASET, "gold")
    ]
    assert head["row_count"] == 1 and staged.store.published
    (staged_rows,) = staged.store.staged.values()
    (row,) = staged_rows
    assert Decimal("300000.00") in row and Decimal("43000.00") in row
    snapshot = pq.read_table(
        io.BytesIO(staged.lake.get_bytes(staged.engine._s3_object_key(head["object_uri"])))
    ).to_pylist()
    assert [item["headcount"] for item in snapshot] == [7]
    assert "user_id" not in snapshot[0]
    published = json.dumps(
        [{key: value for key, value in item.items() if key != "generated_at"} for item in snapshot],
        default=str,
    )
    for salary in salaries.values():
        assert salary not in published

    replay = staged.engine.materialize(_dataset(), CONTEXT)

    assert staged.engine.consume_publication_replay() is True
    assert replay == {"name": exposure.EXPOSURE_DATASET, "layer": "gold", "row_count": 1}
    assert exposure.with_exposure_status(_dataset(), replay)["status"] == "published"


def test_staged_engine_without_the_key_publishes_a_degraded_empty_head(staged, monkeypatch):
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    risk, recurring = _inputs(Fernet(Fernet.generate_key()), {"u-0": "1000"})
    _publish_input(staged, exposure.RISK_SOURCE, risk)
    _publish_input(staged, exposure.RECURRING_SOURCE, recurring)

    result = staged.engine.materialize(_dataset(), CONTEXT)

    assert result == {
        "name": exposure.EXPOSURE_DATASET,
        "layer": "gold",
        "row_count": 0,
        "status": "missing_key",
        "degraded": True,
    }
    replay = staged.engine.materialize(_dataset(), CONTEXT)
    assert staged.engine.consume_publication_replay() is True
    assert exposure.with_exposure_status(_dataset(), replay)["degraded"] is True


def test_staged_engine_reports_an_unpublished_input_as_a_typed_error(staged, monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    risk, _recurring = _inputs(Fernet(Fernet.generate_key()), {"u-0": "1000"})
    _publish_input(staged, exposure.RISK_SOURCE, risk)

    with pytest.raises(ValueError) as caught:
        staged.engine.materialize(_dataset(), CONTEXT)

    assert type(caught.value).__name__ == "ExposureInputError"
    assert caught.value.dependency == "sap_successfactors_emppaycomprecurring_latest"
    assert not staged.store.published
