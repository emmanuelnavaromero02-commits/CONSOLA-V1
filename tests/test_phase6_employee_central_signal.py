"""Fase 6 — the Employee Central deterministic anomaly signal must actually
materialize and reach the Control Room, and the WB-TALENTO action methods must
name what they really are.

Two gaps this pins:
  (1) sap_successfactors_employees_anomalies was orphaned from the materialization
      order and had no fallback, so the "empleado activo sin manager/departamento/
      job invalido" signal never lit up and hard-errored on missing dependencies.
  (2) talent_action_candidates labelled SQL COUNT heuristics as method='permutation'
      and method='assignment' — claiming a rigor (permutation test / assignment
      optimizer) the code does not run.
"""
from __future__ import annotations

import json
from pathlib import Path

from refinement.app.successfactors_fallbacks import fallback_dataset_for_successfactors
from refinement.app.successfactors_foundation_fallbacks import (
    FOUNDATION_GOLD_FALLBACK_SQL,
)

REPO = Path(__file__).resolve().parents[1]
ORDERS = (
    REPO
    / "cartridges"
    / "sap_successfactors"
    / "app"
    / "config"
    / "gold_dataset_orders.json"
)
ACTION_CANDIDATES_SQL = (
    REPO
    / "cartridges"
    / "sap_successfactors"
    / "datasets"
    / "sap_successfactors_talent_action_candidates.sql"
)
TALENT_SEED = REPO / "infra" / "init" / "99p_sap_successfactors_talent_datasets.sql"

_ANOMALIES = "sap_successfactors_employees_anomalies"
_ANOMALY_COLUMNS = ("user_id", "full_name", "anomaly_type", "severity", "details", "detected_at")


def _orders() -> dict:
    return json.loads(ORDERS.read_text(encoding="utf-8"))


# ── (1) the anomaly dataset is now materializable + degrades honestly ────────

def test_employees_anomalies_is_in_the_foundation_sweep():
    """It only depends on employee_360 (foundation gold) + fojobcode silver, so
    it belongs to the foundation order — otherwise the sweep never builds it."""
    foundation = _orders()["foundation_order"]
    assert _ANOMALIES in foundation, (
        "employees_anomalies must be in foundation_order so it materializes"
    )
    # employee_360 is its dependency and must be materialized first.
    assert foundation.index("sap_successfactors_employee_360") < foundation.index(_ANOMALIES)


def test_employees_anomalies_degrades_to_empty_not_error():
    """A missing dependency must degrade to an empty result with the exact
    columns, never a hard error (an empty anomaly set is a HEALTHY state)."""
    fallback = fallback_dataset_for_successfactors(
        {"name": _ANOMALIES, "layer": "gold"},
        RuntimeError("No files found that match read_parquet source"),
    )
    assert fallback is not None, "employees_anomalies must have a fallback"
    sql = fallback["sql_def"]
    assert "WHERE FALSE" in sql
    assert "FROM read_parquet" not in sql
    for col in _ANOMALY_COLUMNS:
        assert f"AS {col}" in sql, f"fallback is missing column {col}"


def test_empty_anomalies_never_escalates_as_a_foundation_error():
    """employees_anomalies must NOT be a foundation fallback: an empty result is
    healthy and must never trip the strict foundation-empty error path."""
    assert _ANOMALIES not in FOUNDATION_GOLD_FALLBACK_SQL


# ── (2) the WB-TALENTO action methods name what they really are ──────────────

def test_action_candidate_methods_do_not_claim_false_rigor():
    for src in (ACTION_CANDIDATES_SQL.read_text(encoding="utf-8"),
                TALENT_SEED.read_text(encoding="utf-8")):
        assert "'permutation' AS method" not in src, (
            "a SQL COUNT heuristic must not be labelled as a permutation test"
        )
        assert "'assignment' AS method" not in src, (
            "a SQL COUNT heuristic must not be labelled as an assignment optimizer"
        )


def test_action_candidate_methods_use_honest_heuristic_names():
    sql = ACTION_CANDIDATES_SQL.read_text(encoding="utf-8")
    assert "'promotion_alignment_heuristic' AS method" in sql
    assert "'role_fit_heuristic' AS method" in sql
