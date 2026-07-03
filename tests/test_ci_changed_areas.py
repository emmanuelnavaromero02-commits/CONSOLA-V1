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


def test_dependency_changes_trigger_security_without_full_stack_by_default():
    flags = _flags("console/requirements.txt", "tests-e2e/package-lock.json")

    assert flags["python_deps"] is True
    assert flags["node_deps"] is True
    assert flags["release_full_stack"] is False


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
    assert flags["root_test_targets"] == "tests/test_status_pages.py"


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
    assert flags["root_test_targets"] == "tests/test_security_headers.py"


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
    assert flags["root_test_targets"] == "tests/test_request_rate_limits.py"


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
    assert flags["root_test_targets"] == "tests/test_mcp_payloads.py"


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
    assert flags["root_test_targets"] == "tests/test_db_pool.py"


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
    assert flags["root_test_targets"] == "tests/test_startup_readiness.py"
