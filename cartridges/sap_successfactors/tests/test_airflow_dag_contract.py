from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_single_entity_extract_dag_limits_active_runs_for_cart_service_backpressure():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "max_active_runs=1" in source


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


def test_single_entity_dag_records_missing_connection_without_retry():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "conn_id = _required_conn_id(conf, config)" in source
    assert '"code": "CONFIG_INCOMPLETE"' in source
    assert "raise AirflowFailException(str(exc)) from exc" in source


def test_extract_all_dag_classifies_missing_entity_connections_per_entity():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    # Each entity gets its own idempotency key, derived from the run's base key
    # and the entity itself, so one entity retrying cannot collide with another.
    #
    # Asserted on the call's parts rather than on one physical line: the
    # previous version required the whole expression unwrapped, and a routine
    # reformat broke the release gate while the behaviour was intact. What
    # matters is that the derivation happens and takes both inputs.
    assignment = re.search(
        r"entity_idempotency_key\s*=\s*_entity_idempotency_key\(\s*"
        r"base_idempotency_key\s*,\s*entity\s*,?\s*\)",
        source,
    )
    assert assignment, "entity_idempotency_key must derive from (base_idempotency_key, entity)"
    # And it must sit inside the per-entity loop, not be hoisted out of it.
    per_entity_loop = source.index("for config in entities:")
    assert assignment.start() > per_entity_loop, (
        "the per-entity idempotency key must be derived inside the entity loop"
    )
    assert "try:\n                    conn_id = _required_config_conn_id(conf, config)" in source
    assert "runtime.classify_extraction_exception(entity, exc)" in source


def test_extract_all_dag_records_successfactors_metadata_blocks_as_partial():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "def _is_nonfatal_successfactors_block" in source
    assert "entity_pipeline_status = (" in source
    assert 'status=entity_pipeline_status' in source
    assert '\"SUCCESSFACTORS_METADATA_BLOCKED\"' in source
    assert '\"SUCCESSFACTORS_PERMISSION\"' in source


def test_single_entity_dag_preflights_metadata_and_returns_partial_blocks():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "prepare_entity_config_for_metadata" in source
    assert "metadata_block" in source
    assert 'status=\"partial\"' in source
    assert "runtime.classify_extraction_exception(str(entity), exc)" in source


def test_single_entity_dag_treats_successfactors_metadata_errors_as_non_retryable():
    source = (ROOT / "dags" / "sap_successfactors_extract.py").read_text(encoding="utf-8")

    assert "http 400" in source
    assert "http 404" in source
    assert "notfoundexception" in source
    assert "invalid property" in source


def test_successfactors_dags_do_not_fail_bronze_when_silver_refresh_fails():
    for filename in ("sap_successfactors_extract.py", "sap_successfactors_extract_all.py"):
        source = (ROOT / "dags" / filename).read_text(encoding="utf-8")

        assert "def _try_silver_refresh" in source
        assert "silver_refresh = _try_silver_refresh" in source
        assert "Bronze extraction must remain" in source or "successful Bronze extract" in source
        assert "def _pipeline_status_for_success_payload" in source
        assert 'return "partial"' in source
        assert '"silver_refresh"' in source
        assert '"empty_result"' in source


def test_successfactors_silver_refresh_reports_missing_refinements_as_partial():
    source = (
        ROOT / "app" / "core" / "refinement_triggers.py"
    ).read_text(encoding="utf-8")

    assert 'payload.get("status") in {"partial", "skipped"}' in source
    assert 'int(payload.get("refreshed") or 0) <= 0' in source


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
    foundation_silver = job_runner._successfactors_curated_silver_datasets_for_target(
        "foundation"
    )
    talent_silver = job_runner._successfactors_curated_silver_datasets_for_target(
        "talent"
    )

    assert foundation == job_runner.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER
    assert foundation_silver == []
    assert talent_silver[:3] == [
        "sap_successfactors_performance_cycle",
        "sap_successfactors_employee_competency",
        "sap_successfactors_employee_aspiration",
    ]
    assert talent[: len(foundation)] == foundation
    assert talent[-3:] == [
        "sap_successfactors_talent_signals",
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_simulation_inputs",
    ]


def test_extract_all_dag_bridges_to_intelligence_via_dataset_refresh_chain():
    # P3: el DAG programado de SF debe puentear a la capa de inteligencia (como ya
    # hacen HubSpot/Replicon) disparando el meta-DAG dataset_refresh_chain tras la
    # extraccion. Sin este puente, el dato extraido nunca alcanza los motores de
    # decision por el reloj ("se extrae de SF y no pasa nada").
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "dataset_refresh_chain" in source
    assert "def trigger_refresh_chain" in source
    # The bridge now carries an authority token end to end: the chain is
    # authorised once and that authorisation is threaded through both the
    # extraction and the trigger, so a refresh cannot be fired without it.
    # That is a deliberate tightening — assert the current shape rather than
    # the old unauthenticated call.
    assert re.search(r"authority\s*=\s*authorize_refresh_chain\(", source), (
        "the refresh chain must be authorised before it is triggered"
    )
    assert re.search(
        r"trigger_refresh_chain\(\s*trigger_extract_all\(\s*authority\s*\)\s*,\s*authority\s*,?\s*\)",
        source,
    ), "trigger_refresh_chain must receive the extraction result and the authority"
    # Debe respetar la separacion de responsabilidades: puentea por REST, NO
    # importando el job_runner del cartucho dentro del DAG.
    assert "app.core.job_runner" not in source
