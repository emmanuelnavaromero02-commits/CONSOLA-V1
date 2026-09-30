from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# A peer module stubs sys.modules["duckdb"] at collection; these tests need the real engine.
_STUB = sys.modules.get("duckdb")
if isinstance(_STUB, MagicMock):
    sys.modules.pop("duckdb")
else:
    _STUB = None
try:
    duckdb = importlib.import_module("duckdb")
    from refinement.app import main as refinement_main
    from refinement.app.duckdb_engine import DuckDBEngine
    from refinement.app.successfactors_fallbacks import (
        fallback_dataset_for_successfactors,
        readfree_empty_dataset_for_successfactors,
    )
    from refinement.app.successfactors_succession_fallbacks import (
        NOMINATION_SOURCE,
        POSITION_SOURCE,
        SUCCESSION_COVERAGE_DATASET,
        succession_coverage_without_nominations,
        succession_fallback_kind,
    )
    from refinement.app.successfactors_talent_readfree_fallbacks import (
        TALENT_READFREE_EMPTY_SQL,
    )
finally:
    if _STUB is not None:
        sys.modules["duckdb"] = _STUB

_REPO = Path(__file__).resolve().parents[2]
_DATASETS = _REPO / "cartridges/sap_successfactors/datasets"
_GOLD = _DATASETS / f"{SUCCESSION_COVERAGE_DATASET}.sql"
_POSITION_SILVER = _DATASETS / "sap_successfactors_position_latest.sql"
_ODATA_MACROS = _REPO / "refinement/app/sql/sf_odata_datetime.sql"
_TENANT = "11111111-1111-4111-8111-111111111111"
_WORKSPACE = "22222222-2222-4222-8222-222222222222"
_CTX = {"tenant_id": _TENANT, "workspace_id": _WORKSPACE}
_REAL_404 = (
    "HTTP Error: HTTP GET error on '/lakehouse/silver/sap_successfactors/"
    "sap_successfactors_successionnomination_latest/tenant_id%3Dx/data.parquet' (HTTP 404)"
)
_SCOPE = f"tenant_id={_TENANT}/workspace_id={_WORKSPACE}"


def _gold_sql() -> str:
    return _GOLD.read_text(encoding="utf-8")


def _dataset() -> dict:
    sql = _gold_sql()
    sources = json.loads(re.search(r"^-- sources:\s*(\[.*\])\s*$", sql, re.M).group(1))
    return {
        "name": SUCCESSION_COVERAGE_DATASET,
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": sources,
        "sql_def": sql,
        "description": "cobertura",
    }


class _PipelineEngine(DuckDBEngine):
    """Real scope/validate SQL pipeline over local files that mirror lakehouse keys and publication heads."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.heads: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.rows: list[dict] = []

    def _latest_materialized_uri(self, layer, cartridge, name, user_context=None):
        return self.heads.get(f"{layer}/{cartridge}/{name}")

    def _local(self, sql: str) -> str:
        return sql.replace(self._storage_uri(""), str(self.root) + "/")

    def materialize(self, ds: dict, user_context: dict) -> dict:
        sources = list(ds.get("sources") or [])
        self.calls.append(sources)
        sql = ds["sql_def"]
        self._validate_safe_sql(sql)
        scoped = self._scope_storage_sql(self._inject_bucket(sql), sources, user_context)
        self._validate_scoped_storage_sql(scoped, user_context)
        self._validate_effective_sql(
            scoped,
            allow_server_resolved_path_list=True,
            allow_server_resolved_publication_relation=True,
        )
        con = duckdb.connect()
        try:
            cursor = con.execute(self._ensure_scope_columns(con, self._local(scoped), user_context))
            columns = [item[0] for item in cursor.description]
            self.rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            con.close()
        return {"name": ds["name"], "layer": ds.get("layer", "gold"), "row_count": len(self.rows)}

    def publish(self, source: str, relation) -> None:
        key = f"{source}/{_SCOPE}/_snapshots/20260929T000000000000Z-a.parquet"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        relation.write_parquet(str(path))
        self.heads[source] = self._storage_uri(key)

    def point_head_at_missing_object(self, source: str) -> None:
        self.heads[source] = self._storage_uri(f"{source}/{_SCOPE}/_snapshots/20260929T000000000000Z-gone.parquet")


def _table(con, create: str, insert: str, rows: list[tuple]):
    con.execute(create)
    for row in rows:
        con.execute(insert, list(row))
    return con.table("src")


def _publish_legacy_positions(engine: _PipelineEngine, count: int = 3) -> None:
    con = duckdb.connect()
    try:
        engine.publish(
            POSITION_SOURCE,
            _table(
                con,
                "CREATE TABLE src (tenant_id VARCHAR, workspace_id VARCHAR, position_id VARCHAR, "
                "position_name VARCHAR, department VARCHAR, location VARCHAR, cost_center VARCHAR, load_date DATE)",
                "INSERT INTO src VALUES (?, ?, ?, ?, 'Ventas', 'CDMX', 'CC1', DATE '2026-09-28')",
                [(_TENANT, _WORKSPACE, f"P{index}", f"Puesto {index}") for index in range(count)],
            ),
        )
    finally:
        con.close()


def _publish_positions_from_raw(engine: _PipelineEngine, raw: list[tuple]) -> None:
    raw_dir = engine.root / f"raw/sap_successfactors/Position/{_SCOPE}/load_date=2026-09-28/batch_id=b1"
    raw_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(_ODATA_MACROS.read_text(encoding="utf-8"))
        _table(
            con,
            "CREATE TABLE src (code VARCHAR, externalName_defaultValue VARCHAR, department VARCHAR, "
            "location VARCHAR, costCenter VARCHAR, lastModifiedDateTime VARCHAR, positionCriticality VARCHAR, "
            "effectiveStatus VARCHAR, _extracted_at VARCHAR)",
            "INSERT INTO src VALUES (?, ?, 'D1', 'L1', 'C1', '2026-09-28T00:00:00Z', ?, ?, '2026-09-28T01:00:00Z')",
            raw,
        ).write_parquet(str(raw_dir / "part.parquet"))
        silver = _POSITION_SILVER.read_text(encoding="utf-8").replace(
            "'s3://{bucket}/raw/sap_successfactors/Position/**/*.parquet'",
            "'" + str(engine.root / f"raw/sap_successfactors/Position/{_SCOPE}") + "/**/*.parquet'",
        )
        engine.publish(POSITION_SOURCE, con.sql(engine._ensure_scope_columns(con, silver, _CTX)))
    finally:
        con.close()


def _publish_nominations(engine: _PipelineEngine, rows: list[tuple]) -> None:
    con = duckdb.connect()
    try:
        engine.publish(
            NOMINATION_SOURCE,
            _table(
                con,
                "CREATE TABLE src (tenant_id VARCHAR, workspace_id VARCHAR, nomination_id VARCHAR, user_id VARCHAR, "
                "target_position VARCHAR, readiness VARCHAR, nomination_status VARCHAR, load_date DATE)",
                "INSERT INTO src VALUES (?, ?, ?, ?, ?, ?, ?, DATE '2026-09-28')",
                [(_TENANT, _WORKSPACE, *row) for row in rows],
            ),
        )
    finally:
        con.close()


def _refresh_outcome():
    path = _REPO / "airflow/dags/dataset_refresh_outcome.py"
    spec = importlib.util.spec_from_file_location("sf_dataset_refresh_outcome", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_status(result: dict) -> tuple[str, str]:
    outcome = _refresh_outcome()
    classification, _payload = outcome.classify_materialization_payload(
        result, expected_name=SUCCESSION_COVERAGE_DATASET
    )
    run = {
        "status": "completed",
        "materialized": 1,
        "results": [{"name": SUCCESSION_COVERAGE_DATASET, "ok": True, "classification": classification}],
    }
    return classification, outcome.materialization_status(run, task_state="success")


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    pipeline = _PipelineEngine(tmp_path)
    monkeypatch.setattr(refinement_main, "engine", pipeline)
    return pipeline


def test_prod_like_state_publishes_positions_without_failing_or_degrading(engine):
    _publish_legacy_positions(engine)

    result = refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)

    assert engine.calls == [[POSITION_SOURCE, NOMINATION_SOURCE], [POSITION_SOURCE]]
    assert result["row_count"] == 3
    assert result["fallback"] is True
    assert result["degraded"] is False
    assert "no files found" in result["original_error"].lower()
    assert {row["nominations_available"] for row in engine.rows} == {False}
    assert {row["criticality_available"] for row in engine.rows} == {False}
    assert {row["has_active_nominee"] for row in engine.rows} == {None}
    assert {row["critical_without_nominee_total"] for row in engine.rows} == {None}
    assert _run_status(result) == ("ok", "success")


def test_extracted_criticality_and_nominations_publish_a_decided_count(engine):
    _publish_positions_from_raw(
        engine,
        [
            ("P1", "Gerente", "High", "A"),
            ("P2", "Jefe", "Critical", "A"),
            ("P3", "Analista", "Low", "A"),
            ("P4", "Tesorero", "High", "I"),
        ],
    )
    _publish_nominations(engine, [("N1", "h1", "P1", "Ready Now", "Active")])

    result = refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)

    assert engine.calls == [[POSITION_SOURCE, NOMINATION_SOURCE]]
    assert "fallback" not in result
    assert result["row_count"] == 3
    assert {row["critical_without_nominee_total"] for row in engine.rows} == {1}
    assert {row["positions_inactive_count"] for row in engine.rows} == {1}
    assert {row["tenant_id"] for row in engine.rows} == {_TENANT}


def test_unpublished_positions_land_the_read_free_projection(engine):
    result = refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)

    assert engine.calls == [[POSITION_SOURCE, NOMINATION_SOURCE], []]
    assert result["row_count"] == 0
    assert result["degraded"] is True
    assert result["degraded_reason"] == "source_entities_absent_readfree_empty"
    assert _run_status(result) == ("degraded", "partial")


def test_nominations_without_positions_land_the_read_free_projection(engine):
    _publish_nominations(engine, [("N1", "h1", "P1", "Ready Now", "Active")])

    result = refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)

    assert engine.calls == [[POSITION_SOURCE, NOMINATION_SOURCE], []]
    assert result["row_count"] == 0


def test_published_nomination_head_with_a_missing_object_fails(engine):
    _publish_legacy_positions(engine)
    engine.point_head_at_missing_object(NOMINATION_SOURCE)

    with pytest.raises(duckdb.IOException, match="No files found"):
        refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)
    assert engine.calls == [[POSITION_SOURCE, NOMINATION_SOURCE]]


def test_published_position_head_with_a_missing_object_fails(engine):
    engine.point_head_at_missing_object(POSITION_SOURCE)

    with pytest.raises(duckdb.IOException, match="No files found"):
        refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)
    assert engine.calls == [[POSITION_SOURCE, NOMINATION_SOURCE], [POSITION_SOURCE]]


def test_an_unreadable_publication_head_propagates_the_original_error(engine, monkeypatch):
    _publish_legacy_positions(engine)

    def unreadable(sources, user_context=None):
        raise RuntimeError("publication integrity failed: StorageError")

    monkeypatch.setattr(engine, "missing_materialized_dependencies", unreadable)

    with pytest.raises(duckdb.IOException, match="No files found"):
        refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)
    assert len(engine.calls) == 1


def test_storage_failures_are_never_masked(monkeypatch):
    calls: list[str] = []

    class _Broken:
        def materialize(self, ds, user_context):
            calls.append(ds["sql_def"])
            raise RuntimeError("HTTP Error: HTTP GET error on '/lakehouse/x.parquet' (HTTP 503)")

        def missing_materialized_dependencies(self, sources, user_context=None):
            return [NOMINATION_SOURCE]

    monkeypatch.setattr(refinement_main, "engine", _Broken())
    with pytest.raises(RuntimeError, match="HTTP 503"):
        refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)
    assert len(calls) == 1


def test_schema_errors_are_not_treated_as_a_missing_source():
    binder = duckdb.BinderException('Referenced column "position_id" not found in FROM clause!')
    only_nominations = lambda sources: [NOMINATION_SOURCE]  # noqa: E731

    assert fallback_dataset_for_successfactors(_dataset(), binder, missing_sources=only_nominations) is None
    assert readfree_empty_dataset_for_successfactors(_dataset(), binder, missing_sources=only_nominations) is None


@pytest.mark.parametrize(
    ("missing", "kind"),
    [
        ([NOMINATION_SOURCE], "derived"),
        ([POSITION_SOURCE], "readfree"),
        ([POSITION_SOURCE, NOMINATION_SOURCE], "readfree"),
        ([], None),
    ],
)
def test_structural_kind_follows_publication_heads_only(missing, kind):
    assert succession_fallback_kind(lambda sources: [s for s in sources if s in missing]) == kind


def test_a_failing_probe_is_logged_by_class_name_only(caplog):
    def probe(sources):
        raise RuntimeError("publication integrity failed: secret-object-key")

    with caplog.at_level(logging.WARNING):
        assert succession_fallback_kind(probe) is None

    messages = [record.getMessage() for record in caplog.records]
    assert "succession fallback probe failed: RuntimeError" in messages
    assert "secret-object-key" not in caplog.text


def test_no_structural_probe_means_no_succession_fallback():
    error = Exception(_REAL_404)

    assert succession_fallback_kind(None) is None
    assert fallback_dataset_for_successfactors(_dataset(), error) is None
    assert readfree_empty_dataset_for_successfactors(_dataset(), error) is None


def _resolver_modules():
    modules = []
    for name in ("app.publication_snapshot", "refinement.app.publication_snapshot"):
        try:
            modules.append(importlib.import_module(name))
        except ModuleNotFoundError:
            continue
    return modules


_STATS: list[str] = []


def _probe_with_heads(monkeypatch, heads: dict[str, dict]):
    """Structural probe through the real DuckDBEngine and PublicationSnapshotResolver validation."""
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://probe@localhost:1/probe")
    real_engine = DuckDBEngine()
    _STATS.clear()

    class _Storage:
        def uri_for(self, key):
            return real_engine._storage_uri(key)

        def stat(self, key, expected_version=None):
            _STATS.append(key)
            raise FileNotFoundError(key)

    real_engine.storage = _Storage()
    for module in _resolver_modules():

        def read_many(self, scopes, _module=module):
            return {
                scope: (
                    _module.PublicationSnapshot(scope, heads[scope.dataset], dict(heads[scope.dataset]))
                    if scope.dataset in heads
                    else None
                )
                for scope in scopes
            }

        monkeypatch.setattr(module.PublicationSnapshotResolver, "_read_many", read_many)
    return lambda sources: real_engine.missing_materialized_dependencies(sources, _CTX)


def _head(dataset: str, status: str) -> dict:
    uri = DuckDBEngine()._storage_uri(f"silver/sap_successfactors/{dataset}/{_SCOPE}/_snapshots/x.parquet")
    return {
        "materialization_run_id": "run-1",
        "generation": 1,
        "status": status,
        "object_uri": uri,
        "object_version": "v1",
        "object_checksum": "c1",
        "schema_digest": "s1",
        "evidence_digest": "e1",
        "row_count": 1,
        "receipt_id": "r1",
    }


def test_absent_nomination_head_through_the_real_resolver_allows_the_derived_fallback(monkeypatch):
    probe = _probe_with_heads(
        monkeypatch,
        {"sap_successfactors_position_latest": _head("sap_successfactors_position_latest", "legacy_unverified")},
    )

    fallback = fallback_dataset_for_successfactors(_dataset(), Exception(_REAL_404), missing_sources=probe)

    assert fallback is not None
    assert fallback["sources"] == [POSITION_SOURCE]


def test_legacy_unverified_nomination_head_counts_as_published(monkeypatch):
    probe = _probe_with_heads(
        monkeypatch,
        {
            "sap_successfactors_position_latest": _head("sap_successfactors_position_latest", "legacy_unverified"),
            "sap_successfactors_successionnomination_latest": _head(
                "sap_successfactors_successionnomination_latest", "legacy_unverified"
            ),
        },
    )
    error = Exception(_REAL_404)

    assert probe([POSITION_SOURCE, NOMINATION_SOURCE]) == []
    assert fallback_dataset_for_successfactors(_dataset(), error, missing_sources=probe) is None
    assert readfree_empty_dataset_for_successfactors(_dataset(), error, missing_sources=probe) is None


def test_verified_head_with_an_unreadable_object_gets_no_fallback(monkeypatch):
    probe = _probe_with_heads(
        monkeypatch,
        {
            "sap_successfactors_position_latest": _head("sap_successfactors_position_latest", "published"),
        },
    )
    error = Exception(_REAL_404)

    with pytest.raises(RuntimeError, match="publication (integrity failed|head unavailable)"):
        probe([POSITION_SOURCE, NOMINATION_SOURCE])
    assert _STATS and _STATS[0].startswith("silver/sap_successfactors/sap_successfactors_position_latest/")
    assert fallback_dataset_for_successfactors(_dataset(), error, missing_sources=probe) is None
    assert readfree_empty_dataset_for_successfactors(_dataset(), error, missing_sources=probe) is None


def _only_nominations_missing(sources):
    return [source for source in sources if source == NOMINATION_SOURCE]


def test_derived_fallback_drops_only_the_nomination_input():
    fallback = fallback_dataset_for_successfactors(
        _dataset(), Exception(_REAL_404), missing_sources=_only_nominations_missing
    )

    assert fallback is not None
    assert fallback["sources"] == [POSITION_SOURCE]
    assert "sap_successfactors_successionnomination_latest/**" not in fallback["sql_def"]
    assert "sap_successfactors_position_latest/**/*.parquet" in fallback["sql_def"]
    assert "SELECT FALSE AS nominations_available" in fallback["sql_def"]
    assert "SELECT TRUE AS nominations_available" not in fallback["sql_def"]


def test_derived_fallback_tolerates_case_and_spacing():
    sql = (
        _gold_sql()
        .replace("read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_successionnomination_latest",
                 "READ_PARQUET ( 's3://{bucket}/silver/sap_successfactors/sap_successfactors_successionnomination_latest")
        .replace("SELECT TRUE AS nominations_available", "select  true   as nominations_available")
    )

    derived = succession_coverage_without_nominations(sql)

    assert derived is not None
    assert "successionnomination_latest/**" not in derived
    assert "SELECT FALSE AS nominations_available" in derived


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        _GOLD.read_text(encoding="utf-8").replace("SELECT TRUE AS nominations_available", "SELECT 1 AS x"),
        _GOLD.read_text(encoding="utf-8")
        + "\n-- read_parquet('s3://{bucket}/silver/sap_successfactors/"
        "sap_successfactors_successionnomination_latest/**/*.parquet')",
    ],
)
def test_unexpected_gold_shapes_get_no_derived_fallback(sql):
    assert succession_coverage_without_nominations(sql) is None
    ds = {**_dataset(), "sql_def": sql}
    assert fallback_dataset_for_successfactors(
        ds, Exception(_REAL_404), missing_sources=_only_nominations_missing
    ) is None


def test_derived_fallback_passes_the_refinement_sql_policy(monkeypatch):
    engine = DuckDBEngine()
    monkeypatch.setattr(
        engine,
        "_latest_materialized_uri",
        lambda layer, cartridge, name, user_context=None: engine._storage_uri(
            f"{layer}/{cartridge}/{name}/{_SCOPE}/_snapshots/20260929T000000000000Z-a.parquet"
        ),
    )
    fallback = fallback_dataset_for_successfactors(
        _dataset(), Exception(_REAL_404), missing_sources=_only_nominations_missing
    )
    sql = fallback["sql_def"]

    engine._validate_safe_sql(sql)
    scoped = engine._scope_storage_sql(engine._inject_bucket(sql), fallback["sources"], _CTX)
    engine._validate_scoped_storage_sql(scoped, _CTX)
    engine._validate_effective_sql(
        scoped,
        allow_server_resolved_path_list=True,
        allow_server_resolved_publication_relation=True,
    )
    assert "successionnomination_latest/" not in scoped
    assert f"workspace_id={_WORKSPACE}" in scoped


def test_read_free_projection_matches_the_gold_schema(engine):
    _publish_legacy_positions(engine, 1)
    refinement_main._materialize_with_operational_fallback(_dataset(), _CTX)
    con = duckdb.connect()
    try:
        described = con.execute(
            "DESCRIBE " + TALENT_READFREE_EMPTY_SQL[SUCCESSION_COVERAGE_DATASET]
        ).fetchall()
    finally:
        con.close()
    types = {row[0]: row[1] for row in described}

    assert [row[0] for row in described] == list(engine.rows[0])
    assert types["critical_without_nominee_total"] == "BIGINT"
    assert "NULL::BIGINT AS critical_without_nominee_total" in TALENT_READFREE_EMPTY_SQL[SUCCESSION_COVERAGE_DATASET]
