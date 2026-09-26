from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_changed_areas.py"
SPEC = importlib.util.spec_from_file_location("ci_changed_areas", SCRIPT)
assert SPEC and SPEC.loader
ci_changed_areas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_changed_areas)


def _flags(*files: str) -> dict[str, object]:
    return ci_changed_areas._flags(list(files))


def _root_targets(flags: dict[str, object]) -> set[str]:
    return set(str(flags["root_test_targets"]).split())


def test_dataset_sql_changes_skip_runtime_and_full_stack_gates():
    flags = _flags(
        "cartridges/sap_successfactors/datasets/sap_successfactors_user_latest.sql",
        "tests/test_sap_successfactors_datasets.py",
    )

    assert flags["python"] is True
    assert flags["python_runtime"] is False
    assert flags["dataset"] is True
    assert flags["root_tests"] is True
    assert flags["cartridge_tests"] is True
    assert flags["e2e"] is False
    assert flags["release_full_stack"] is False
    assert flags["has_build_matrix"] is False
    assert flags["root_test_targets"] == "tests/test_sap_successfactors_datasets.py"
    assert flags["cartridge_test_targets"] == "cartridges/sap_successfactors/tests/test_airflow_dag_contract.py"
    assert flags["cartridge_requirement_paths"] == "cartridges/sap_successfactors/requirements.txt"


def test_deleted_release_test_is_reported_and_cannot_disappear_from_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ci_changed_areas,
        "_run_diff_paths",
        lambda *_args, **_kwargs: ["cartridges/retired_connector/tests/test_contract.py"],
    )

    assert ci_changed_areas._deleted_files("base", "head") == [
        "cartridges/retired_connector/tests/test_contract.py"
    ]


def test_console_frontend_and_compose_changes_trigger_heavier_surfaces():
    flags = _flags(
        "console/app/main.py",
        "console-next/src/app/page.tsx",
        "console-next/package-lock.json",
        "infra/docker-compose.yml",
    )

    assert flags["python_runtime"] is True
    assert flags["frontend"] is True
    assert flags["node_deps"] is True
    assert flags["compose"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is True
    matrix = json.loads(str(flags["build_matrix"]))
    assert {"service": "console", "context": "./console"} in matrix["include"]


def test_changed_console_tests_are_release_root_targets():
    flags = _flags(
        "console/app/main.py",
        "console/tests/test_agent_runner_scheduler_auth.py",
        "console/tests/test_ops_summary_and_version.py",
    )

    assert flags["root_tests"] is True
    targets = str(flags["root_test_targets"]).split()
    assert targets == [
        "console/tests/test_agent_runner_scheduler_auth.py",
        "console/tests/test_ops_summary_and_version.py",
    ]


def test_agent_runner_runtime_only_changes_select_exact_console_regressions():
    flags = _flags(
        "console/app/main.py",
        "console/app/routers/operations.py",
        "console/app/services/scheduled_runtime.py",
    )

    targets = set(str(flags["root_test_targets"]).split())
    assert "console/tests/test_agent_runner_scheduler_auth.py" in targets
    assert "tests/test_operational_truth_runtime_red.py" in targets


def test_main_auth_contract_is_not_masked_by_an_unrelated_changed_test():
    flags = _flags(
        "console/app/main.py",
        "tests/test_ci_changed_areas.py",
    )

    targets = set(str(flags["root_test_targets"]).split())
    assert "tests/test_ci_changed_areas.py" in targets
    assert "console/tests/test_agent_runner_scheduler_auth.py" in targets


def test_successfactors_health_runtime_selects_secret_sentinel_contract():
    for changed_file in (
        "cartridges/sap_successfactors/app/main.py",
        "cartridges/sap_successfactors/app/api/routes_health.py",
        "cartridges/sap_successfactors/app/core/startup_status.py",
        "cartridges/sap_successfactors/app/services/catalog_service.py",
    ):
        targets = set(str(_flags(changed_file)["root_test_targets"]).split())
        assert "tests/test_cartridge_startup_fail_fast.py" in targets


def test_gcp_secret_and_day2_runtime_changes_select_fail_closed_contracts():
    for changed_file in (
        "infra/terraform-gcp/release/hydrate-runtime-secrets.sh",
        "scripts/gcp/gcp-canonical-deploy.sh",
        "scripts/gcp/gcp-canonical-deploy-remote.sh",
    ):
        flags = _flags(changed_file)
        targets = set(str(flags["root_test_targets"]).split())
        assert "tests/test_gcp_runtime_secret_hydration.py" in targets
        if changed_file.startswith("scripts/gcp/"):
            assert "tests/test_gcp_canonical_deploy.py" in targets
        assert flags["release_full_stack"] is True


def test_file_ingest_runtime_change_selects_accumulation_and_gcs_contract():
    flags = _flags("airflow/dags/file_ingest.py")

    assert "tests/test_t2_file_ingest_accumulates.py" in _root_targets(flags)


def test_console_storage_generators_select_their_exact_regressions():
    expected = {
        "console/app/services/s3_client.py": "console/tests/test_console_s3_iam_client.py",
        "console/app/services/cartridge_service.py": "console/tests/test_cartridge_service_storage_provider.py",
        "console/app/services/dag_templates.py": "console/tests/test_dag_templates.py",
        "console/app/services/dag_code_generator.py": "tests/test_dag_codegen_security.py",
    }
    for changed_file, target in expected.items():
        flags = _flags(changed_file)
        assert target in set(str(flags["root_test_targets"]).split())


def test_provider_runtime_only_changes_select_cross_provider_contracts():
    for changed_file in (
        "cartridges/hubspot/app/core/config.py",
        "cartridges/replicon/app/core/minio_client.py",
        "cartridges/salesforce/app/services/duckdb_service.py",
        "cartridges/sap_hcm/app/core/config.py",
        "cartridges/sap_s4hana/app/services/duckdb_service.py",
        "mcp-infra/app/lakehouse_runtime.py",
    ):
        targets = set(str(_flags(changed_file)["root_test_targets"]).split())
        assert "tests/test_phase0_provider_safe_storage.py" in targets
        if "/core/" in changed_file:
            assert "tests/test_gcp_runtime_secret_hydration.py" in targets

    overlay_targets = set(
        str(
            _flags("infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl")[
                "root_test_targets"
            ]
        ).split()
    )
    assert {
        "tests/test_phase0_provider_safe_storage.py",
        "tests/test_gcp_runtime_secret_hydration.py",
        "tests/test_gcp_canonical_deploy.py",
        "tests/test_replicon_ses_upload_scope.py",
    } <= overlay_targets

    renderer_targets = _root_targets(
        _flags("scripts/gcp/render_gcp_compose_override.py")
    )
    assert "tests/test_gcp_canonical_deploy.py" in renderer_targets
    assert _flags("scripts/gcp/render_gcp_compose_override.py")[
        "release_full_stack"
    ] is True


def test_offline_aws_extension_runtime_changes_select_preload_contracts():
    for changed_file in (
        "cartridges/hubspot/Dockerfile",
        "cartridges/replicon/Dockerfile",
        "cartridges/salesforce/Dockerfile",
        "cartridges/sap_hcm/Dockerfile",
        "cartridges/sap_s4hana/Dockerfile",
        "cartridges/sap_successfactors/Dockerfile",
        "mcp-infra/app/lakehouse_runtime.py",
        "mcp-infra/scripts/install_duckdb_extensions.py",
        "mcp-infra/scripts/duckdb_offline_smoke.py",
    ):
        targets = set(str(_flags(changed_file)["root_test_targets"]).split())
        assert {
            "tests/test_duckdb_p0_guard.py",
            "tests/test_phase0_provider_safe_storage.py",
        } <= targets

    for changed_file in (
        "refinement/app/duckdb_engine.py",
        "refinement/scripts/install_duckdb_extensions.py",
        "refinement/scripts/duckdb_offline_smoke.py",
        "scripts/prepare_refinement_duckdb_ci.sh",
    ):
        targets = set(str(_flags(changed_file)["root_test_targets"]).split())
        assert {
            "refinement/tests/test_duckdb_s3_materialize_paths.py",
            "tests/test_refinement_duckdb_extensions.py",
        } <= targets


def test_phase0_selects_console_and_refinement_release_targets_together():
    targets = _root_targets(
        _flags(
            "console/app/main.py",
            "refinement/app/duckdb_engine.py",
        )
    )

    assert "console/tests/test_agent_runner_scheduler_auth.py" in targets
    assert "refinement/tests/test_duckdb_s3_materialize_paths.py" in targets
    assert "tests/test_refinement_duckdb_extensions.py" in targets


def test_release_partition_and_migration_runner_select_exact_contracts():
    for changed_file in (
        ".github/workflows/release.yml",
        "scripts/run_release_pytest.py",
    ):
        targets = _root_targets(_flags(changed_file))
        assert "tests/test_release_root_target_partition.py" in targets

    migration_targets = _root_targets(_flags("scripts/apply_db_migrations.sh"))
    assert {
        "tests/test_apply_db_migrations_script.py",
        "tests/test_schema_migrations_tracking.py",
    } <= migration_targets


def test_cartridge_runtime_change_builds_only_that_cartridge():
    flags = _flags("cartridges/sap_successfactors/app/core/job_runner.py")

    assert flags["python_runtime"] is True
    assert flags["cartridge_tests"] is True
    assert flags["release_full_stack"] is True
    matrix = json.loads(str(flags["build_matrix"]))
    assert matrix == {
        "include": [
            {
                "service": "sap_successfactors",
                "context": "./cartridges/sap_successfactors",
            }
        ]
    }
    assert "tests/test_sap_successfactors_airflow_runtime.py" in str(flags["root_test_targets"])
    assert flags["cartridge_test_targets"] == "cartridges/sap_successfactors/tests"


def test_banxico_runtime_change_builds_with_cartridge_tests():
    flags = _flags("cartridges/banxico/app/core/banxico_client.py")

    assert flags["python_runtime"] is True
    assert flags["cartridge_tests"] is True
    matrix = json.loads(str(flags["build_matrix"]))
    assert {"service": "banxico", "context": "./cartridges/banxico"} in matrix["include"]
    assert flags["cartridge_test_targets"] == "cartridges/banxico/tests"


def test_inegi_runtime_change_builds_with_cartridge_tests():
    flags = _flags("cartridges/inegi/app/core/inegi_client.py")

    assert flags["python_runtime"] is True
    assert flags["cartridge_tests"] is True
    matrix = json.loads(str(flags["build_matrix"]))
    assert {"service": "inegi", "context": "./cartridges/inegi"} in matrix["include"]
    assert flags["cartridge_test_targets"] == "cartridges/inegi/tests"


def test_sec_edgar_runtime_change_builds_with_cartridge_tests():
    flags = _flags("cartridges/sec_edgar/app/core/sec_client.py")

    assert flags["python_runtime"] is True
    assert flags["cartridge_tests"] is True
    matrix = json.loads(str(flags["build_matrix"]))
    assert {"service": "sec_edgar", "context": "./cartridges/sec_edgar"} in matrix["include"]
    assert flags["cartridge_test_targets"] == "cartridges/sec_edgar/tests"


def test_dependency_changes_trigger_security_without_full_stack_by_default():
    flags = _flags("console/requirements.txt", "tests-e2e/package-lock.json")

    assert flags["python_deps"] is True
    assert flags["node_deps"] is True
    assert flags["release_full_stack"] is False


def test_pipeline_reconciler_is_a_security_scanned_python_runtime():
    flags = _flags("scripts/reconcile_pipeline_runs.py")

    assert flags["python"] is True
    assert flags["python_runtime"] is True
    assert flags["control_room"] is True


def test_mcp_infra_pdf_changes_run_functional_security_tests():
    for changed_file in (
        "mcp-infra/requirements.txt",
        "mcp-infra/app/rag/ingest.py",
        "mcp-infra/app/rag/pdf_capacity.py",
        "mcp-infra/app/rag/pdf_worker.py",
        "mcp-infra/app/main.py",
    ):
        targets = str(_flags(changed_file)["root_test_targets"])
        assert "tests/test_mcp_infra_pdf_ingest.py" in targets
        assert "tests/test_mcp_infra_pdf_capacity.py" in targets
        assert "tests/test_mcp_infra_pdf_compose_capacity.py" in targets
        assert "tests/test_pypdf_security.py" in targets


def test_console_page_route_changes_do_not_trigger_full_stack_release_gate():
    flags = _flags("console/app/routers/pages.py")

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False


def test_console_main_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags("console/app/main.py")

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["e2e"] is False
    assert flags["release_full_stack"] is False


def test_console_service_url_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/service_urls.py",
        "console/app/services/operations_service.py",
        "console/app/services/vault_utils.py",
        "tests/test_service_urls.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert flags["root_test_targets"] == "tests/test_service_urls.py"


def test_console_status_page_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/status_pages.py",
        "console/app/main.py",
        "tests/test_status_pages.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_status_pages.py",
    }


def test_console_security_header_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/security_headers.py",
        "console/app/main.py",
        "tests/test_security_headers.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_security_headers.py",
    }


def test_console_request_rate_limit_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/request_rate_limits.py",
        "console/app/main.py",
        "tests/test_request_rate_limits.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_request_rate_limits.py",
    }


def test_console_mcp_payload_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/mcp_payloads.py",
        "console/app/main.py",
        "tests/test_mcp_payloads.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_mcp_payloads.py",
    }


def test_console_db_pool_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/db_pool.py",
        "console/app/main.py",
        "tests/test_db_pool.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_db_pool.py",
    }


def test_console_startup_readiness_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/startup_readiness.py",
        "console/app/main.py",
        "tests/test_startup_readiness.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_startup_readiness.py",
    }


def test_console_runtime_call_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/runtime_calls.py",
        "console/app/main.py",
        "tests/test_runtime_calls.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_runtime_calls.py",
    }


def test_console_readyz_dependency_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/readyz_dependencies.py",
        "console/app/main.py",
        "tests/test_readyz_dependencies.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_readyz_dependencies.py",
    }


def test_console_readyz_data_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/readyz_data.py",
        "console/app/main.py",
        "tests/test_readyz_data.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_readyz_data.py",
    }


def test_console_sync_progress_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/sync_progress.py",
        "console/app/main.py",
        "tests/test_sync_progress.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_sync_progress.py",
    }


def test_console_sync_agentops_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/sync_agentops.py",
        "console/app/main.py",
        "tests/test_sync_agentops.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_sync_agentops.py",
    }


def test_console_sync_control_room_helper_refactor_does_not_trigger_full_stack_release_gate():
    flags = _flags(
        "console/app/services/sync_control_room.py",
        "console/app/main.py",
        "tests/test_sync_control_room.py",
    )

    assert flags["python_runtime"] is True
    assert flags["console_tests"] is True
    assert flags["root_tests"] is True
    assert flags["e2e"] is True
    assert flags["release_full_stack"] is False
    assert _root_targets(flags) == {
        "console/tests/test_agent_runner_scheduler_auth.py",
        "tests/test_sync_control_room.py",
    }


def test_sap_b1_hint_sources_select_the_hints_contract():
    for changed in (
        "cartridges/sap_b1/datasets/sap_b1_batch_expiry.sql",
        "cartridges/sap_b1/hints/assistant.md",
        "cartridges/sap_b1/app/config/indicators.yaml",
        "cartridges/sap_b1/app/config/connector.yaml",
        "cartridges/sap_b1/apps/sap_b1_margen.json",
        "cartridges/sap_b1/app/services/business_parameters_mapping.py",
        "console/app/services/studio_assistant.py",
        "console/app/services/agent_runtime.py",
        "console/app/services/seed_packaged_hints.py",
        "workspace/app/services/consumer_assistant.py",
        "mcp-infra/app/tools/control_room.py",
        "console-next/src/app/(shell)/studio/page.tsx",
        "console-next/src/lib/studio/sections.ts",
        "scripts/deploy_main_aws.py",
        "infra/terraform/deploy/docker-compose.cartridges.yml",
    ):
        assert "tests/test_sap_b1_hints.py" in _root_targets(_flags(changed)), changed

    dataset_only = _root_targets(_flags("cartridges/sap_b1/datasets/sap_b1_batch_expiry.sql"))
    assert dataset_only == {"tests/test_sap_b1_datasets.py", "tests/test_sap_b1_hints.py"}
    assert "tests/test_sap_b1_hints.py" not in _root_targets(
        _flags("cartridges/sap_b1/tests/test_indicators_catalog.py")
    )
