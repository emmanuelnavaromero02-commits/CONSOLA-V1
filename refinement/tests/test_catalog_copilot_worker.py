from __future__ import annotations

import json
import queue
from datetime import datetime, timedelta, timezone

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


def test_catch_up_is_bounded_by_items_and_enqueues_the_rest(env):
    local, host, store = env
    host.add("sap_successfactors_locations", "SELECT 'L' || i AS location_id FROM range(5) t(i)")
    worker = _worker(local, host, store)
    status = worker.catch_up(SEC, max_items=1)
    assert (status.processed, status.pending, status.stale) == (1, 2, 3)
    assert status.status == "working"
    assert worker.wait_idle(timeout=10)
    assert len(store.states) == 3
    again = worker.catch_up(SEC)
    assert again.status == "idle"
    assert (again.processed, again.pending, again.stale) == (0, 0, 0)
    assert again.annotation_epoch is not None


def test_catch_up_respects_the_time_budget(env):
    local, host, store = env
    ticks = iter([0.0] + [10.0] * 50)
    worker = _worker(local, host, store, clock=lambda: next(ticks, 10.0))
    status = worker.catch_up(SEC, budget_ms=1500)
    assert status.processed == 0
    assert status.pending == 2
    worker.wait_idle(timeout=10)


def test_catch_up_caps_caller_limits(env):
    local, host, store = env
    worker = _worker(local, host, store)
    status = worker.catch_up(SEC, max_items=500, budget_ms=10_000_000)
    assert status.processed == 2


def test_catch_up_filters_by_cartridge(env):
    local, host, store = env
    host.add("hubspot_deals", "SELECT 'x' AS deal_id", cartridge="hubspot")
    worker = _worker(local, host, store)
    status = worker.catch_up(SEC, cartridge="hubspot")
    assert status.stale == 1
    assert ("dataset", "hubspot_deals") in store.states
    assert ("dataset", EMPLOYEES) not in store.states


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
    assert "Cubre del" in columns["lastModified"]["description"]
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


def test_catch_up_keeps_going_when_one_subject_breaks(env, monkeypatch):
    local, host, store = env
    worker = _worker(local, host, store)
    original = worker._profile_dataset

    def flaky(sec, name):
        if name == EMPLOYEES:
            raise RuntimeError("storage hiccup")
        return original(sec, name)

    monkeypatch.setattr(worker, "_profile_dataset", flaky)
    status = worker.catch_up(SEC)
    assert status.processed == 1
    assert ("dataset", DEPARTMENTS) in store.states
    assert ("dataset", EMPLOYEES) not in store.states
