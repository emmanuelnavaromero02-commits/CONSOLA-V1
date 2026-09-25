from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import subprocess
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/control-room-postgres-rls.yml"
PACKAGE_SCRIPT = ROOT / "scripts/package_operational_truth_evidence.sh"


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


def test_minio_init_verifies_versioning_without_missing_image_utilities() -> None:
    compose = (ROOT / "infra/e2e/compose.infrastructure.yml").read_text(
        encoding="utf-8"
    )
    minio_init = compose.split("  minio-init:", 1)[1].split("\nvolumes:", 1)[0]

    assert "mc version enable local/lakehouse" in minio_init
    assert 'version_info="$$(mc version info local/lakehouse)"' in minio_init
    assert 'case "$$version_info" in *Enabled*|*enabled*)' in minio_init
    assert "grep" not in minio_init


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
        "service_job_client.py",
    }

    assert 'chmod 0755 "$dag_dir"' in script
    assert 'chmod 0777 "$dag_dir"' not in script
    assert 'install -m 0444 "$task_root/airflow/dags/$module"' in script
    assert required <= {name for name in required if name in script}
    assert "${OMEGA_E2E_DAGS_DIR:?required}:/opt/airflow/dags:ro" in compose
    assert 'exec -T airflow-scheduler python - "${dag_modules[@]}"' in script
    assert "os.geteuid() == 0" in script
    assert "not path.is_file() or not os.access(path, os.R_OK)" in script


def test_e2e_verifier_secret_uses_a_mode_preserving_mount_and_is_removed() -> None:
    script = (ROOT / "scripts/run_operational_truth_e2e.sh").read_text(encoding="utf-8")

    assert (
        'verifier_secret="$(mktemp "$task_root/.omega-ot-verifier.XXXXXX")"' in script
    )
    assert 'chmod 0600 "$verifier_secret"' in script
    assert 'rm -f "$verifier_secret"' in script


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


def test_e2e_evidence_upload_uses_only_a_verified_portable_archive() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    e2e = workflow.split("operational-truth-e2e:", 1)[1].split(
        "no-control-room-needed:", 1
    )[0]

    assert "scripts/package_operational_truth_evidence.sh" in e2e
    assert e2e.count("if: always()") == 2
    assert "if-no-files-found: error" in e2e
    assert "/tmp/operational-truth-e2e-evidence.tar.gz" in e2e
    assert "/tmp/operational-truth-e2e-evidence.tar.gz.sha256" in e2e
    upload = e2e.split("uses: actions/upload-artifact@v4", 1)[1]
    assert "path: /tmp/operational-truth-e2e\n" not in upload


def test_e2e_evidence_packager_preserves_forensic_paths_and_is_fail_closed(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "raw-evidence"
    forensic = evidence / "airflow-logs" / "run_id=manual__00:01:02+00:00"
    forensic.mkdir(parents=True)
    (forensic / "attempt=1.log").write_text("forensic-log\n", encoding="utf-8")
    archive = tmp_path / "portable-evidence.tar.gz"

    subprocess.run(
        ["bash", str(PACKAGE_SCRIPT), str(evidence), str(archive)],
        check=True,
    )

    assert archive.is_file() and archive.stat().st_size > 0
    checksum = archive.with_name(f"{archive.name}.sha256")
    assert checksum.is_file() and checksum.stat().st_size > 0
    checksum_text = checksum.read_text(encoding="utf-8")
    assert str(tmp_path) not in checksum_text
    assert checksum_text.split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()
    listing = subprocess.run(
        ["tar", "-tzf", str(archive)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "run_id=manual__00:01:02+00:00/attempt=1.log" in listing
    with tarfile.open(archive, "r:gz") as bundle:
        member = next(
            item for item in bundle.getmembers() if item.name.endswith("/attempt=1.log")
        )
        extracted = bundle.extractfile(member)
        assert extracted is not None and extracted.read() == b"forensic-log\n"

    missing = subprocess.run(
        ["bash", str(PACKAGE_SCRIPT), str(tmp_path / "missing"), str(archive)],
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0


def test_the_e2e_stack_provides_every_per_pair_key_console_requires_to_start():
    import re

    root = Path(__file__).resolve().parents[1]
    auth = (root / "console/app/services/auth.py").read_text(encoding="utf-8")
    required = sorted(set(re.findall(r"\"(INTERNAL_API_KEY_[A-Z0-9_]+_TO_CONSOLE)\"", auth)))
    assert "INTERNAL_API_KEY_SAP_B1_TO_CONSOLE" in required
    script = (root / "scripts/run_operational_truth_e2e.sh").read_text(encoding="utf-8")
    compose = (root / "infra/e2e/compose.apps.yml").read_text(encoding="utf-8")
    console_env = compose.split("  console:", 1)[1].split("\n  e2e-test:", 1)[0]
    missing_script = [name for name in required if name not in script]
    missing_compose = [name for name in required if f"{name}: ${{{name}:?required}}" not in console_env]
    assert not missing_script, f"not generated by run_operational_truth_e2e.sh: {missing_script}"
    assert not missing_compose, f"not passed to console in infra/e2e/compose.apps.yml: {missing_compose}"

