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


def test_employees_anomalies_is_in_the_foundation_sweep():
    foundation = _orders()["foundation_order"]
    assert _ANOMALIES in foundation, (
        "employees_anomalies must be in foundation_order so it materializes"
    )
    assert foundation.index("sap_successfactors_employee_360") < foundation.index(_ANOMALIES)


def test_employees_anomalies_degrades_to_empty_not_error():
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
    assert _ANOMALIES not in FOUNDATION_GOLD_FALLBACK_SQL


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
