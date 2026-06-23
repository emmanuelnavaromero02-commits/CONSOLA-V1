from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_single_entity_extract_dag_limits_active_runs_for_cart_service_backpressure():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "max_active_runs=2" in source


def test_extract_all_dag_is_serial_to_avoid_duplicate_bulk_extracts():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "max_active_runs=1" in source


def test_extract_all_dag_timeout_is_extended_for_bulk_live_runs():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "SAP_SUCCESSFACTORS_EXTRACT_ALL_TIMEOUT_SECONDS" in source
    assert "_DEFAULT_EXTRACT_ALL_TIMEOUT_SECONDS = 3600" in source
    assert "httpx.Timeout(_extract_all_timeout_seconds(), connect=30.0)" in source
    assert "httpx.Client(timeout=300)" not in source


def test_extract_all_dag_forwards_sync_target_to_cartridge():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert '"target": conf.get("target") or "all"' in source


def test_extract_all_dag_forwards_sync_idempotency_key_to_cartridge():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert '"idempotency_key": conf.get("idempotency_key") or None' in source
