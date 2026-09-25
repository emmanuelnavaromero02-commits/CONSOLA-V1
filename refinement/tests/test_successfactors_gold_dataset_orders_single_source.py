from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CANON = _REPO / "cartridges" / "sap_successfactors" / "app" / "config" / "gold_dataset_orders.json"

_CONSUMERS = [
    _REPO / "cartridges" / "sap_successfactors" / "app" / "core" / "refinement_triggers.py",
    _REPO / "cartridges" / "sap_successfactors" / "app" / "core" / "job_runner.py",
    _REPO / "refinement" / "scripts" / "materialize_successfactors_foundation.py",
]
_ORDER_NAMES = {
    "SUCCESSFACTORS_GOLD_FOUNDATION_ORDER",
    "SUCCESSFACTORS_GOLD_TALENT_ORDER",
    "SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER",
    "SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER",
    "SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER",
}
_COHORT_MONTH = [
    "sap_successfactors_talent_headcount_by_cohort_month",
    "sap_successfactors_talent_tenure_by_cohort_month",
    "sap_successfactors_talent_attrition_by_cohort_month",
]


def _canon() -> dict:
    return json.loads(_CANON.read_text(encoding="utf-8"))


def test_canonical_json_is_well_formed_and_invariant_holds():
    data = _canon()
    for key in ("foundation_order", "silver_talent_curated_order", "talent_order",
                "talent_contract_order", "talent_operational_order"):
        assert isinstance(data.get(key), list) and data[key], f"falta/ vacia: {key}"
        assert len(data[key]) == len(set(data[key])), f"duplicados en {key}"
    assert data["talent_contract_order"] + data["talent_operational_order"] == data["talent_order"]


def test_cohort_month_datasets_present_and_ordered_before_signals():
    talent = _canon()["talent_order"]
    idx = {name: i for i, name in enumerate(talent)}
    for ds in _COHORT_MONTH:
        assert ds in idx, f"{ds} ausente de talent_order"
    last_cohort = max(idx[ds] for ds in _COHORT_MONTH)
    assert last_cohort < idx["sap_successfactors_talent_signals"]
    assert last_cohort < idx["sap_successfactors_talent_operational_features"]


def test_materialize_runner_matches_canonical_source():
    from scripts.materialize_successfactors_foundation import (
        ALLOWED_FOUNDATION_DATASETS,
        SUCCESSFACTORS_GOLD_FOUNDATION_ORDER,
        SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER,
        SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER,
        SUCCESSFACTORS_GOLD_TALENT_ORDER,
    )
    data = _canon()
    assert SUCCESSFACTORS_GOLD_FOUNDATION_ORDER == data["foundation_order"]
    assert SUCCESSFACTORS_GOLD_TALENT_ORDER == data["talent_order"]
    assert SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER == data["talent_contract_order"]
    assert SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER == data["talent_operational_order"]
    assert ALLOWED_FOUNDATION_DATASETS == set(data["foundation_order"]) | set(data["talent_order"])


@pytest.mark.parametrize("path", _CONSUMERS, ids=lambda p: p.name)
def test_consumers_do_not_hardcode_order_lists(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _ORDER_NAMES:
                    offenders.append((target.id, getattr(node, "lineno", "?")))
    assert not offenders, (
        f"{path.name} re-hardcodea listas de orden (usa la fuente unica): {offenders}"
    )
