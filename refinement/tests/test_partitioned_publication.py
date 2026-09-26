from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import io
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omega_lakehouse import ObjectAlreadyExists, ObjectNotFound
from omega_lakehouse.types import ObjectStat, PutResult


BUCKET = "omega-partition-test"
TENANT = "8a0f6f7e-8a57-4c52-9d0e-0d4b8f1d6a11"
WORKSPACE = "1b9e0c1c-5d0e-4b0f-9f4c-7c1d2e3f4a52"
CONTEXT = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
MONTHS = [f"{2024 + (m // 12)}-{(m % 12) + 1:02d}" for m in range(24)]
ROWS_PER_MONTH = 40

UPSTREAM_SQL = """-- {name}  (silver)  cartridge: omega_test
-- sources: ["raw/omega_test/Movements"]
{header}
SELECT movement_id, company, posted_on,
       strftime(posted_on, '%Y-%m') AS period, amount
FROM read_parquet('s3://{{bucket}}/raw/omega_test/Movements/**/*.parquet')
"""

DOWNSTREAM_SQL = """-- {name}  (silver)  cartridge: omega_test
-- sources: ["silver/omega_test/{upstream}"]
SELECT period, company, COUNT(*) AS movements, SUM(amount) AS amount
FROM read_parquet('s3://{{bucket}}/silver/omega_test/{upstream}/**/*.parquet')
GROUP BY period, company
"""


class LocalLakehouse:
    """Versioned object store that mirrors the newest version of each key on disk."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = SimpleNamespace(provider="minio", bucket=BUCKET)
        self.objects: dict[str, list[dict]] = {}
        self.fail_put = None
        self._lock = threading.Lock()

    def uri_for(self, key: str) -> str:
        return f"s3://{BUCKET}/{key}"

    def _client_or_create(self):
        return SimpleNamespace(
            get_bucket_versioning=lambda Bucket: {"Status": "Enabled"}
        )

    def _mirror(self, key: str) -> None:
        path = self.root / key
        versions = self.objects.get(key) or []
        if versions:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(versions[-1]["data"])
        elif path.exists():
            path.unlink()

    def _entry(self, key: str, expected_version: str | None = None) -> dict:
        versions = self.objects.get(key) or []
        for entry in reversed(versions):
            if expected_version is None or entry["version"] == expected_version:
                return entry
        raise ObjectNotFound("missing", key=key)

    def put_bytes(self, key, data, *, overwrite=False, expected_version=None,
                  metadata=None, checksum_sha256=None) -> PutResult:
        if self.fail_put is not None and self.fail_put(key):
            raise RuntimeError("injected storage failure")
        digest = hashlib.sha256(data).hexdigest()
        if checksum_sha256 and checksum_sha256 != digest:
            raise RuntimeError("checksum mismatch")
        with self._lock:
            if self.objects.get(key) and not overwrite:
                raise ObjectAlreadyExists("exists", key=key)
            version = uuid.uuid4().hex
            self.objects.setdefault(key, []).append(
                {
                    "version": version,
                    "data": bytes(data),
                    "checksum": digest,
                    "metadata": dict(metadata or {}),
                }
            )
            self._mirror(key)
        return PutResult(key=key, uri=self.uri_for(key), size=len(data),
                         checksum_sha256=digest, version=version,
                         metadata=dict(metadata or {}))

    def put_file(self, key, path, **kwargs) -> PutResult:
        return self.put_bytes(key, Path(path).read_bytes(), **kwargs)

    def get_bytes(self, key, *, expected_version=None) -> bytes:
        return self._entry(key, expected_version)["data"]

    def iter_chunks(self, key, *, chunk_size=1024 * 1024, expected_version=None):
        yield self.get_bytes(key, expected_version=expected_version)

    def stat(self, key, *, expected_version=None) -> ObjectStat:
        entry = self._entry(key, expected_version)
        return ObjectStat(key=key, uri=self.uri_for(key), size=len(entry["data"]),
                          etag=entry["checksum"][:32], version=entry["version"],
                          checksum_sha256=entry["checksum"],
                          metadata=entry["metadata"])

    def exists(self, key) -> bool:
        return bool(self.objects.get(key))

    def iter_list(self, prefix, *, page_size=1000):
        for key in sorted(self.objects):
            if key.startswith(prefix) and self.objects[key]:
                yield self.stat(key)

    def delete_object(self, key, *, expected_version=None) -> bool:
        with self._lock:
            removed = self.objects.pop(key, None) is not None
            self._mirror(key)
        return removed

    def delete_prefix(self, prefix, **_kwargs) -> int:
        keys = [key for key in list(self.objects) if key.startswith(prefix)]
        for key in keys:
            self.delete_object(key)
        return len(keys)

    def corrupt(self, key: str) -> None:
        entry = self.objects[key][-1]
        entry["data"] = entry["data"][:-9] + b"corrupted"


def _real_duckdb():
    existing = sys.modules.get("duckdb")
    if existing is not None and getattr(existing, "__file__", None) is None:
        sys.modules.pop("duckdb")
        try:
            return importlib.import_module("duckdb")
        finally:
            sys.modules["duckdb"] = existing
    return importlib.import_module("duckdb")


class LocalS3Connection:
    """Real DuckDB connection whose s3:// reads resolve to the lakehouse mirror."""

    def __init__(self, root: Path) -> None:
        self._con = _real_duckdb().connect()
        self._con.execute("SET preserve_insertion_order=false;")
        self._con.execute("SET threads=2;")
        self._root = str(root).rstrip("/") + "/"

    def _local(self, sql: str) -> str:
        return sql.replace(f"s3://{BUCKET}/", self._root)

    def execute(self, sql, params=None):
        if params is None:
            return self._con.execute(self._local(sql))
        return self._con.execute(self._local(sql), params)

    def executemany(self, sql, params):
        return self._con.executemany(self._local(sql), params)

    def interrupt(self):
        self._con.interrupt()

    def close(self):
        self._con.close()


class FakePublicationStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.heads: dict[object, dict] = {}
        self.published: list[str] = []

    @contextmanager
    def run_lock(self, identity):
        try:
            yield
        except Exception:
            run = self.runs.get(str(identity.materialization_run_id)) or {}
            if run.get("status") not in {"prepared", "published"}:
                self.abandon(identity)
            raise

    def reserve(self, identity) -> str:
        run = self.runs.setdefault(
            str(identity.materialization_run_id),
            {"status": "reserved", "expected_head_run_id": identity.expected_head_run_id},
        )
        if run["status"] == "abandoned":
            run["status"] = "reserved"
        return run["status"]

    def run(self, identity) -> dict | None:
        run = self.runs.get(str(identity.materialization_run_id))
        return dict(run) if run else None

    def head(self, scope) -> dict | None:
        head = self.heads.get(scope)
        return dict(head) if head else None

    def mark_prepared(self, identity, **values) -> dict:
        self.runs[str(identity.materialization_run_id)].update(status="prepared", **values)
        return {"schema_digest": "s" * 64, "evidence_digest": "e" * 64}

    def publish(self, identity, expected_head) -> dict:
        run_id = str(identity.materialization_run_id)
        run = self.runs[run_id]
        current = self.heads.get(identity.scope) or {}
        if current.get("materialization_run_id") != expected_head:
            raise RuntimeError("publication head conflict")
        generation = int(current.get("generation") or 0) + 1
        run["status"] = "published"
        self.heads[identity.scope] = {
            "materialization_run_id": run_id,
            "generation": generation,
            "object_uri": run["object_uri"],
            "object_version": run["object_version"],
            "object_checksum": run["object_checksum"],
            "row_count": run["row_count"],
            "status": "published",
            "input_digest": identity.input_digest,
            "contract_digest": identity.contract_digest,
            "gold_table": None,
            "receipt_id": str(uuid.uuid4()),
            "evidence_digest": "e" * 64,
        }
        self.published.append(run_id)
        return {"receipt_id": self.heads[identity.scope]["receipt_id"],
                "generation": generation, "replayed": False}

    def abandon(self, identity) -> None:
        run = self.runs.get(str(identity.materialization_run_id))
        if run is not None:
            run["status"] = "abandoned"


class FakeCandidateStore:
    def __init__(self) -> None:
        self.rows: dict[str, tuple] = {}

    def submit(self, identity, *, object_uri, object_version, object_checksum,
               row_count, lineage, catalog):
        candidate = uuid.uuid4()
        scope = identity.scope
        self.rows[str(candidate)] = (
            str(candidate), str(identity.materialization_run_id), scope.tenant_id,
            scope.workspace_id, scope.dataset, scope.layer, object_uri,
            object_version, object_checksum, row_count, lineage, catalog,
        )
        return candidate


class FakeVerifierClient:
    def __init__(self, worker) -> None:
        self.worker = worker
        self.attested: list[str] = []

    def verify(self, candidate_id) -> None:
        self.worker.verify_candidate(str(candidate_id))
        self.attested.append(str(candidate_id))


@pytest.fixture()
def modules():
    spe = importlib.import_module("app.staged_publication_engine")
    return SimpleNamespace(
        spe=spe,
        engine=sys.modules[spe.DuckDBEngine.__module__],
        inputs=sys.modules[spe.resolve_input_state.__module__],
        binding=sys.modules[spe.PublicationInputBindingMixin.__module__],
        contract=sys.modules[spe.PublicationIdentity.__module__],
        worker=importlib.import_module("app.publication_verifier_worker"),
        partitioned=importlib.import_module("app.partitioned_parquet"),
    )


@pytest.fixture()
def lake(tmp_path):
    return LocalLakehouse(tmp_path / "lake")


@pytest.fixture()
def publication(modules, lake, monkeypatch):
    store = FakePublicationStore()
    candidates = FakeCandidateStore()
    verifier = FakeVerifierClient(modules.worker)

    class Resolver:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def published_snapshot(self, ds, context):
            scope = modules.contract.PublicationScope(
                context["tenant_id"], context["workspace_id"],
                str(ds.get("name")), str(ds.get("layer") or "silver"),
            )
            head = store.head(scope)
            return SimpleNamespace(head=head) if head else None

    class Attestations:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

        def execute(self, *_args):
            return None

    monkeypatch.setattr(modules.inputs, "PublicationSnapshotResolver", Resolver)
    monkeypatch.setattr(modules.binding, "PublicationSnapshotResolver", Resolver)
    monkeypatch.setattr(modules.worker, "_load_candidate", candidates.rows.__getitem__)
    monkeypatch.setattr(modules.worker, "storage_from_env", lambda: lake)
    monkeypatch.setattr(modules.worker.psycopg2, "connect", lambda *_a, **_k: Attestations())
    monkeypatch.setenv("GOLD_VERIFIER_DATABASE_URL", "postgresql://verifier@unused/gold")
    return SimpleNamespace(store=store, candidates=candidates, verifier=verifier)


def _engine(modules, lake, monkeypatch, *, staged: bool, publication=None):
    for name in ("DATABASE_URL", "GOLD_DATABASE_URL", "GOLD_PUBLISHER_DATABASE_URL"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(modules.engine, "storage_from_env", lambda bucket=None: lake)
    engine_class = modules.spe.StagedPublicationEngine if staged else modules.engine.DuckDBEngine
    engine = engine_class()
    connection = LocalS3Connection(lake.root)
    engine._conn = lambda: connection

    def no_database():
        raise RuntimeError("no database in unit tests")

    engine._pg_conn = no_database
    if staged:
        evidence_module = sys.modules[modules.spe.PublicationEvidenceStore.__module__]
        engine._publication_store_instance = publication.store
        engine._candidate_store_instance = publication.candidates
        engine._publication_verifier_instance = publication.verifier
        engine._evidence_store_instance = object.__new__(
            evidence_module.PublicationEvidenceStore
        )
    else:
        engine._write_lineage = lambda **_kwargs: None
        engine._update_catalog = lambda **_kwargs: None
    return engine


def _write_raw(lake: LocalLakehouse, batch: str, months: list[str], rows: int, start: int = 0) -> int:
    ids, companies, dates, amounts = [], [], [], []
    for month_index, month in enumerate(months):
        year, number = (int(part) for part in month.split("-"))
        for row in range(rows):
            ident = start + month_index * rows + row
            ids.append(ident)
            companies.append(("ACME", "GLOBEX", "INITECH")[ident % 3])
            dates.append(dt.date(year, number, 1 + row % 28))
            amounts.append(round(ident * 1.25, 2))
    table = pa.table({"movement_id": pa.array(ids, pa.int64()),
                      "company": companies,
                      "posted_on": pa.array(dates, pa.date32()),
                      "amount": pa.array(amounts, pa.float64())})
    sink = io.BytesIO()
    pq.write_table(table, sink)
    lake.put_bytes(
        f"raw/omega_test/Movements/tenant_id={TENANT}/workspace_id={WORKSPACE}/"
        f"load_date=2026-09-0{batch}/batch_id=b{batch}/movements.parquet",
        sink.getvalue(),
    )
    return len(ids)


def _upstream(name: str, partitioned: bool) -> dict:
    header = "-- partition_by: period" if partitioned else "-- description: flat twin"
    return {"name": name, "layer": "silver", "cartridge": "omega_test",
            "sources": ["raw/omega_test/Movements"],
            "sql_def": UPSTREAM_SQL.format(name=name, header=header)}


def _downstream(name: str, upstream: str) -> dict:
    return {"name": name, "layer": "silver", "cartridge": "omega_test",
            "sources": [f"silver/omega_test/{upstream}"],
            "sql_def": DOWNSTREAM_SQL.format(name=name, upstream=upstream)}


def _rows(engine, dataset: dict) -> list[tuple]:
    result = engine.query_dataset(dataset, {}, 10_000, CONTEXT)
    assert "error" not in result, result
    return sorted(tuple(sorted(row.items())) for row in result["data"])


def _scope(modules, name: str):
    return modules.contract.PublicationScope(TENANT, WORKSPACE, name, "silver")


def test_partition_header_is_parsed_from_the_first_twenty_lines(modules):
    parse = modules.partitioned.partition_column_from_sql

    assert parse(UPSTREAM_SQL.format(name="x", header="-- partition_by: period")) == "period"
    assert parse(UPSTREAM_SQL.format(name="x", header="")) is None
    assert parse("\n" * 20 + "-- partition_by: period\nSELECT 1") is None
    assert parse("-- partition_by: period\n-- partition_by: period\nSELECT 1") == "period"
    for bad in ("period, company", "period;", "1period", ""):
        with pytest.raises(ValueError, match="partition_by"):
            parse(f"-- partition_by: {bad}\nSELECT 1")
    with pytest.raises(ValueError, match="more than once"):
        parse("-- partition_by: period\n-- partition_by: company\nSELECT 1")


def test_partitioned_silver_publishes_hive_partitions_under_one_manifest(
    modules, lake, publication, monkeypatch
):
    total = _write_raw(lake, "1", MONTHS, ROWS_PER_MONTH)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    dataset = _upstream("omega_movements", partitioned=True)

    result = engine.materialize(dataset, CONTEXT)

    head = publication.store.head(_scope(modules, "omega_movements"))
    key = engine._s3_object_key(head["object_uri"])
    run = uuid.UUID(head["materialization_run_id"]).hex
    set_key = (
        f"silver/omega_test/omega_movements/tenant_id={TENANT}/workspace_id={WORKSPACE}"
        f"/_snapshots/_pending/{run}/_partitioned"
    )
    manifest_raw = lake.get_bytes(key, expected_version=head["object_version"])
    manifest = modules.partitioned.read_manifest(manifest_raw, key)
    assert result["row_count"] == head["row_count"] == manifest["row_count"] == total
    assert key == f"{set_key}/{head['object_checksum']}.parquet"
    assert hashlib.sha256(manifest_raw).hexdigest() == head["object_checksum"]
    assert [part["value"] for part in manifest["parts"]] == MONTHS
    stored = sorted(k for k in lake.objects if k.startswith(f"{set_key}/period="))
    assert stored == sorted(part["key"] for part in manifest["parts"])
    for part in manifest["parts"]:
        assert part["key"].startswith(f"{set_key}/period={part['value']}/")
        raw = lake.get_bytes(part["key"], expected_version=part["version"])
        assert hashlib.sha256(raw).hexdigest() == part["checksum"]
        assert pq.ParquetFile(io.BytesIO(raw)).metadata.num_rows == ROWS_PER_MONTH
    assert pq.ParquetFile(io.BytesIO(manifest_raw)).metadata.num_rows == 0
    assert publication.verifier.attested and publication.store.published

    rows = _rows(engine, dataset)
    assert len(rows) == total
    assert {dict(row)["period"] for row in rows} == set(MONTHS)


def test_downstream_of_a_partitioned_dataset_matches_the_flat_twin(
    modules, lake, publication, monkeypatch
):
    _write_raw(lake, "1", MONTHS, ROWS_PER_MONTH)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    partitioned = _upstream("omega_movements", partitioned=True)
    flat = _upstream("omega_movements_flat", partitioned=False)
    engine.materialize(partitioned, CONTEXT)
    engine.materialize(flat, CONTEXT)
    flat_head = publication.store.head(_scope(modules, "omega_movements_flat"))
    flat_key = engine._s3_object_key(flat_head["object_uri"])
    assert "/_partitioned/" not in flat_key
    assert flat_key.endswith(f"/_pending/{uuid.UUID(flat_head['materialization_run_id']).hex}/"
                             f"{flat_head['object_checksum']}.parquet")

    monthly = _downstream("omega_monthly", "omega_movements")
    monthly_flat = _downstream("omega_monthly_flat", "omega_movements_flat")
    engine.materialize(monthly, CONTEXT)
    engine.materialize(monthly_flat, CONTEXT)

    assert _rows(engine, partitioned) == _rows(engine, flat)
    downstream = _rows(engine, monthly)
    assert downstream == _rows(engine, monthly_flat)
    assert len(downstream) == len(MONTHS) * 3
    assert sum(dict(row)["movements"] for row in downstream) == len(MONTHS) * ROWS_PER_MONTH


def test_a_failure_midway_keeps_the_previous_partition_set_published(
    modules, lake, publication, monkeypatch
):
    first = _write_raw(lake, "1", MONTHS[:12], ROWS_PER_MONTH)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    dataset = _upstream("omega_movements", partitioned=True)
    engine.materialize(dataset, CONTEXT)
    scope = _scope(modules, "omega_movements")
    previous = publication.store.head(scope)

    second = _write_raw(lake, "2", MONTHS[12:], ROWS_PER_MONTH, start=first)
    uploads: list[str] = []

    def fail_third_partition(key: str) -> bool:
        if "/_partitioned/period=" in key:
            uploads.append(key)
        return len(uploads) >= 3

    lake.fail_put = fail_third_partition
    with pytest.raises(RuntimeError, match="injected storage failure"):
        engine.materialize(dataset, CONTEXT)

    assert publication.store.head(scope) == previous
    assert publication.store.published == [previous["materialization_run_id"]]
    assert len(_rows(engine, dataset)) == first
    failed_runs = [
        run_id for run_id, run in publication.store.runs.items() if run["status"] == "abandoned"
    ]
    assert len(failed_runs) == 1
    failed_set = f"_pending/{uuid.UUID(failed_runs[0]).hex}/_partitioned/"
    assert not [
        key for key in lake.objects
        if failed_set in key and modules.partitioned.is_manifest_key(key)
    ]

    lake.fail_put = None
    engine.materialize(dataset, CONTEXT)
    current = publication.store.head(scope)
    assert current["generation"] == previous["generation"] + 1
    assert len(_rows(engine, dataset)) == first + second


def test_verifier_rejects_a_partition_set_with_a_corrupted_part(
    modules, lake, publication, monkeypatch
):
    _write_raw(lake, "1", MONTHS[:3], ROWS_PER_MONTH)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    engine.materialize(_upstream("omega_movements", partitioned=True), CONTEXT)
    head = publication.store.head(_scope(modules, "omega_movements"))
    candidate = next(iter(publication.candidates.rows))
    key = engine._s3_object_key(head["object_uri"])
    manifest = modules.partitioned.read_manifest(lake.get_bytes(key), key)

    lake.corrupt(manifest["parts"][1]["key"])

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        modules.worker.verify_candidate(candidate)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        engine._verify_parquet_evidence(
            object_uri=head["object_uri"],
            object_checksum=head["object_checksum"],
            object_version=head["object_version"],
            row_count=head["row_count"],
            expected_columns=[field.name for field in manifest["schema"]],
        )


def test_manifest_refuses_parts_outside_its_own_partition_set(modules, tmp_path):
    partitioned = modules.partitioned
    set_key = f"silver/c/d/tenant_id={TENANT}/workspace_id={WORKSPACE}/_snapshots/x/_partitioned"
    schema = pa.schema([("period", pa.string())])
    digest = "a" * 64
    for key in (
        f"silver/c/other/tenant_id={TENANT}/workspace_id={WORKSPACE}/_partitioned/period=2024-01/00000-{digest}.parquet",
        f"{set_key}/period=2024-01/../00000-{digest}.parquet",
        f"{set_key}/period=2024-02/00000-{digest}.parquet",
    ):
        path = tmp_path / "manifest.parquet"
        partitioned.write_manifest(
            path, schema, "period",
            [{"key": key, "value": "2024-01", "checksum": digest, "rows": 1, "version": "v"}],
        )
        raw = path.read_bytes()
        manifest_key = f"{set_key}/{hashlib.sha256(raw).hexdigest()}.parquet"
        with pytest.raises(ValueError, match="part is invalid"):
            partitioned.read_manifest(raw, manifest_key)
    with pytest.raises(ValueError, match="checksum mismatch"):
        partitioned.read_manifest(raw, f"{set_key}/{'b' * 64}.parquet")


def test_legacy_pruning_removes_whole_partition_sets(
    modules, lake, monkeypatch
):
    _write_raw(lake, "1", MONTHS[:4], 5)
    engine = _engine(modules, lake, monkeypatch, staged=False)
    dataset = _upstream("omega_movements", partitioned=True)
    uris = [engine.materialize(dataset, CONTEXT)["storage_uri"] for _ in range(7)]

    prefix = engine._snapshot_prefix("silver", "omega_test", "omega_movements", CONTEXT)
    remaining: dict[str, list[str]] = {}
    for key in lake.objects:
        if key.startswith(prefix):
            remaining.setdefault(key[len(prefix):].split("/", 1)[0], []).append(key)
    kept = sorted(uri.split("/_snapshots/", 1)[1].split("/", 1)[0] for uri in uris)[-5:]
    assert sorted(remaining) == kept
    for entry, keys in remaining.items():
        manifests = [key for key in keys if modules.partitioned.is_manifest_key(key)]
        assert len(manifests) == 1 and len(keys) == 1 + 4
        manifest = modules.partitioned.read_manifest(lake.get_bytes(manifests[0]), manifests[0])
        assert sorted(part["key"] for part in manifest["parts"]) == sorted(set(keys) - set(manifests))


def test_empty_partitioned_dataset_publishes_a_schema_only_manifest(
    modules, lake, publication, monkeypatch
):
    _write_raw(lake, "1", MONTHS[:2], 3)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    dataset = _upstream("omega_movements", partitioned=True)
    dataset["sql_def"] = dataset["sql_def"].rstrip() + "\nWHERE amount < 0\n"

    assert engine.materialize(dataset, CONTEXT)["row_count"] == 0
    head = publication.store.head(_scope(modules, "omega_movements"))
    key = engine._s3_object_key(head["object_uri"])
    manifest = modules.partitioned.read_manifest(lake.get_bytes(key), key)
    assert manifest["parts"] == [] and head["row_count"] == 0
    result = engine.query_dataset(dataset, {}, 10, CONTEXT)
    assert result["data"] == []
    assert "period" in [column["name"] for column in result["schema"]]
    engine.materialize(_downstream("omega_monthly", "omega_movements"), CONTEXT)
    assert _rows(engine, _downstream("omega_monthly", "omega_movements")) == []


@pytest.mark.parametrize(
    ("period_expression", "message"),
    [
        ("strftime(posted_on, '%Y') AS period", "changes type"),
        ("company || ' ' || strftime(posted_on, '%Y-%m') AS period", "storage-safe"),
        ("strftime(posted_on, '%Y-%m') AS month_label", "not produced"),
    ],
)
def test_unsafe_partition_columns_fail_before_anything_is_published(
    modules, lake, publication, monkeypatch, period_expression, message
):
    _write_raw(lake, "1", MONTHS[:2], 3)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    dataset = _upstream("omega_movements", partitioned=True)
    dataset["sql_def"] = dataset["sql_def"].replace(
        "strftime(posted_on, '%Y-%m') AS period", period_expression
    )

    with pytest.raises(ValueError, match=message):
        engine.materialize(dataset, CONTEXT)
    assert publication.store.heads == {}
    assert not [key for key in lake.objects if "/_partitioned/" in key]


def test_gold_parquet_snapshot_is_partitioned_and_rebuildable(
    modules, lake, publication, monkeypatch
):
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    identity = modules.contract.PublicationIdentity.build(
        {"name": "omega_gold", "layer": "gold", "cartridge": "omega_test", "sql_def": "SELECT 1"},
        CONTEXT,
    )
    rows = [(TENANT, WORKSPACE, month, index * 10.5)
            for index, month in enumerate(MONTHS[:6]) for _ in range(3)]
    engine._publication_local.state = {
        "identity": identity,
        "gold_columns": [{"name": name, "type": "TEXT"} for name in
                         ("tenant_id", "workspace_id", "period", "amount")],
        "gold_snapshot_schema": {"tenant_id": "VARCHAR", "workspace_id": "VARCHAR",
                                 "period": "VARCHAR", "amount": "DOUBLE"},
        "gold_rows": rows,
    }
    try:
        path = engine._snapshot_path("gold", "omega_test", "omega_gold", CONTEXT)
        uri = engine._copy_scoped_gold_table_snapshot(
            engine._conn(), "gold_omega_gold", path, TENANT, WORKSPACE, CONTEXT,
            partition_by="period",
        )
        state = engine._state()
        row_count, catalog = engine._verify_parquet_evidence(
            object_uri=state["object_uri"], object_checksum=state["object_checksum"],
            object_version=state["object_version"], row_count=len(rows),
            expected_columns=["tenant_id", "workspace_id", "period", "amount"],
        )
    finally:
        engine._publication_local.state = None

    key = engine._s3_object_key(uri)
    assert modules.partitioned.is_manifest_key(key)
    assert key.startswith(f"gold/omega_test/omega_gold/tenant_id={TENANT}/")
    assert row_count == len(rows)
    assert [field["name"] for field in catalog] == ["tenant_id", "workspace_id", "period", "amount"]
    table = engine._read_published_table(uri, state["object_version"])
    assert sorted(table.to_pylist(), key=lambda row: (row["period"], row["amount"])) == [
        dict(zip(("tenant_id", "workspace_id", "period", "amount"), row)) for row in rows
    ]
    assert engine._read_published_table(uri, state["object_version"], max_rows=1).num_rows == 3


def test_non_partitioned_copy_keeps_the_single_file_statement(modules, monkeypatch):
    engine = object.__new__(modules.engine.DuckDBEngine)
    engine.minio_bucket = BUCKET
    engine.storage = SimpleNamespace(config=SimpleNamespace(provider="minio"))
    executed: list[str] = []
    engine._upload_local_parquet = lambda local, target: target
    connection = SimpleNamespace(execute=lambda sql: executed.append(sql))

    engine._copy_to_parquet(connection, "SELECT 1 AS ok", f"s3://{BUCKET}/silver/x/data.parquet")

    assert len(executed) == 1
    assert executed[0].startswith("COPY (SELECT 1 AS ok) TO '")
    assert executed[0].endswith("(FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
    assert "PARTITION_BY" not in executed[0]


def test_request_validation_sees_one_manifest_and_execution_sees_every_partition(
    modules, lake, publication, monkeypatch
):
    _write_raw(lake, "1", MONTHS, 2)
    engine = _engine(modules, lake, monkeypatch, staged=True, publication=publication)
    engine.materialize(_upstream("omega_movements", partitioned=True), CONTEXT)
    policy = sys.modules[modules.engine.validate_table_function_query.__module__]
    downstream = _downstream("omega_monthly", "omega_movements")

    scoped = engine._scope_storage_sql(
        engine._inject_bucket(downstream["sql_def"]), downstream["sources"], CONTEXT
    )
    reads = policy.validate_table_function_query(
        scoped, expected_bucket=BUCKET, allow_bucket_placeholder=False
    )
    head = publication.store.head(_scope(modules, "omega_movements"))
    assert [read.path for read in reads] == [head["object_uri"]]

    executed = engine._expand_partition_manifests(scoped, CONTEXT)
    reads = policy.validate_table_function_query(
        executed, expected_bucket=BUCKET, allow_bucket_placeholder=False,
        allow_server_resolved_path_list=True,
    )
    assert len(reads) == len(MONTHS)
    engine._validate_scoped_storage_sql(executed, CONTEXT)
    foreign = {"tenant_id": str(uuid.uuid4()), "workspace_id": str(uuid.uuid4())}
    assert engine._expand_partition_manifests(scoped, foreign) == scoped
