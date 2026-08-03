"""Benchmark approval must come from a server-owned ledger, never from the row.

Two independent holes are pinned here:

* a caller holding ``datasets.write`` could replace the packaged authority
  dataset through the generic ``save_dataset`` tool, and
* the projection guard decided ``approved`` from the row's own ``approval_*``
  columns, so a forged row attested itself.

Both must fail closed: without a verifiable authoritative record scoped to the
same tenant, workspace and head, the benchmark is unreviewed and readiness and
9-box may not consume it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
OTHER_WORKSPACE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BENCHMARK = "sap_successfactors_talent_benchmark_internal"

# The exact payload reproduced by the independent audit.
FORGED_ROW = {
    "approved": True,
    "approved_by": "42",
    "approved_at": "2026-08-04T00:00:00Z",
    "approval_actor_source": "server",
    "approval_recorded_by_server": True,
    "approval_authorization_verified": True,
    "approval_evidence_ref": "does-not-exist",
    "approval_authorization_ref": "does-not-exist",
    "approval_status": "approved",
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
}


def _guard():
    sys.path.insert(0, str(ROOT / "console"))
    try:
        return importlib.import_module(
            "app.services.intelligence.gold_projection_guard"
        )
    finally:
        try:
            sys.path.remove(str(ROOT / "console"))
        except ValueError:
            pass


def _project(row: dict) -> dict:
    return _guard().project_operational_truth_rows(BENCHMARK, [dict(row)])[0]


def test_forged_row_cannot_attest_itself():
    projected = _project(FORGED_ROW)

    assert projected["approved"] is False
    assert projected["approval_status"] in {"unreviewed", "blocked"}


@pytest.mark.parametrize(
    "override",
    [
        pytest.param(
            {"approval_evidence_ref": "does-not-exist"}, id="missing-evidence"
        ),
        pytest.param(
            {"approval_authorization_ref": "does-not-exist"}, id="missing-authorization"
        ),
        pytest.param({"tenant_id": OTHER_TENANT}, id="evidence-of-another-tenant"),
        pytest.param(
            {"workspace_id": OTHER_WORKSPACE}, id="actor-of-another-workspace"
        ),
    ],
)
def test_references_outside_the_scope_never_attest(override):
    projected = _project({**FORGED_ROW, **override})

    assert projected["approved"] is False
    assert projected["approval_status"] in {"unreviewed", "blocked"}


def test_derived_datasets_cannot_consume_an_unattested_benchmark():
    guard = _guard()
    readiness = guard.project_operational_truth_rows(
        "sap_successfactors_talent_readiness",
        [
            {
                "source_mode": "benchmark_internal",
                "benchmark_approval_valid": True,
                "benchmark_provenance_status": "approved_durable",
                "readiness_status": "ready",
                "readiness_score": 90.0,
            }
        ],
    )[0]

    # A derived row may only claim a durable benchmark result when the benchmark
    # itself is attested; the guard must not take its word for it.
    assert (
        readiness["readiness_status"] != "ready"
        or readiness.get("benchmark_provenance_status") != "approved_durable"
    )


def test_packaged_authority_datasets_are_not_writable_through_save_dataset():
    """The generic dataset writer must refuse authority/readiness internals."""
    source = (ROOT / "refinement/app/main.py").read_text(encoding="utf-8")

    assert "PROTECTED_AUTHORITY_DATASETS" in source
    save_block = source.split('if tool == "save_dataset":', 1)[1].split(
        'if tool == "delete_dataset":', 1
    )[0]
    assert "PROTECTED_AUTHORITY_DATASETS" in save_block


def test_protected_registry_covers_the_authority_and_readiness_internals():
    sys.path.insert(0, str(ROOT / "refinement"))
    try:
        store = importlib.import_module("app.dataset_store")
    finally:
        try:
            sys.path.remove(str(ROOT / "refinement"))
        except ValueError:
            pass

    protected = getattr(store, "PROTECTED_AUTHORITY_DATASETS", frozenset())
    assert BENCHMARK in protected
    assert "sap_successfactors_talent_readiness" in protected
    assert "sap_successfactors_talent_9box" in protected


def test_save_dataset_refuses_a_protected_dataset(monkeypatch):
    sys.path.insert(0, str(ROOT / "refinement"))
    try:
        store_module = importlib.import_module("app.dataset_store")
    finally:
        try:
            sys.path.remove(str(ROOT / "refinement"))
        except ValueError:
            pass

    store = store_module.DatasetStore.__new__(store_module.DatasetStore)
    with pytest.raises(store_module.ProtectedDatasetError):
        store.save_dataset(
            {
                "name": BENCHMARK,
                "sql_def": "SELECT 1",
                "tenant_id": TENANT,
                "workspace_id": WORKSPACE,
            }
        )
