from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CONTROLLER = REPO / "scripts/gcp_release.py"
REMOTE = REPO / "scripts/gcp/reconcile-pipeline-runs.sh"


def _load_controller():
    spec = importlib.util.spec_from_file_location("gcp_release_reconcile", CONTROLLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(*, count: int = 2) -> dict:
    observed = datetime.now(timezone.utc) - timedelta(minutes=10)
    started = observed - timedelta(hours=2)
    rows = []
    for index in range(count):
        rows.append(
            {
                "run_id": f"stale-run-{index}",
                "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
                "workspace_id": f"00000000-0000-4000-8000-{index:012d}",
                "expected_status": "running",
                "expected_started_at": started.isoformat(),
                "expected_fencing_token": index,
                "target_status": "failed" if index % 2 == 0 else "blocked",
                "reason": "stale_orphan",
                "evidence": {
                    "airflow_state": "not_found",
                    "observed_at": observed.isoformat(),
                    "orphan_confirmed": True,
                },
            }
        )
    return {
        "schema": "omega.pipeline-run-reconciliation/v1",
        "change_id": "checkpoint-test-stale-runs",
        "runs": rows,
    }


def _raw(payload: dict) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()


def test_strict_reconciliation_manifest_accepts_exact_generic_contract() -> None:
    module = _load_controller()
    payload = _manifest()
    assert (
        module.validate_pipeline_run_reconciliation_manifest(
            _raw(payload), expected_count=2
        )
        == payload
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value.update(schema="omega.pipeline-run-reconciliation/v2"),
            "schema",
        ),
        (lambda value: value["runs"][0].update(expected_status="queued"), "running"),
        (lambda value: value["runs"][0].update(target_status="success"), "terminal"),
        (lambda value: value["runs"][0].update(tenant_id="NOT-A-UUID"), "tenant_id"),
        (
            lambda value: value["runs"][1].update(run_id=value["runs"][0]["run_id"]),
            "repeats",
        ),
        (
            lambda value: value["runs"][0]["evidence"].update(
                access_token="must-not-enter-evidence"
            ),
            "credential",
        ),
        (lambda value: value["runs"][0].update(unreviewed=True), "shape"),
    ],
)
def test_strict_reconciliation_manifest_rejects_drift(mutation, message: str) -> None:
    module = _load_controller()
    payload = _manifest()
    mutation(payload)
    with pytest.raises(ValueError, match=message):
        module.validate_pipeline_run_reconciliation_manifest(
            _raw(payload), expected_count=2
        )


def test_strict_reconciliation_manifest_rejects_count_and_duplicate_json_key() -> None:
    module = _load_controller()
    with pytest.raises(ValueError, match="count"):
        module.validate_pipeline_run_reconciliation_manifest(
            _raw(_manifest()), expected_count=17
        )
    duplicate = (
        b'{"schema":"omega.pipeline-run-reconciliation/v1",'
        b'"change_id":"one-change","change_id":"second-change","runs":[]}'
    )
    with pytest.raises(ValueError, match="malformed"):
        module.validate_pipeline_run_reconciliation_manifest(
            duplicate, expected_count=1
        )


def test_external_manifest_must_be_private_single_link_and_outside_repo(
    tmp_path: Path,
) -> None:
    module = _load_controller()
    path = tmp_path / "reconciliation.json"
    path.write_bytes(_raw(_manifest(count=1)))
    path.chmod(0o600)
    raw, payload = module.read_private_reconciliation_manifest(path, expected_count=1)
    assert raw == path.read_bytes()
    assert len(payload["runs"]) == 1

    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode-0600"):
        module.read_private_reconciliation_manifest(path, expected_count=1)
    path.chmod(0o600)
    linked = tmp_path / "linked.json"
    os.link(path, linked)
    with pytest.raises(ValueError, match="single-link"):
        module.read_private_reconciliation_manifest(path, expected_count=1)

    inside = REPO / ".pipeline-run-reconciliation-test.json"
    try:
        inside.write_bytes(_raw(_manifest(count=1)))
        inside.chmod(0o600)
        with pytest.raises(ValueError, match="outside the repo"):
            module.read_private_reconciliation_manifest(inside, expected_count=1)
    finally:
        inside.unlink(missing_ok=True)


def test_remote_reconciliation_uses_only_the_canonical_engine_under_fence() -> None:
    result = subprocess.run(
        ["bash", "-n", str(REMOTE)], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    script = REMOTE.read_text(encoding="utf-8")
    assert "omega.pipeline-run-reconciliation/v1" in script
    assert 'gcs-download --uri "$MANIFEST_URI"' in script
    assert 'gcs-download --uri "$BACKUP_MANIFEST_URI"' in script
    assert 'gcs-download --uri "$ROUTING_ATTESTATION_URI"' in script
    assert '"$#" -ne 26' in script
    assert "external-routing-scheduler-attestations" in script
    assert 'attested.get("state") != "ready"' in script
    assert "reconcile_pipeline_runs.py" in script
    assert "OMEGA_PIPELINE_RECONCILIATION_ALLOW_APPLY=1" in script
    assert "OMEGA_PIPELINE_RECONCILIATION_MANIFEST_SHA256" in script
    assert "run_engine dry-run" in script
    assert "run_engine apply" in script
    assert "validate_engine_output" in script
    assert "would_apply dry-run" in script
    assert "already_applied dry-run" in script
    assert "--entrypoint python" in script
    assert "--read-only --cap-drop ALL --security-opt no-new-privileges" in script
    assert "--pids-limit 64" in script
    assert "PUBLISHED_CONSOLE_IMAGE" in script
    assert "--pull never" in script
    assert 'METADATA_FIREWALL" verify-container' in script
    assert "UPDATE pipeline_runs" not in script
    assert "INSERT INTO audit_events" not in script
    assert "BEGIN;" not in script and "COMMIT;" not in script
    assert "omega_release_reconciliation" not in script
    assert "DELETE FROM PIPELINE_RUNS" not in script.upper()

    lease_writer = (REPO / "airflow/dags/dataset_refresh_idempotency.py").read_text(
        encoding="utf-8"
    )
    finish = lease_writer[lease_writer.index("def finish_materialization") :]
    assert "AND status = 'running'" in finish
    assert "AND fencing_token = %s" in finish
    assert "AND lease_expires_at > clock_timestamp()" in finish


def test_remote_reconciliation_is_published_release_gated_before_mutation() -> None:
    script = REMOTE.read_text(encoding="utf-8")
    marker = script.index('"state": "fencing"')
    stop = script.index('"${COMPOSE[@]}" stop --timeout 60')
    blocked = script.index('emit "checkpoint zero-AWS writer gate" "BLOCKED"')
    published = script.index(
        "release-published-${PUBLISHED_RELEASE_TAG}-${HELPER_REF}-by-${HELPER_REF}"
    )
    final_fence = script.rindex('writer-fence --compose-project "$COMPOSE_PROJECT"')
    poststate = script.index('emit "canonical poststate"')
    handoff = script.index('payload["state"] = "handoff-ready"')
    assert blocked < published < marker < stop < final_fence < poststate < handoff
    applying = script.index('payload["state"] = "applying"')
    apply = script.index('run_engine apply "${WORKDIR}/apply.json"')
    assert script.index("APPLY_STARTED=1") < applying < apply
    assert '"$APPLY_STARTED" != "1"' in script
    assert "attested_at > now" in script
    assert "now - attested_at > timedelta(minutes=30)" in script
    assert '"$WATCHDOG" arm "$$" "$OPERATION_MARKER"' in script
    assert '"$WATCHDOG" arm-hold "$HANDOFF_TIMEOUT_SECONDS"' in script
    assert "wait_for_exact_restore" in script
    assert 'payload.get("target_kind") != "published"' in script
    assert 'payload.get("target_tag") != sys.argv[5]' in script
    assert 'payload.get("image_authority_mode") != "published"' in script
    assert 'authority.get("manifest_digest") != sys.argv[6]' in script
    assert 'authority.get("tag_object_sha") != sys.argv[7]' in script
    assert '"authority_sha256": sys.argv[19]' in script
    assert 'payload.get("target_ref") != sys.argv[4]' in script
    assert "set(assignments) != set(expected_images)" in script
    assert (
        'payload.get("lock_sha256") != hashlib.sha256(lock_raw).hexdigest()' in script
    )
    assert "stat.S_IMODE(lock_info.st_mode) != 0o400" in script
    assert "console:{re.escape(sys.argv[2])}@sha256" in script
    success_tail = script[poststate:]
    assert '"${COMPOSE[@]}" start' not in success_tail
    assert '"checkpoint_zero_aws_writer_gate":"PASS"' in script
    assert '"pre_release_runtime_restart_attempted": False' in script


def test_day2_adopts_only_published_release_handoff_without_old_runtime_restart() -> (
    None
):
    deploy = (REPO / "scripts/gcp/day2-release.sh").read_text(encoding="utf-8")
    assert '[[ -e "$OPERATION_MARKER" || -L "$OPERATION_MARKER" ]]' in deploy
    assert 'marker.get("state") != "handoff-ready"' in deploy
    assert 'marker.get("published_release_tag") != sys.argv[4]' in deploy
    assert 'receipt.get("published_release_tag") != sys.argv[4]' in deploy
    assert 'receipt.get("pre_release_runtime_restart_attempted") is not False' in deploy
    assert "RECONCILIATION_HANDOFF=1" in deploy
    assert "OMEGA_GCP_ALLOW_OPERATION_MARKER" in deploy
    assert "without restarting the old runtime" in deploy
    assert '"${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}"' not in deploy

    host_policy = deploy.index('"$CANDIDATE_FIREWALL" install')
    candidate_start = deploy.index(
        '"${COMPOSE[@]}" up -d --no-build --pull never "${SERVICES_WITHOUT_SCHEDULER[@]}"'
    )
    container_proof = deploy.index('"$CANDIDATE_FIREWALL" verify-container')
    assert host_policy < candidate_start < container_proof


def test_controller_upload_and_remote_execution_are_exactly_backup_bound() -> None:
    controller = CONTROLLER.read_text(encoding="utf-8")
    upload = controller[
        controller.index(
            "def upload_reconciliation_manifest_immutable"
        ) : controller.index("def remote_script")
    ]
    assert "pipeline-run-reconciliations/{change_id}/" in upload
    assert "--if-generation-match=0" in upload
    assert "omega-reconciliation-sha256" in upload
    assert 'f"{uri}#{generation}"' in upload

    command = controller[
        controller.index("def command_reconcile_pipeline_runs") : controller.index(
            "def command_deploy"
        )
    ]
    assert command.index("validate_live_canonical_target") < command.index(
        "upload_reconciliation_manifest_immutable"
    )
    assert command.index("validate_backup_bucket_controls") < command.index(
        "upload_reconciliation_manifest_immutable"
    )
    assert command.index("validate_transfer_fence") < command.index(
        "upload_reconciliation_manifest_immutable"
    )
    assert '"scripts/gcp/reconcile-pipeline-runs.sh"' in command
    assert '"reconcile_pipeline_runs.py"' in command
    assert '"runtime_contract.py"' in command
    assert '"operation-watchdog.sh"' in command
    assert '"metadata-firewall.sh"' in command
    assert "require_zero_aws_writer_gate" in command
    assert "routing_attestation.uri" in command
    assert "args.backup_manifest_generation" in command
    assert "args.backup_manifest_size_bytes" in command
    assert "args.backup_manifest_sha256" in command
    assert "args.release_tag" in command
    assert "args.handoff_timeout_seconds" in command


def test_make_and_runbook_expose_backup_then_reconciliation_gate() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    runbook = (REPO / "docs/runbook/16_gcp_canonical_day2_release.md").read_text(
        encoding="utf-8"
    )
    assert "reconcile-gcp-pipeline-runs:" in makefile
    assert "scripts/gcp_release.py reconcile-pipeline-runs" in makefile
    assert runbook.index("## 3. Fresh writer-fenced pre-deploy backup") < runbook.index(
        "## 4. Atomic stale pipeline-run reconciliation"
    )
    assert "GCP_PIPELINE_RECONCILIATION_EXPECTED_COUNT='17'" in runbook
    assert "Any missing, active, or changed row" in runbook
    assert "transaction" in runbook
    assert "There is no `DELETE`" in runbook
