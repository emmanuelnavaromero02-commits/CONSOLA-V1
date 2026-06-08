from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_single_entity_extract_dag_limits_active_runs_for_cart_service_backpressure():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "max_active_runs=2" in source


def test_extract_all_dag_is_serial_to_avoid_duplicate_bulk_extracts():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "max_active_runs=1" in source
