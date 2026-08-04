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
HEAD = "33333333-3333-3333-3333-333333333333"

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


def _authority(**overrides) -> dict:
    entry = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "dataset": BENCHMARK,
        "materialization_head": HEAD,
        "actor_user_id": 77,
        "evidence_ref": "evidence:9001",
        "authorization_ref": "44444444-4444-4444-4444-444444444444",
        "authorization_role_id": 9,
        "evidence_digest": "a" * 64,
        "evidence_digest_version": 1,
        "evidence_item_count": 1,
        "authorization_digest": "b" * 64,
        "approval_status": "approved",
        "approved_at": "2026-08-04T01:00:00Z",
        "recorded_by_server": True,
    }
    entry.update(overrides)
    return {(TENANT, WORKSPACE): entry}


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


def test_server_authority_overlays_instead_of_corroborating_row_claims():
    """All approval fields come from the ledger; forged row values are ignored."""
    guard = _guard()
    projected = guard.project_operational_truth_rows(
        BENCHMARK,
        [dict(FORGED_ROW)],
        _authority(),
        benchmark_head=HEAD,
    )[0]

    assert projected["approved"] is True
    assert projected["approved_by"] == "77"
    assert projected["approved_at"] == "2026-08-04T01:00:00Z"
    assert projected["approval_evidence_ref"] == "evidence:9001"
    assert projected["approval_authorization_ref"].startswith("44444444-")
    assert "does-not-exist" not in repr(projected)


@pytest.mark.parametrize(
    "authority",
    [
        pytest.param(_authority(dataset="attacker-selected"), id="wrong-dataset"),
        pytest.param(_authority(materialization_head="55555555-5555-5555-5555-555555555555"), id="wrong-head"),
        pytest.param(_authority(tenant_id=OTHER_TENANT), id="wrong-tenant"),
        pytest.param(_authority(workspace_id=OTHER_WORKSPACE), id="wrong-workspace"),
    ],
)
def test_authority_must_match_exact_scope_dataset_and_head(authority):
    guard = _guard()
    projected = guard.project_operational_truth_rows(
        BENCHMARK,
        [dict(FORGED_ROW)],
        authority,
        benchmark_head=HEAD,
    )[0]

    assert projected["approved"] is False
    assert projected["approval_status"] in {"unreviewed", "blocked"}


def test_packaged_authority_datasets_are_not_writable_through_save_dataset():
    """The generic dataset writer must refuse authority/readiness internals."""
    source = (ROOT / "refinement/app/main.py").read_text(encoding="utf-8")

    assert "is_protected_dataset" in source
    save_block = source.split('if tool == "save_dataset":', 1)[1].split(
        'if tool == "delete_dataset":', 1
    )[0]
    assert "is_protected_dataset" in save_block


def test_protected_registry_covers_the_authority_and_readiness_internals():
    sys.path.insert(0, str(ROOT / "refinement"))
    try:
        store = importlib.import_module("app.dataset_store")
    finally:
        try:
            sys.path.remove(str(ROOT / "refinement"))
        except ValueError:
            pass

    protection = importlib.import_module("app.dataset_protection")
    protected = {
        BENCHMARK,
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
        "sap_successfactors_talent_employee_profile",
        "sap_successfactors_talent_cpa_scores",
        "sap_successfactors_talent_9box_operational",
        "sap_successfactors_talent_promotion_alignment",
        "sap_successfactors_talent_calibration_sensitivity",
        "sap_successfactors_talent_retention_risk",
        "sap_successfactors_talent_role_fit_assignments",
        "sap_successfactors_talent_role_profile",
        "sap_successfactors_talent_mobility_history",
        "sap_successfactors_talent_action_candidates",
        "sap_successfactors_talent_signals",
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_simulation_inputs",
        "sap_successfactors_employee_360",
        "sap_successfactors_performance_cycle",
        "sap_successfactors_employee_competency",
        "sap_successfactors_employee_aspiration",
    }
    assert all(protection.is_protected_dataset(name) for name in protected)
    assert not protection.is_protected_dataset("custom_workforce_view")


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
    for name in store_module.PROTECTED_AUTHORITY_DATASETS:
        with pytest.raises(store_module.ProtectedDatasetError):
            store.save_dataset(
                {
                    "name": name,
                    "sql_def": "SELECT 1",
                    "tenant_id": TENANT,
                    "workspace_id": WORKSPACE,
                }
            )


def test_ledger_function_derives_scope_actor_and_authorization_server_side():
    sql = (ROOT / "infra/init/99zzs_talent_benchmark_approval_ledger.sql").read_text(
        encoding="utf-8"
    ).lower()
    function = sql.split("create or replace function record_talent_benchmark_approval", 1)[1]

    assert "current_setting('app.tenant_id'" in function
    assert "current_setting('app.workspace_id'" in function
    assert "current_setting('app.user_id'" in function
    assert "control_room_approver" in function
    assert "evidence_packs" in function
    assert "materialization_head" in function
    assert "metadata" in function
    assert "on conflict" not in function.split("alter function", 1)[0]


def test_gold_fetcher_resolves_authority_for_the_exact_benchmark_head():
    source = (
        ROOT / "console/app/services/intelligence/gold_fetcher.py"
    ).read_text(encoding="utf-8")

    assert "resolve_benchmark_approval_authority" in source
    assert "benchmark_head=" in source
