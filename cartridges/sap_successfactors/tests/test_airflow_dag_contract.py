from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_single_entity_extract_dag_limits_active_runs_for_cart_service_backpressure():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "max_active_runs=2" in source


def test_extract_all_dag_is_serial_to_avoid_duplicate_bulk_extracts():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "max_active_runs=1" in source


def test_successfactors_dags_do_not_call_cart_service():
    for filename in ("sap_successfactors_extract.py", "sap_successfactors_extract_all.py"):
        source = (ROOT / "dags" / filename).read_text(encoding="utf-8")

        assert "SAP_SUCCESSFACTORS_URL" not in source
        assert "http://sap-successfactors:8203" not in source
        assert "CARTRIDGE_URL" not in source
        assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" not in source


def test_successfactors_dags_do_not_import_cartridge_job_runner():
    for filename in ("sap_successfactors_extract.py", "sap_successfactors_extract_all.py"):
        source = (ROOT / "dags" / filename).read_text(encoding="utf-8")

        assert "app.core.job_runner" not in source
        assert "app.core.refinement_triggers" in source


def test_extract_all_dag_uses_runtime_plan_and_target():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "get_extract_all_plan" in source
    assert 'target = str(conf.get("target") or "all").strip().lower()' in source


def test_extract_all_dag_forwards_sync_idempotency_key_to_runtime():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "base_idempotency_key" in source
    assert 'run_config["idempotency_key"] = entity_idempotency_key' in source


def test_successfactors_dags_require_vault_connection_id():
    for filename in ("sap_successfactors_extract.py", "sap_successfactors_extract_all.py"):
        source = (ROOT / "dags" / filename).read_text(encoding="utf-8")

        assert "entity_config.connection_id" in source
        assert "no default connection fallback is allowed" in source
        assert '"conn_id": conn_id' in source


def test_successfactors_dags_do_not_fail_bronze_when_silver_refresh_fails():
    for filename in ("sap_successfactors_extract.py", "sap_successfactors_extract_all.py"):
        source = (ROOT / "dags" / filename).read_text(encoding="utf-8")

        assert "def _try_silver_refresh" in source
        assert "silver_refresh = _try_silver_refresh" in source
        assert "Bronze extraction must remain" in source or "successful Bronze extract" in source


def test_successfactors_dags_refresh_signed_scope_at_task_runtime():
    for filename in ("sap_successfactors_extract.py", "sap_successfactors_extract_all.py"):
        source = (ROOT / "dags" / filename).read_text(encoding="utf-8")

        assert "if key not in {_SIGNATURE_FIELD, _SIGNED_AT_FIELD, _SIGNATURE_VERSION_FIELD}" in source
        assert "return _sign_security_context(unsigned)" in source


def test_single_entity_dag_updates_studio_trigger_pipeline_run():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert '"run_id": airflow_run_id' in source
    assert 'sap_successfactors_extract:{entity}:{airflow_run_id}' not in source
    assert "def _scope_from_conf" in source


def test_extract_all_dag_records_entity_pipeline_runs_for_studio():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "def _pipeline_run_save" in source
    assert '"dag_id": "sap_successfactors_extract_all"' in source
    assert '"run_id": run_id_override or f"{airflow_run_id}:{entity}"' in source
    assert "run_id_override=context.get(\"run_id\")" in source
    assert "runtime.classify_successful_extraction(result)" in source
    assert "runtime.classify_extraction_exception(entity, exc)" in source
    assert "silver_refresh" in source


def test_successfactors_gold_refresh_order_respects_target():
    from app.core import job_runner

    foundation = job_runner._successfactors_gold_datasets_for_target("foundation")
    talent = job_runner._successfactors_gold_datasets_for_target("talent")

    assert foundation == job_runner.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER
    assert talent[: len(foundation)] == foundation
    assert "sap_successfactors_talent_signals" == talent[-1]
