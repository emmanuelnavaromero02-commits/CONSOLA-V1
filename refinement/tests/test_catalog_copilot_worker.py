from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from refinement.app import catalog_copilot
from refinement.app.catalog_copilot import (
    AutonomousCatalogWorker,
    CatalogCopilotHost,
    subject_fingerprint,
)
from refinement.app.catalog_copilot_probe import CatalogCopilotProbe
from refinement.tests.catalog_copilot_fakes import (
    DEPARTMENTS_SQL,
    EMPLOYEES_SQL,
    SEC,
    FakeCopilotStore,
    FakeHost,
    LocalEngine,
)

EMPLOYEES = "sap_successfactors_employees"
DEPARTMENTS = "sap_successfactors_departments"
EDGE = {
    "from_dataset": EMPLOYEES,
    "from_column": "department_id",
    "to_dataset": DEPARTMENTS,
    "to_column": "department_id",
}


@pytest.fixture()
def env(tmp_path):
    local = LocalEngine(tmp_path)
    host = FakeHost(local)
    store = FakeCopilotStore()
    store.seed_from_snapshot(DEPARTMENTS, host.add(DEPARTMENTS, DEPARTMENTS_SQL))
    store.seed_from_snapshot(EMPLOYEES, host.add(EMPLOYEES, EMPLOYEES_SQL))
    return local, host, store


def _worker(local, host, store, **kwargs) -> AutonomousCatalogWorker:
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
        **kwargs,
    )


def _profile(worker, name=EMPLOYEES):
    return worker.profile_and_link(SEC, {"kind": "dataset", "name": name})


def test_fingerprint_makes_profiling_idempotent(env):
    local, host, store = env
    worker = _worker(local, host, store)
    assert _profile(worker)["processed"] is True
    assert _profile(worker) == {"processed": False, "reason": "fresh"}
    assert store.saved_states == 1
    state = store.states[("dataset", EMPLOYEES)]
    assert state["fingerprint"].endswith("|catalog-copilot/1")
    host.add(EMPLOYEES, EMPLOYEES_SQL, run_id="run-2")
    assert _profile(worker)["processed"] is True
    assert store.saved_states == 2


def test_fingerprint_changes_with_run_generation_schema_and_rules():
    base = {
        "materialization_run_id": "r1",
        "generation": 1,
        "schema_digest": "s1",
    }
    first = subject_fingerprint("dataset", base)
    assert subject_fingerprint("dataset", {**base, "generation": 2}) != first
    assert subject_fingerprint("dataset", {**base, "schema_digest": "s2"}) != first
    assert subject_fingerprint("dataset", {**base, "materialization_run_id": "r2"}) != first
    bronze = subject_fingerprint(
        "bronze_source",
        {"load_date": "2026-09-25", "num_rows": 3, "fields": [{"name": "a", "type": "INT"}]},
    )
    assert bronze.startswith("2026-09-25|3|") and bronze.endswith("|catalog-copilot/1")


def test_never_overwrites_a_manual_description_or_classification(env):
    local, host, store = env
    store.columns[(EMPLOYEES, "email")].update(
        description="Correo corporativo validado por RH",
        description_origin="manual",
        classifications=["confidential"],
        classification_origin="manual",
    )
    _profile(_worker(local, host, store))
    email = store.columns[(EMPLOYEES, "email")]
    assert email["description"] == "Correo corporativo validado por RH"
    assert email["description_origin"] == "manual"
    assert email["classifications"] == ["confidential"]
    assert email["classification_origin"] == "manual"
    assert email["copilot_evidence"]["basis"][0] == "name:email"
    rfc = store.columns[(EMPLOYEES, "rfc")]
    assert rfc["description_origin"] == "copilot"
    assert rfc["classifications"] == ["pii", "confidential"]


def test_rejected_relationship_never_comes_back(env):
    local, host, store = env
    store.add_edge(**EDGE, origin="manual", status="rejected")
    _profile(_worker(local, host, store))
    row = store.edges[tuple(EDGE.values())]
    assert (row["origin"], row["status"]) == ("manual", "rejected")
    assert not [
        edge for edge in store.edges.values() if edge["origin"] == "copilot"
    ]


def test_manual_edge_in_either_direction_blocks_the_copilot(env):
    local, host, store = env
    store.add_edge(
        from_dataset=DEPARTMENTS,
        from_column="department_id",
        to_dataset=EMPLOYEES,
        to_column="department_id",
        origin="manual",
        status="active",
    )
    _profile(_worker(local, host, store))
    assert tuple(EDGE.values()) not in store.edges


def test_stale_copilot_edges_are_retired_but_invisible_ends_are_left_alone(env):
    local, host, store = env
    store.add_edge(
        from_dataset=EMPLOYEES,
        from_column="status",
        to_dataset=DEPARTMENTS,
        to_column="department_name",
        origin="copilot",
        status="active",
    )
    store.add_edge(
        from_dataset=EMPLOYEES,
        from_column="team",
        to_dataset="not_visible_dataset",
        to_column="team",
        origin="copilot",
        status="active",
    )
    _profile(_worker(local, host, store))
    assert store.edges[(EMPLOYEES, "status", DEPARTMENTS, "department_name")]["status"] == "retired"
    assert store.edges[(EMPLOYEES, "team", "not_visible_dataset", "team")]["status"] == "active"
    assert store.edges[tuple(EDGE.values())]["status"] == "active"


def test_retired_copilot_edge_is_reactivated_when_detected_again(env):
    local, host, store = env
    store.add_edge(**EDGE, origin="copilot", status="retired")
    _profile(_worker(local, host, store))
    assert store.edges[tuple(EDGE.values())]["status"] == "active"


def test_annotations_and_state_hold_counts_never_values(env):
    local, host, store = env
    _profile(_worker(local, host, store))
    dumped = json.dumps(
        {
            "columns": [
                row["copilot_evidence"]
                for (dataset, _c), row in store.columns.items()
                if dataset == EMPLOYEES
            ],
            "state": store.states[("dataset", EMPLOYEES)]["summary"],
            "edges": [edge["basis"] for edge in store.edges.values()],
        },
        default=str,
    )
    for value in ("@empresa.com.mx", "GODE", "Nombre1", "EMP000", "D001"):
        assert value not in dumped


def test_probe_failure_yields_partial_state_with_name_based_annotations(env):
    local, host, store = env
    del local.heads[EMPLOYEES]
    outcome = _profile(_worker(local, host, store))
    assert outcome["status"] == "partial"
    state = store.states[("dataset", EMPLOYEES)]
    assert (state["status"], state["error_code"]) == ("partial", "relation_unavailable")
    email = store.columns[(EMPLOYEES, "email")]
    assert email["classifications"] == ["pii", "confidential"]
    assert email["copilot_confidence"] == 0.7


def test_failed_or_partial_state_is_retried_after_the_window(env):
    local, host, store = env
    del local.heads[EMPLOYEES]
    worker = _worker(local, host, store)
    _profile(worker)
    local.publish(EMPLOYEES, EMPLOYEES_SQL)
    assert _profile(worker) == {"processed": False, "reason": "fresh"}
    store.states[("dataset", EMPLOYEES)]["profiled_at"] = datetime.now(
        timezone.utc
    ) - timedelta(seconds=catalog_copilot.RETRY_AFTER_SECONDS + 1)
    assert _profile(worker)["status"] == "ready"


def test_queue_deduplicates_and_refuses_unscoped_contexts(env, monkeypatch):
    local, host, store = env
    worker = _worker(local, host, store)
    monkeypatch.setattr(worker, "_ensure_thread", lambda: None)
    subject = {"kind": "dataset", "name": EMPLOYEES}
    assert worker.enqueue(SEC, subject) is True
    assert worker.enqueue(SEC, subject) is False
    assert worker.enqueue({**SEC, "trusted": False}, {"kind": "dataset", "name": "x"}) is False
    assert worker.enqueue({**SEC, "workspace_id": ""}, {"kind": "dataset", "name": "x"}) is False
    worker._queue = queue.Queue(maxsize=1)
    worker._queue.put_nowait(("full", "full"))
    assert worker.enqueue(SEC, {"kind": "dataset", "name": "other"}) is False


def test_disabled_copilot_does_nothing(env, monkeypatch):
    local, host, store = env
    monkeypatch.setenv("CATALOG_COPILOT_ENABLED", "false")
    worker = _worker(local, host, store)
    assert worker.enqueue(SEC, {"kind": "dataset", "name": EMPLOYEES}) is False
    assert worker.catch_up(SEC).to_dict() == {
        "status": "idle",
        "processed": 0,
        "pending": 0,
        "stale": 0,
        "annotation_epoch": None,
    }
    assert store.saved_states == 0


def test_catch_up_never_profiles_in_the_request_and_reports_pending(env, monkeypatch):
    local, host, store = env
    host.add("sap_successfactors_locations", "SELECT 'L' || i AS location_id FROM range(5) t(i)")
    worker = _worker(local, host, store)
    profiled_in_request = []
    original = worker.profile_and_link

    def spy(sec, subject, **kwargs):
        profiled_in_request.append(threading.current_thread().name)
        return original(sec, subject, **kwargs)

    monkeypatch.setattr(worker, "profile_and_link", spy)
    request_thread = threading.current_thread().name
    status = worker.catch_up(SEC)
    assert (status.processed, status.stale) == (0, 3)
    assert status.status == "working" and status.pending == 3
    assert worker.wait_idle(timeout=15)
    assert len(store.states) == 3
    assert request_thread not in profiled_in_request
    again = worker.catch_up(SEC)
    assert again.status == "idle"
    assert (again.processed, again.pending, again.stale) == (0, 0, 0)
    assert again.annotation_epoch is not None


def test_catch_up_returns_fast_even_when_profiles_are_slow(env, monkeypatch):
    local, host, store = env
    worker = _worker(local, host, store)
    release = threading.Event()
    original = worker._profile_dataset

    def slow(sec, name, deadline):
        release.wait(timeout=5)
        return original(sec, name, deadline)

    monkeypatch.setattr(worker, "_profile_dataset", slow)
    started = time.perf_counter()
    status = worker.catch_up(SEC)
    assert time.perf_counter() - started < 1.0
    assert status.status == "working"
    polled = worker.catch_up(SEC)
    assert polled.pending == 2, "queued and in-flight subjects count as pending"
    release.set()
    assert worker.wait_idle(timeout=15)


def test_catch_up_filters_by_cartridge(env):
    local, host, store = env
    host.add("hubspot_deals", "SELECT 'x' AS deal_id", cartridge="hubspot")
    worker = _worker(local, host, store)
    status = worker.catch_up(SEC, cartridge="hubspot")
    assert status.stale == 1
    assert worker.wait_idle(timeout=15)
    assert ("dataset", "hubspot_deals") in store.states
    assert ("dataset", EMPLOYEES) not in store.states


def test_every_query_of_a_profile_is_clamped_to_its_budget(env):
    local, host, store = env
    worker = _worker(local, host, store)
    outcome = worker.profile_and_link(
        SEC, {"kind": "dataset", "name": EMPLOYEES}, deadline=time.monotonic() - 1
    )
    assert outcome["status"] == "partial"
    state = store.states[("dataset", EMPLOYEES)]
    assert state["error_code"] == "probe_budget_exhausted"


def test_bronze_source_profile_uses_footers_and_declared_protection(env, tmp_path):
    local, host, store = env
    source = "raw/sap_successfactors/PerEmail"
    folder = (
        tmp_path
        / source
        / f"tenant_id={SEC['tenant_id']}"
        / f"workspace_id={SEC['workspace_id']}"
        / "load_date=2026-09-25"
        / "batch_id=1"
    )
    folder.mkdir(parents=True)
    table = local.engine._con.execute(
        "SELECT 'P' || i AS personIdExternal, 'x' || i || '@a.mx' AS emailAddress, "
        "DATE '2026-01-01' + CAST(i AS INTEGER) AS lastModified FROM range(30) t(i)"
    ).fetch_arrow_table()
    pq.write_table(table, folder / "a.parquet")
    host.sources.append(source)
    store.protection = {"emailaddress": "masked"}
    worker = _worker(local, host, store)
    outcome = worker.profile_and_link(SEC, {"kind": "bronze_source", "name": source})
    assert outcome["processed"] is True
    state = store.states[("bronze_source", source)]
    assert state["display_name"] == "PerEmail" or state["display_name"]
    summary = state["summary"]
    assert summary["rows"] == 30
    columns = {column["name"]: column for column in summary["columns"]}
    assert columns["emailAddress"]["classifications"] == ["pii", "confidential"]
    assert columns["emailAddress"]["classification_origin"] == "packaged"
    assert columns["emailAddress"]["confidence"] == 1.0
    assert "@a.mx" not in json.dumps(summary)
    assert "description" not in columns["lastModified"], "rendered at read time"
    assert worker.profile_and_link(SEC, {"kind": "bronze_source", "name": source}) == {
        "processed": False,
        "reason": "fresh",
    }


def test_bronze_source_outside_scope_is_refused(env):
    local, host, store = env
    worker = _worker(local, host, store)
    assert worker.profile_and_link(
        SEC, {"kind": "bronze_source", "name": "raw/other/Secret"}
    ) == {"processed": False, "reason": "not_visible"}


def test_invisible_dataset_is_never_profiled(env):
    local, host, store = env
    worker = _worker(local, host, store)
    assert _profile(worker, "someone_elses_dataset") == {
        "processed": False,
        "reason": "not_visible",
    }
    assert worker.profile_and_link({**SEC, "trusted": False}, {"kind": "dataset", "name": EMPLOYEES}) == {
        "processed": False,
        "reason": "scope",
    }


def test_the_background_queue_keeps_going_when_one_subject_breaks(env, monkeypatch):
    local, host, store = env
    worker = _worker(local, host, store)
    original = worker._profile_dataset

    def flaky(sec, name, deadline):
        if name == EMPLOYEES:
            raise RuntimeError("storage hiccup")
        return original(sec, name, deadline)

    monkeypatch.setattr(worker, "_profile_dataset", flaky)
    worker.catch_up(SEC)
    assert worker.wait_idle(timeout=15)
    assert ("dataset", DEPARTMENTS) in store.states
    assert ("dataset", EMPLOYEES) not in store.states


def test_reprofiling_the_parent_keeps_valid_child_edges(env):
    """Reviewer reproduction: profiling the dimension must not retire FK edges."""
    local, host, store = env
    worker = _worker(local, host, store)
    _profile(worker, DEPARTMENTS)
    _profile(worker, EMPLOYEES)
    assert store.edges[tuple(EDGE.values())]["status"] == "active"
    host.add(DEPARTMENTS, DEPARTMENTS_SQL, run_id="run-2")
    store.seed_from_snapshot(DEPARTMENTS, host.snapshots[DEPARTMENTS])
    assert _profile(worker, DEPARTMENTS)["processed"] is True
    assert store.edges[tuple(EDGE.values())]["status"] == "active"
    summary = store.states[("dataset", EMPLOYEES)]["summary"]
    assert "department_id" in summary["refuted_keys"]
    assert "employee_id" in summary["exact_keys"]


def test_an_edge_beyond_the_candidate_cap_is_not_retired(env, monkeypatch):
    local, host, store = env
    worker = _worker(local, host, store)
    _profile(worker, EMPLOYEES)
    assert store.edges[tuple(EDGE.values())]["status"] == "active"
    monkeypatch.setattr(catalog_copilot, "MAX_CONTAINMENT_CANDIDATES", 0)
    host.add(EMPLOYEES, EMPLOYEES_SQL, run_id="run-2")
    _profile(worker, EMPLOYEES)
    assert store.edges[tuple(EDGE.values())]["status"] == "active"


def test_an_edge_to_a_refuted_key_is_retired(env):
    local, host, store = env
    worker = _worker(local, host, store)
    _profile(worker, EMPLOYEES)
    host.add(
        DEPARTMENTS,
        DEPARTMENTS_SQL + " UNION ALL SELECT 'D001', 'Duplicado'",
        run_id="run-2",
    )
    _profile(worker, DEPARTMENTS)
    assert store.edges[tuple(EDGE.values())]["status"] == "retired"
    assert "department_id" in store.states[("dataset", DEPARTMENTS)]["summary"]["refuted_keys"]


def test_one_failing_containment_keeps_the_other_edges(env, monkeypatch):
    local, host, store = env
    store.seed_from_snapshot(
        "sap_successfactors_legacy_departments",
        host.add(
            "sap_successfactors_legacy_departments",
            "SELECT 'D' || lpad(CAST(i AS VARCHAR), 3, '0') AS department_id FROM range(60) t(i)",
        ),
    )
    worker = _worker(local, host, store)
    original = worker.probe.containment
    from refinement.app.catalog_copilot_probe import ProbeError

    def flaky(child, child_column, parent, parent_column, ctx, **kwargs):
        if "legacy_departments" in parent.sql:
            raise ProbeError("probe_timeout")
        return original(child, child_column, parent, parent_column, ctx, **kwargs)

    monkeypatch.setattr(worker.probe, "containment", flaky)
    outcome = _profile(worker)
    assert outcome["status"] == "partial"
    assert "probe_timeout" in outcome["errors"]
    assert store.edges[tuple(EDGE.values())]["status"] == "active"
    assert store.states[("dataset", EMPLOYEES)]["error_code"] == "probe_timeout"


def test_failed_edge_writes_still_save_a_partial_state(env):
    local, host, store = env
    store.fail_edges = True
    worker = _worker(local, host, store)
    outcome = _profile(worker)
    assert outcome["status"] == "partial"
    state = store.states[("dataset", EMPLOYEES)]
    assert (state["status"], state["error_code"]) == ("partial", "edge_write_failed")
    assert _profile(worker) == {"processed": False, "reason": "fresh"}


def test_candidates_rank_confirmed_parents_before_siblings(env, monkeypatch):
    """Reviewer reproduction: siblings sharing the FK name must not use up the cap."""
    local, host, store = env
    for index in range(4):
        store.seed_from_snapshot(
            f"sap_successfactors_sibling_{index}",
            host.add(
                f"sap_successfactors_sibling_{index}",
                "SELECT 'D' || lpad(CAST(i % 40 AS VARCHAR), 3, '0') AS department_id, i AS n "
                "FROM range(200) t(i)",
            ),
        )
    worker = _worker(local, host, store)
    _profile(worker, DEPARTMENTS)
    for index in range(4):
        _profile(worker, f"sap_successfactors_sibling_{index}")
    monkeypatch.setattr(catalog_copilot, "MAX_CONTAINMENT_CANDIDATES", 1)
    _profile(worker, EMPLOYEES)
    assert store.edges[tuple(EDGE.values())]["status"] == "active"


def test_one_to_one_pairs_are_stored_once(env):
    local, host, store = env
    store.seed_from_snapshot(
        "sap_successfactors_employee_extra",
        host.add(
            "sap_successfactors_employee_extra",
            "SELECT 'EMP' || lpad(CAST(i AS VARCHAR), 6, '0') AS employee_id, i AS badge "
            "FROM range(10000) t(i)",
        ),
    )
    worker = _worker(local, host, store)
    _profile(worker, "sap_successfactors_employee_extra")
    _profile(worker, EMPLOYEES)
    host.add(
        "sap_successfactors_employee_extra",
        "SELECT 'EMP' || lpad(CAST(i AS VARCHAR), 6, '0') AS employee_id, i AS badge "
        "FROM range(10000) t(i)",
        run_id="run-2",
    )
    _profile(worker, "sap_successfactors_employee_extra")
    pairs = [
        (row["from_dataset"], row["to_dataset"], row["cardinality"], row["status"])
        for row in store.edges.values()
        if row["from_column"] == "employee_id"
    ]
    active = [pair for pair in pairs if pair[3] == "active"]
    assert active == [
        ("sap_successfactors_employee_extra", EMPLOYEES, "1:1", "active")
    ]


def test_wide_bronze_sources_fit_the_state_row(env, tmp_path):
    """Reviewer reproduction: 200 columns with long names must still be saved."""
    local, host, store = env
    source = "raw/sap_successfactors/WideEntity"
    folder = (
        tmp_path
        / source
        / f"tenant_id={SEC['tenant_id']}"
        / f"workspace_id={SEC['workspace_id']}"
        / "load_date=2026-09-25"
        / "batch_id=1"
    )
    folder.mkdir(parents=True)
    table = pa.table(
        {
            f"emailAddressCustomFieldNumber{index:03d}AbcdefghijZ": [f"v{row}" for row in range(5)]
            for index in range(200)
        }
    )
    pq.write_table(table, folder / "a.parquet")
    host.sources.append(source)
    store.max_summary_bytes = 65_536
    worker = _worker(local, host, store)
    assert worker.profile_and_link(SEC, {"kind": "bronze_source", "name": source})["processed"]
    state = store.states[("bronze_source", source)]
    assert len(json.dumps(state["summary"]).encode()) <= 65_536
    assert state["summary"]["columns"]
    assert worker.profile_and_link(SEC, {"kind": "bronze_source", "name": source})["reason"] == "fresh"


def test_an_oversized_summary_is_saved_without_columns(env, tmp_path, monkeypatch):
    local, host, store = env
    source = "raw/sap_successfactors/PerEmail"
    folder = (
        tmp_path
        / source
        / f"tenant_id={SEC['tenant_id']}"
        / f"workspace_id={SEC['workspace_id']}"
        / "load_date=2026-09-25"
        / "batch_id=1"
    )
    folder.mkdir(parents=True)
    table = local.engine._con.execute(
        "SELECT 'x' || i AS emailAddress FROM range(3) t(i)"
    ).fetch_arrow_table()
    pq.write_table(table, folder / "a.parquet")
    host.sources.append(source)
    store.refuse_source_columns = True
    worker = _worker(local, host, store)
    outcome = worker.profile_and_link(SEC, {"kind": "bronze_source", "name": source})
    assert outcome["status"] == "partial"
    state = store.states[("bronze_source", source)]
    assert state["error_code"] == "summary_too_large"
    assert state["summary"]["columns"] == []


def test_source_scan_runs_in_the_background_and_is_throttled(env, tmp_path):
    local, host, store = env
    source = "raw/sap_successfactors/PerEmail"
    folder = (
        tmp_path
        / source
        / f"tenant_id={SEC['tenant_id']}"
        / f"workspace_id={SEC['workspace_id']}"
        / "load_date=2026-09-25"
        / "batch_id=1"
    )
    folder.mkdir(parents=True)
    table = local.engine._con.execute("SELECT 'P' || i AS personIdExternal FROM range(3) t(i)").fetch_arrow_table()
    pq.write_table(table, folder / "a.parquet")
    host.sources.append(source)
    listed = []
    original = host.list_sources
    host.list_sources = lambda sec: listed.append(1) or original(sec)
    worker = _worker(local, host, store)
    worker.catch_up(SEC, include_sources=True)
    assert worker.wait_idle(timeout=15)
    assert ("bronze_source", source) in store.states
    worker.catch_up(SEC, include_sources=True)
    assert worker.wait_idle(timeout=15)
    assert len(listed) == 1


def test_processed_counts_subjects_profiled_after_the_first_poll(env):
    local, host, store = env
    worker = _worker(local, host, store)
    first = worker.catch_up(SEC)
    assert (first.processed, first.pending) == (0, 2)
    assert first.annotation_epoch is None
    assert worker.wait_idle(timeout=15)
    follow = worker.catch_up(SEC, since="start")
    assert (follow.status, follow.processed, follow.pending) == ("ready", 2, 0)
    host.add(EMPLOYEES, EMPLOYEES_SQL, run_id="run-2")
    epoch = worker.catch_up(SEC).annotation_epoch
    assert worker.wait_idle(timeout=15)
    later = worker.catch_up(SEC, since=epoch)
    assert (later.status, later.processed, later.pending) == ("ready", 1, 0)
    assert worker.catch_up(SEC, since=later.annotation_epoch).status == "idle"


def test_an_unchecked_key_never_retires_edges_into_the_dataset(env, monkeypatch):
    """Reviewer reproduction: republished parent without stats + key check timeout."""
    local, host, store = env
    worker = _worker(local, host, store)
    _profile(worker, EMPLOYEES)
    assert store.edges[tuple(EDGE.values())]["status"] == "active"
    host.add(DEPARTMENTS, DEPARTMENTS_SQL, run_id="run-2")
    for row in store.columns.values():
        if row["dataset"] == DEPARTMENTS:
            row["null_rate"] = None
            row["distinct_count"] = None
    snapshot = host.snapshots[DEPARTMENTS]
    for field in snapshot.evidence["catalog"]:
        field["null_rate"] = None
        field["distinct_count"] = None
    original = worker.probe.column_probe

    def timed_out_keys(relation, ctx, *, key_cols, **kwargs):
        result = original(relation, ctx, key_cols=[], **kwargs)
        return result

    monkeypatch.setattr(worker.probe, "column_probe", timed_out_keys)
    outcome = _profile(worker, DEPARTMENTS)
    assert outcome["processed"] is True
    assert store.edges[tuple(EDGE.values())]["status"] == "active"


def test_budget_is_checked_before_downloading_published_objects(env, monkeypatch):
    """Reviewer reproduction: a zero budget must not fetch or hash any object."""
    local, host, store = env
    worker = _worker(local, host, store, subject_budget=0.0)
    fetched = []
    original = local.engine._published_dataset_head

    def spy(dataset, ctx):
        fetched.append(dataset["name"])
        return original(dataset, ctx)

    monkeypatch.setattr(local.engine, "_published_dataset_head", spy)
    outcome = _profile(worker)
    assert outcome["status"] == "partial"
    assert fetched == []
    assert store.states[("dataset", EMPLOYEES)]["error_code"] == "probe_budget_exhausted"
