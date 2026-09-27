from __future__ import annotations

import json
import time

import pyarrow.parquet as pq
import pytest

from refinement.app.catalog_copilot import AutonomousCatalogWorker, CatalogCopilotHost
from refinement.app.catalog_copilot_probe import (
    CatalogCopilotProbe,
    ProbeError,
    PublishedRelation,
    quote_identifier,
)
from refinement.app.catalog_overlay import CatalogAnnotations, apply_catalog_annotations
from refinement.tests.catalog_copilot_fakes import (
    DEPARTMENTS_SQL,
    EMPLOYEES_SQL,
    SEC,
    FakeCopilotStore,
    FakeHost,
    LocalEngine,
)


@pytest.fixture()
def local(tmp_path):
    return LocalEngine(tmp_path)


def _relation(local: LocalEngine, name: str, sql: str) -> PublishedRelation:
    local.publish(name, sql)
    probe = CatalogCopilotProbe(local.engine)
    return probe.published_relation({"name": name, "layer": "silver"}, SEC)


def test_quote_identifier_escapes_double_quotes():
    assert quote_identifier('a"b') == '"a""b"'


def test_column_probe_counts_pattern_hits_and_never_returns_values(local):
    relation = _relation(local, "employees", EMPLOYEES_SQL)
    probe = CatalogCopilotProbe(local.engine)
    result = probe.column_probe(
        relation,
        SEC,
        text_cols=["email", "rfc", "status", "department_id"],
        key_cols=["employee_id", "department_id"],
        row_count=10000,
    )
    assert result.sampled_rows == 1000
    assert result.non_null["email"] == 1000
    assert result.pattern_hits["email"] == {"email": 1000}
    assert result.pattern_hits["rfc"]["rfc"] == 1000
    assert "status" not in result.pattern_hits
    assert result.keys_checked is True
    assert result.exact_keys == {"employee_id": True, "department_id": False}
    serialized = json.dumps(
        {
            "non_null": result.non_null,
            "hits": result.pattern_hits,
            "keys": result.exact_keys,
        }
    )
    assert "@empresa.com.mx" not in serialized
    assert "GODE" not in serialized
    assert all(
        isinstance(count, int)
        for hits in result.pattern_hits.values()
        for count in hits.values()
    )


def test_exact_key_check_is_skipped_above_the_row_bound(local):
    relation = _relation(local, "employees", EMPLOYEES_SQL)
    probe = CatalogCopilotProbe(local.engine)
    result = probe.column_probe(
        relation, SEC, text_cols=[], key_cols=["employee_id"], row_count=5_000_001
    )
    assert result.keys_checked is False
    assert result.exact_keys == {}


def test_exact_key_rejects_duplicates_and_nulls(local):
    relation = _relation(
        local,
        "keys",
        "SELECT * FROM (VALUES (1, 'a', 1), (2, 'b', NULL), (3, 'b', 3)) v(unique_id, dup, nullable)",
    )
    result = CatalogCopilotProbe(local.engine).column_probe(
        relation, SEC, text_cols=[], key_cols=["unique_id", "dup", "nullable"], row_count=3
    )
    assert result.exact_keys == {"unique_id": True, "dup": False, "nullable": False}


def test_containment_counts_orphans_between_published_relations(local):
    child = _relation(local, "employees", EMPLOYEES_SQL)
    parent = _relation(local, "departments", DEPARTMENTS_SQL)
    probe = CatalogCopilotProbe(local.engine)
    full = probe.containment(child, "department_id", parent, "department_id", SEC)
    assert (full.child_distinct, full.orphan_values, full.ratio) == (40, 0, 1.0)
    partial_parent = _relation(
        local,
        "few_departments",
        "SELECT 'D' || lpad(CAST(i AS VARCHAR), 3, '0') AS department_id FROM range(30) t(i)",
    )
    partial = probe.containment(child, "department_id", partial_parent, "department_id", SEC)
    assert (partial.child_distinct, partial.orphan_values) == (40, 10)
    assert partial.ratio == 0.75
    capped = probe.containment(
        child, "department_id", parent, "department_id", SEC, child_cap=10
    )
    assert capped.sampled is True


def test_unpublished_relation_is_a_probe_error(local):
    with pytest.raises(ProbeError) as exc:
        CatalogCopilotProbe(local.engine).published_relation(
            {"name": "missing", "layer": "silver"}, SEC
        )
    assert exc.value.code == "relation_unavailable"


def test_probe_timeout_interrupts_long_queries(local):
    huge = PublishedRelation(
        sql="SELECT i AS k FROM range(2000000000) t(i)", head={}, gold=False
    )
    probe = CatalogCopilotProbe(local.engine, timeout_seconds=0.05)
    started = time.perf_counter()
    with pytest.raises(ProbeError) as exc:
        probe.containment(huge, "k", huge, "k", SEC, child_cap=1_000_000)
    assert exc.value.code == "probe_timeout"
    assert time.perf_counter() - started < 5.0


def test_a_timed_out_key_check_degrades_to_unchecked(local):
    huge = PublishedRelation(
        sql="SELECT i AS k FROM range(2000000000) t(i)", head={}, gold=False
    )
    probe = CatalogCopilotProbe(local.engine, timeout_seconds=0.05)
    result = probe.column_probe(
        huge, SEC, text_cols=[], key_cols=["k"], row_count=4_000_000
    )
    assert result.keys_checked is False
    assert result.exact_keys == {}


def _bronze(local: LocalEngine, tmp_path, load_date: str, rows: int, batch: str):
    folder = (
        tmp_path
        / "raw/sap_successfactors/PerPersonal"
        / f"tenant_id={SEC['tenant_id']}"
        / f"workspace_id={SEC['workspace_id']}"
        / f"load_date={load_date}"
        / f"batch_id={batch}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    table = local.engine._con.execute(
        "SELECT 'P' || i AS personIdExternal, "
        "'n' || i || '@x.mx' AS email, "
        "CASE WHEN i % 4 = 0 THEN NULL ELSE DATE '2024-01-01' + CAST(i AS INTEGER) END "
        "AS start_date, "
        "DATE '1980-01-01' + CAST(i AS INTEGER) AS dateOfBirth "
        "FROM range(?) t(i)",
        [rows],
    ).fetch_arrow_table()
    pq.write_table(table, folder / "part.parquet")


def test_bronze_footer_reads_only_the_latest_partition_metadata(local, tmp_path):
    _bronze(local, tmp_path, "2026-09-24", 50, "a")
    _bronze(local, tmp_path, "2026-09-25", 120, "b")
    probe = CatalogCopilotProbe(local.engine)
    footer = probe.bronze_footer(
        "raw/sap_successfactors/PerPersonal",
        SEC,
        allow_range=lambda name, data_type: name == "start_date",
    )
    assert footer.load_date == "2026-09-25"
    assert footer.num_rows == 120
    assert footer.files == 1
    assert footer.truncated is False
    fields = {field["name"]: field for field in footer.fields}
    assert set(fields) == {"personIdExternal", "email", "start_date", "dateOfBirth"}
    assert fields["start_date"]["null_count"] == 30
    assert fields["start_date"]["num_values"] == 120
    assert fields["start_date"]["range"] == ("2024-01-02", "2024-04-29")
    assert "range" not in fields["dateOfBirth"]
    assert "range" not in fields["email"]


def test_bronze_footer_without_partitions_is_a_probe_error(local):
    with pytest.raises(ProbeError) as exc:
        CatalogCopilotProbe(local.engine).bronze_footer("raw/sap_successfactors/Nothing", SEC)
    assert exc.value.code == "bronze_partition_unavailable"


def _worker(local: LocalEngine, host: FakeHost, store: FakeCopilotStore) -> AutonomousCatalogWorker:
    return AutonomousCatalogWorker(
        CatalogCopilotHost(
            list_datasets=host.list_datasets,
            get_dataset=host.get_dataset,
            published_snapshots=host.published_snapshots,
            list_sources=host.list_sources,
            source_allowed=host.source_allowed,
        ),
        store,
        CatalogCopilotProbe(local.engine),
    )


def test_profile_and_link_new_dataset_under_two_seconds(local):
    """Zero clicks: publication enqueues, the catalog shows it in < 2 s."""
    host = FakeHost(local)
    store = FakeCopilotStore()
    departments = host.add("sap_successfactors_departments", DEPARTMENTS_SQL)
    store.seed_from_snapshot("sap_successfactors_departments", departments)
    employees = host.add("sap_successfactors_employees", EMPLOYEES_SQL)
    store.seed_from_snapshot("sap_successfactors_employees", employees)
    assert employees.head["row_count"] == 10000
    assert len(employees.evidence["catalog"]) == 30
    worker = _worker(local, host, store)

    started = time.perf_counter()
    assert worker.enqueue(SEC, {"kind": "dataset", "name": "sap_successfactors_employees"})
    assert worker.wait_idle(timeout=5.0)
    visible = set(host.datasets)
    raw = store.load_annotations(SEC, visible)
    output = apply_catalog_annotations(
        {
            "datasets": {
                "sap_successfactors_employees": {
                    "layer": "silver",
                    "cartridge": "sap_successfactors",
                    "row_count": 10000,
                    "description": "",
                    "columns": [
                        {"name": f["name"], "type": f["type"], "min_value": f.get("min_value"),
                         "max_value": f.get("max_value"), "example_values": []}
                        for f in employees.evidence["catalog"]
                    ],
                }
            },
            "relationships": [],
        },
        CatalogAnnotations(
            columns=raw["columns"],
            relationships=raw["relationships"],
            subjects=raw["subjects"],
        ),
        visible=visible,
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"autocatalog took {elapsed:.3f}s"

    dataset = output["datasets"]["sap_successfactors_employees"]
    assert dataset["description_origin"] == "copilot"
    assert dataset["description"].startswith(
        "Employees de SAP SuccessFactors: 10 000 registros"
    ) or dataset["description"].startswith("Empleados de SAP SuccessFactors: 10 000 registros")
    assert "datos personales identificables" in dataset["description"]
    assert dataset["copilot"]["status"] == "ready"
    columns = {column["name"]: column for column in dataset["columns"]}
    assert columns["email"]["classifications"] == ["pii", "confidential"]
    assert columns["email"]["stats_redacted"] is True
    assert "min_value" not in columns["email"]
    assert columns["rfc"]["classifications"] == ["pii", "confidential"]
    assert columns["salary_amount"]["classifications"] == ["financial", "confidential"]
    assert "min_value" not in columns["salary_amount"]
    assert columns["employee_id"]["is_key"] is True
    assert columns["employee_id"]["semantic_type"] == "identifier"
    assert columns["hire_date"]["description"].startswith("Fecha de contratación.")
    edges = [
        relation
        for relation in output["relationships"]
        if relation["from_dataset"] == "sap_successfactors_employees"
    ]
    assert edges == [
        {
            "from_dataset": "sap_successfactors_employees",
            "from_column": "department_id",
            "to_dataset": "sap_successfactors_departments",
            "to_column": "department_id",
            "join_hint": "LEFT",
            "description": edges[0]["description"],
            "origin": "copilot",
            "status": "active",
            "cardinality": "N:1",
            "confidence": 1.0,
            "basis": ["name:exact", "types:compatible", "containment:1.00", "key:exact"],
        }
    ]
