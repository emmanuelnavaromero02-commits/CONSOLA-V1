from __future__ import annotations

import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/control-room-postgres-rls.yml"


def test_operational_truth_e2e_is_required_by_canonical_gate() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    e2e = jobs["operational-truth-e2e"]
    gate = jobs["control-room-gate"]

    assert e2e["needs"] == "changes"
    assert e2e["if"] == "needs.changes.outputs.control_room == 'true'"
    assert e2e["timeout-minutes"] == 35
    assert any(
        step.get("run") == "scripts/run_operational_truth_e2e.sh"
        for step in e2e["steps"]
    )
    assert "operational-truth-e2e" in gate["needs"]
    gate_env = gate["steps"][0]["env"]
    assert gate_env["OPERATIONAL_TRUTH_E2E_RESULT"] == (
        "${{ needs['operational-truth-e2e'].result }}"
    )


def test_e2e_runner_does_not_install_duckdb_extensions_at_runtime() -> None:
    runtime_files = [
        ROOT / "infra/e2e/compose.apps.yml",
        ROOT / "infra/e2e/compose.test.yml",
        ROOT / "scripts/run_operational_truth_e2e.sh",
    ]
    for path in runtime_files:
        assert "INSTALL httpfs" not in path.read_text(encoding="utf-8")
        assert "INSTALL postgres" not in path.read_text(encoding="utf-8")


def test_e2e_linux_dag_bundle_is_readable_by_the_non_root_airflow_user() -> None:
    script = (ROOT / "scripts/run_operational_truth_e2e.sh").read_text(encoding="utf-8")
    compose = (ROOT / "infra/e2e/compose.airflow.yml").read_text(encoding="utf-8")
    required = {
        "file_ingest.py",
        "dataset_refresh_chain.py",
        "dataset_refresh_graph.py",
        "dataset_refresh_finalization.py",
        "dataset_refresh_idempotency.py",
        "dataset_refresh_materialize.py",
        "dataset_refresh_outcome.py",
        "runtime_security_context.py",
    }

    assert 'chmod 0755 "$dag_dir"' in script
    assert 'chmod 0777 "$dag_dir"' not in script
    assert 'install -m 0444 "$task_root/airflow/dags/$module"' in script
    assert required <= {name for name in required if name in script}
    assert "${OMEGA_E2E_DAGS_DIR:?required}:/opt/airflow/dags:ro" in compose
    assert 'exec -T airflow-scheduler python - "${dag_modules[@]}"' in script
    assert "os.geteuid() == 0" in script
    assert "not path.is_file() or not os.access(path, os.R_OK)" in script


def test_e2e_orchestrator_explicitly_exercises_pre_xcom_failure() -> None:
    path = ROOT / "tests/operational_truth_e2e/test_runtime_pipeline.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    tests = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]

    assert len(tests) == 1
    assert "def _assert_pre_xcom_failure" in source
    assert source.count("_assert_pre_xcom_failure(") == 2
    assert "task_instance_states" in source
    assert "pipeline_status" in source
    assert "intelligence_count" in source


def test_e2e_junit_floor_matches_the_single_real_orchestrator() -> None:
    script = (ROOT / "scripts/run_operational_truth_e2e.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infra/e2e/Dockerfile").read_text(encoding="utf-8")

    assert "--junitxml=/tmp/operational-truth-e2e.xml" in dockerfile
    assert 'counts != {"tests": 1, "skipped": 0, "failures": 0, "errors": 0}' in script
