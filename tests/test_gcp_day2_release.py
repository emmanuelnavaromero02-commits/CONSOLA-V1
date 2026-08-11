from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from base64 import b64encode
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
GCP_SHELL_DIR = REPO / "scripts" / "gcp"
GCP_COMPOSE_DIR = REPO / "infra" / "terraform-gcp" / "release"


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def _load_module():
    path = REPO / "scripts/gcp_release.py"
    spec = importlib.util.spec_from_file_location("gcp_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gcp_release_overlay_covers_exactly_15_proprietary_images() -> None:
    overlay = _read("infra/terraform-gcp/release/docker-compose.release.yml")
    lock_names = set(re.findall(r"image: \$\{(OMEGA_GCP_IMAGE_[A-Z0-9_]+):", overlay))
    assert len(lock_names) == 15
    assert "latest" not in overlay
    assert overlay.count("OMEGA_GCP_IMAGE_AIRFLOW") == 3
    assert overlay.count("build: !reset null") == 17
    assert overlay.count("pull_policy: never") == 17


def test_gcp_release_overlay_resolves_with_base_compose() -> None:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(REPO / "infra/.env.example"),
        "-f",
        str(REPO / "infra/docker-compose.yml"),
        "-f",
        str(GCP_COMPOSE_DIR / "docker-compose.release.yml"),
        "--profile",
        "sap",
        "config",
        "--images",
    ]
    env = os.environ.copy()
    repositories = {
        "AIRFLOW": "airflow",
        "BANXICO": "banxico",
        "CONSOLE": "console",
        "HUBSPOT": "hubspot",
        "INEGI": "inegi",
        "MCP_INFRA": "mcp-infra",
        "REFINEMENT": "refinement",
        "REPLICON": "replicon",
        "SALESFORCE": "salesforce",
        "SAP_HCM": "sap_hcm",
        "SAP_S4HANA": "sap_s4hana",
        "SAP_SUCCESSFACTORS": "sap_successfactors",
        "SEC_EDGAR": "sec_edgar",
        "VAULT": "vault",
        "WORKSPACE": "workspace",
    }
    digest = "a" * 64
    env.update(
        {
            f"OMEGA_GCP_IMAGE_{key}": (
                f"ghcr.io/example-owner/{repository}:v1.45.207-beta@sha256:{digest}"
            )
            for key, repository in repositories.items()
        }
    )
    result = subprocess.run(
        command, text=True, capture_output=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    ghcr = {
        line
        for line in result.stdout.splitlines()
        if line.startswith("ghcr.io/example-owner/")
    }
    assert len(ghcr) == 15
    assert all(line.endswith(f"@sha256:{digest}") for line in ghcr)


@pytest.mark.parametrize(
    "script",
    [
        "day2-release.sh",
        "backup.sh",
        "restore-rehearsal.sh",
        "reboot-runtime.sh",
        "image-preflight.sh",
        "operation-watchdog.sh",
    ],
)
def test_gcp_day2_shell_is_syntax_valid(script: str) -> None:
    result = subprocess.run(
        ["bash", "-n", str(GCP_SHELL_DIR / script)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_backup_operation_state_python_helper_compiles_and_runs(
    tmp_path: Path,
) -> None:
    backup = _read("scripts/gcp/backup.sh")
    match = re.search(
        r"""python3 - \"\$OPERATION_MARKER\" \"\$state\" \"\$BACKUP_ID\" \"\$CURRENT_REF\" \\
    \"\$CANDIDATE_REF\" \"\$OPERATION_MODE\" <<'PY'\n(.*?)\nPY""",
        backup,
        re.DOTALL,
    )
    assert match, "write_operation_state Python heredoc is missing"
    marker = tmp_path / "operation-state.json"
    result = subprocess.run(
        [
            sys.executable,
            "-",
            str(marker),
            "fencing",
            "20260811T120000Z-v1.45.207-beta",
            "a" * 40,
            "b" * 40,
            "backup",
        ],
        input=match.group(1),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(marker.read_text(encoding="utf-8"))
    updated_at = payload.pop("updated_at")
    assert updated_at.endswith("+00:00")
    assert payload == {
        "backup_id": "20260811T120000Z-v1.45.207-beta",
        "deploy_ref": "a" * 40,
        "operation": "backup",
        "schema_version": 1,
        "state": "fencing",
    }
    assert marker.stat().st_mode & 0o777 == 0o600


def test_permanent_operation_gate_publication_is_crash_safe() -> None:
    bootstrap = _read("scripts/gcp/bootstrap-runtime.sh")
    bootstrap_publish = bootstrap[
        bootstrap.index("GUARD_TMP=") : bootstrap.index(
            "# A SIGKILL or reboot during backup/deploy"
        )
    ]
    assert "fsync-file" in bootstrap_publish
    assert bootstrap_publish.index(
        'mv -Tf "${DROPIN_TMP}" /etc/systemd/system/docker.service.d/omega-operation-gate.conf'
    ) < bootstrap_publish.index(
        'mv -Tf "${GUARD_TMP}" /usr/local/sbin/omega-operation-gate'
    )
    assert "> /etc/systemd/system/docker.service.d/omega-operation-gate.conf" not in (
        bootstrap_publish
    )

    for relative in ("scripts/gcp/backup.sh", "scripts/gcp/day2-release.sh"):
        script = _read(relative)
        publish = script[
            script.index("install_operation_guard()") : script.index(
                "write_operation_state()"
            )
        ]
        assert "fsync-file" in publish
        assert publish.index(
            'mv -Tf "$dropin_tmp" /etc/systemd/system/docker.service.d/omega-operation-gate.conf'
        ) < publish.index(
            'mv -Tf "$guard_tmp" /usr/local/sbin/omega-operation-gate'
        )
        assert "> /etc/systemd/system/docker.service.d/omega-operation-gate.conf" not in (
            publish
        )


def test_state_bundle_cleanup_is_disarmed_at_the_atomic_commit_point() -> None:
    backup = _read("scripts/gcp/backup.sh")
    backup_publish = backup[backup.index('mv -Tf "$STATE_PREVIEW" "$STATE_LINK"') :]
    assert backup_publish.index('STATE_FINAL=""') < backup_publish.index(
        '"$SAFE_IO" fsync-dir "$SHARED_ROOT"'
    )

    deploy = _read("scripts/gcp/day2-release.sh")
    deploy_publish = deploy[deploy.index('mv -Tf "$STATE_PREVIEW" "$STATE_LINK"') :]
    assert deploy_publish.index('STATE_FINAL=""') < deploy_publish.index(
        '"$SAFE_IO" fsync-dir "$APP_ROOT" "$SHARED_ROOT"'
    )


def test_failed_restore_or_deploy_reestablishes_verified_writer_fence() -> None:
    backup = _read("scripts/gcp/backup.sh")
    backup_recovery = backup[
        backup.index("ensure_fail_closed_runtime()") : backup.index(
            "restore_runtime()"
        )
    ]
    assert 'write_operation_state "restore-failed"' in backup_recovery
    assert 'stop --timeout 30 "${WRITER_SERVICES[@]}"' in backup_recovery
    assert "writer-fence" in backup_recovery
    assert "database_fence on" in backup_recovery
    assert '"FAIL" "manual recovery required' in backup_recovery

    deploy = _read("scripts/gcp/day2-release.sh")
    cleanup = deploy[deploy.index("cleanup()") : deploy.index("fail()")]
    assert 'write_operation_state "failed"' in cleanup
    assert 'stop --timeout 30 "${MUTATING_SERVICES[@]}"' in cleanup
    assert "writer-fence" in cleanup
    assert "database_fence on" in cleanup
    assert "|| true" not in cleanup
    assert '"FAIL" "manual recovery required' in cleanup

    for script in (backup, deploy):
        match = re.search(r"database_fence\(\) \{\n(.*?)\n\}", script, re.DOTALL)
        assert match
        fence = match.group(1)
        assert 'local rc=0' in fence
        assert 'return "$rc"' in fence


def test_independent_watchdog_covers_every_live_writer_mutation_window() -> None:
    watchdog = _read("scripts/gcp/operation-watchdog.sh")
    assert "systemd-run --quiet --collect" in watchdog
    assert "--property=Restart=on-failure" in watchdog
    assert "--property=StartLimitIntervalSec=0" in watchdog
    assert "database_fence_best_effort" in watchdog
    assert "docker stop --time 30" in watchdog
    assert "systemctl mask --runtime" in watchdog
    assert "containerd-shim-runc-v2.*-namespace moby" in watchdog

    backup = _read("scripts/gcp/backup.sh")
    assert backup.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"') < backup.index(
        '"${COMPOSE[@]}" stop --timeout 60'
    )
    deploy = _read("scripts/gcp/day2-release.sh")
    assert deploy.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"') < deploy.index(
        '"${COMPOSE[@]}" stop --timeout 60'
    )
    bootstrap = _read("scripts/gcp/bootstrap-runtime.sh")
    assert bootstrap.index(
        '"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}"'
    ) < bootstrap.index('"${COMPOSE[@]}" up --build -d')
    for script in (backup, deploy, bootstrap):
        assert "operation-watchdog.sh" in script
        arm = re.search(r'(?m)^.*WATCHDOG[^\n]* arm "\$\$"', script)
        assert arm
        disarm = re.search(
            r'(?m)^.*WATCHDOG[^\n]* disarm "\$\$"', script[arm.end() :]
        )
        assert disarm


def test_candidate_gate_is_verified_before_permanent_replacement() -> None:
    deploy = _read("scripts/gcp/day2-release.sh")
    assert deploy.index('if ! "$CANDIDATE_OPERATION_GUARD"') < deploy.index(
        'install_operation_guard "$CANDIDATE_OPERATION_GUARD"'
    )
    backup = _read("scripts/gcp/backup.sh")
    assert backup.index('if ! "$CANDIDATE_GUARD"') < backup.index(
        'install_operation_guard "$CANDIDATE_GUARD"'
    )


def test_image_lock_files_and_directory_are_durable_before_publication() -> None:
    deploy = _read("scripts/gcp/day2-release.sh")
    lock = deploy[deploy.index('chmod 0400 "$NEW_LOCK_ENV"') :]
    file_sync = lock.index('fsync-file "$NEW_LOCK_ENV" "$NEW_LOCK_MANIFEST"')
    directory_sync = lock.index('fsync-dir "$LOCK_TMP"')
    publish = lock.index('mv "$LOCK_TMP" "$LOCK_DIR"')
    assert file_sync < directory_sync < publish


def test_reused_release_lock_and_evidence_reject_path_and_mode_drift() -> None:
    deploy = _read("scripts/gcp/day2-release.sh")

    managed = deploy[deploy.index("validate_managed_directory()") :]
    assert "path.resolve(strict=True)" in managed
    assert "info.st_uid != 0" in managed
    assert "info.st_gid != 0" in managed
    assert "stat.S_IMODE(info.st_mode) & 0o022" in managed

    release = deploy[deploy.index('RELEASE_MARKER="${RELEASE_DIR}/.omega-release.json"') :]
    release = release[: release.index('ACTUAL_VERSION=')]
    assert '[[ -e "$RELEASE_DIR" || -L "$RELEASE_DIR" ]]' in release
    assert "--exclude .omega-release.json --require-read-only" in release
    assert "not stat.S_ISREG(info.st_mode)" in release
    assert "stat.S_IMODE(info.st_mode) != 0o400" in release
    assert "set(payload) != expected_keys" in release
    assert '"tree_sha256": sys.argv[10]' in release
    assert release.index('chmod -R a-w "$RELEASE_TMP"') < release.index(
        'TREE_SHA256="$($SAFE_IO tree-sha256 --root "$RELEASE_TMP" --require-read-only)"'
    )

    lock = deploy[deploy.index('NEW_LOCK_TREE_SHA256=') : deploy.index("COMPOSE=(")]
    assert '[[ -e "$LOCK_DIR" || -L "$LOCK_DIR" ]]' in lock
    assert 'tree-sha256 --root "$LOCK_DIR"' in lock
    assert '"$EXISTING_LOCK_TREE_SHA256" != "$NEW_LOCK_TREE_SHA256"' in lock
    assert '"$LOCK_TREE_SHA256" != "$NEW_LOCK_TREE_SHA256"' in lock

    evidence = deploy[deploy.index('DEPLOYMENT_PROVENANCE_STAGE=') :]
    evidence = evidence[: evidence.index('write_operation_state "validated"')]
    assert '--write-provenance "$DEPLOYMENT_PROVENANCE_STAGE"' in evidence
    assert "not stat.S_ISREG(info.st_mode)" in evidence
    assert "stat.S_IMODE(info.st_mode) != mode" in evidence
    assert "path.resolve(strict=True)" in evidence
    assert "os.path.lexists(destination)" in evidence
    assert "destination.read_bytes() != staged_bytes" in evidence
    assert "os.replace(temporary, destination)" in evidence


def test_deploy_fails_closed_and_promotes_only_after_all_gates() -> None:
    deploy = _read("scripts/gcp/day2-release.sh")
    assert "ifGenerationMatch" not in deploy  # artifact upload is operator-side
    assert "artifact checksum" in deploy
    assert "backup source commit does not match current release" in deploy
    assert (
        "release does not contain the audited auth, preflight, and runtime helpers"
        in deploy
    )
    assert "registry credential found in runtime env" in deploy
    assert "host-owned env and generated GCP overlay are canonical" in deploy
    assert 'CURRENT_ENV="${OLD_RELEASE}/infra/.env"' not in deploy
    assert '"${OLD_RELEASE}/infra/docker-compose.gcp.yml"' not in deploy
    assert "OMEGA_GCP_GHCR_AUTH_RUNNER" in deploy
    assert 'OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION"' in deploy
    assert "pull.log" not in deploy
    assert "15/15 tag=${TARGET_TAG}" in deploy
    assert '"schema_version": 2' in deploy
    assert '"service_binding_count": len(service_images)' in deploy
    assert (
        "--no-build --pull never --no-deps --force-recreate postgres postgres_gold"
        in deploy
    )
    assert "named volume identity changed" in deploy
    assert "scripts/apply_db_migrations.sh" in deploy
    assert "/readyz?require_data=1" in deploy
    assert "--scheduler stopped --one-shots" in deploy
    assert "--scheduler required --one-shots" in deploy
    assert (
        "running=22 healthy=22 scheduler=1 analytics=healthy exact_lock=true"
        in deploy
    )
    assert "manual recovery required; one or more stop/fence/marker checks failed" in deploy
    assert 'write_operation_state "fencing"' in deploy
    assert 'write_operation_state "fenced"' in deploy
    assert 'write_operation_state "migrated"' in deploy
    assert 'write_operation_state "validated"' in deploy
    assert 'write_operation_state "promoted"' in deploy
    assert "down -v" not in deploy
    assert "docker compose down" not in deploy

    pull = deploy.index("private GHCR release pull")
    fence = deploy.index('"${COMPOSE[@]}" stop --timeout 60')
    migrate = deploy.index("scripts/apply_db_migrations.sh")
    readiness = deploy.index("candidate data readiness")
    exact_runtime = deploy.index("exact pre-scheduler runtime")
    scheduler = deploy.index("up -d --no-build --pull never airflow-scheduler")
    promotion = deploy.index("atomic current promotion")
    marker = deploy.index('write_operation_state "fencing"')
    assert (
        pull
        < marker
        < fence
        < migrate
        < readiness
        < exact_runtime
        < scheduler
        < promotion
    )


def test_backup_is_writer_fenced_versioned_and_immutable() -> None:
    backup = _read("scripts/gcp/backup.sh")
    assert "expected one scheduler" in backup
    assert "registry credential found in runtime env" in backup
    assert "all global application writers stopped" in backup
    assert "pg_dumpall" in backup
    assert 'get("versioning", {}).get("enabled") is not True' in backup
    assert "object-generation" not in backup
    assert "A second generation listing" in backup
    assert '"ifGenerationMatch": "0"' in backup
    assert '"generation": str(item["generation"])' in backup
    assert '"crc32c": item.get("crc32c")' in backup
    assert '"md5": item.get("md5Hash")' in backup
    assert '"plaintext_runtime_secrets_included": False' in backup
    assert '"database_contents_sensitive": True' in backup
    assert "runtime restored after backup" in backup
    assert "candidate helper artifact" in backup
    assert "legacy runtime adoption" in backup
    assert "live release coherence" in backup
    assert '"runtime_provenance_sha256"' in backup
    assert '"repo_digest"' in backup
    assert '"image_id"' in backup
    assert "Authorization: Bearer ${token}" not in backup
    assert 'headers={"Authorization": f"Bearer {token}"}' in _read(
        "scripts/gcp/safe_io.py"
    )
    assert 'write_operation_state "fencing"' in backup
    assert 'write_operation_state "captured"' in backup
    assert 'write_operation_state "restored"' in backup
    assert 'rm -f -- "$OPERATION_MARKER"' in backup
    assert "all previously-active services running/healthy" in backup
    assert "down -v" not in backup

    staged = backup.index('--output "$ACTIVE_PROVENANCE"')
    health = backup.index("health/version or readyz/data gate differs from current release")
    provenance = backup.index('--provenance "$ACTIVE_PROVENANCE"')
    publication = backup.index('mv -Tf "$STATE_PREVIEW" "$STATE_LINK"')
    assert staged < health < provenance < publication
    assert "one-existing/one-missing direct state pair is corrupt" in backup
    assert "state_pair=atomic" in backup


def test_legacy_adoption_binds_host_inputs_and_keeps_cas_marker_until_readback() -> None:
    backup = _read("scripts/gcp/backup.sh")
    controller = _read("scripts/gcp_release.py")
    finalizer = _read("scripts/gcp/finalize-startup-adoption.sh")

    marker = backup.index('write_operation_state "metadata-cas-preparing"')
    env_backup = backup.index('ENV_BACKUP="${ENV_BACKUPS_ROOT}/${ENV_BEFORE_SHA256}.env"')
    env_publish = backup.index("env-set --path \"$SHARED_ENV\"")
    gcp_overlay = backup.index(
        'publish_private_file "$LIVE_GCP_RUNTIME_COMPOSE" "$GCP_RUNTIME_COMPOSE"'
    )
    legacy_overlay = backup.index(
        'publish_private_file "$LIVE_LEGACY_IMAGE_COMPOSE" "$LEGACY_IMAGE_COMPOSE"'
    )
    provenance = backup.index('bootstrap-record --compose-project "$COMPOSE_PROJECT"')
    pending = backup.index('write_operation_state "metadata-cas-pending"')
    hold = backup.index('"$WATCHDOG" arm-hold 3600 "$OPERATION_MARKER"')
    assert marker < env_backup < env_publish < gcp_overlay < legacy_overlay < provenance
    assert provenance < pending < hold
    for runtime_input in (
        "shared_env=${SHARED_ENV}",
        "base_compose=${BASE_COMPOSE}",
        "gcp_compose=${GCP_RUNTIME_COMPOSE}",
        "legacy_image_compose=${LEGACY_IMAGE_COMPOSE}",
    ):
        assert f'--runtime-input "{runtime_input}"' in backup
    assert "runtime_input_sha256" in _read("scripts/gcp/runtime_contract.py")

    cas = controller.index("before_used, after = replace_startup_metadata_cas(")
    finalize = controller.index('script=REMOTE_ROOT / "finalize-startup-adoption.sh"')
    success = controller.index(
        'print(json.dumps({"status": "PASS", "evidence_dir": str(evidence_dir), **evidence}))',
        finalize,
    )
    assert cas < finalize < success
    metadata_readback = finalizer.index("verify_live_startup")
    completion_record = finalizer.index('"state": "complete"', metadata_readback)
    marker_remove = finalizer.index('rm -f -- "$OPERATION_MARKER"', completion_record)
    assert metadata_readback < completion_record < marker_remove


def test_prebackup_data_readiness_exception_is_exact_and_sql_attested() -> None:
    backup = _read("scripts/gcp/backup.sh")
    assert 'current_ref == "6b12883c5b5ea0537120279ccbee4947137998a2"' in backup
    assert 'current_version == "1.45.205-beta"' in backup
    assert 'body != {"ok": False, "service": "console"}' in backup
    assert "exc.code != 503" in backup
    assert "direct operational DB attestation" in backup
    assert "direct Gold DB attestation" in backup
    assert "omega_publication.dataset_publication_heads" in backup
    assert "omega_publication.materialization_runs" in backup
    assert "OPERATIONAL_ITEMS + GOLD_ROWS + SILVER_ROWS" in backup


def test_backup_temporarily_holds_every_exact_gcs_generation_before_success() -> None:
    backup = _read("scripts/gcp/backup.sh")
    stable_inventory = backup.index(
        "GCS object generations changed while the backup manifest was captured"
    )
    patch_hold = backup.index('body = json.dumps({"temporaryHold": True}).encode()')
    patch_method = backup.index('method="PATCH"', patch_hold)
    exact_generation = backup.index('"generation": generation', stable_inventory)
    metadata_cas = backup.index(
        '"ifMetagenerationMatch": original_meta', stable_inventory
    )
    held_manifest = backup.index("executor.map(hold_generation, rows)", patch_method)
    final_readback = backup.index(
        "Re-read every exact live generation immediately before sealing"
    )
    manifest_success = backup.index('emit "retention-protected backup manifest"')

    assert backup.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"') < stable_inventory
    assert (
        stable_inventory < exact_generation < metadata_cas < patch_hold < patch_method
    )
    assert patch_method < held_manifest < final_readback < manifest_success
    assert 'actual.get("temporaryHold") is not True' in backup
    assert 'str(actual.get("metageneration", "")) != row["metageneration"]' in backup
    assert '"all_recorded_generations_held": True' in backup
    assert '"release_policy": "explicit-approved-release-only"' in backup
    assert '{"temporaryHold": False}' not in backup
    assert "ThreadPoolExecutor(max_workers=24)" in backup
    assert "executor.map(hold_generation, rows)" in backup


def test_restore_rehearsal_requires_live_hold_for_every_recorded_generation() -> None:
    rehearsal = _read("scripts/gcp/restore-rehearsal.sh")
    assert 'storage.get("generation_hold") != {' in rehearsal
    assert '"type": "temporaryHold"' in rehearsal
    assert 'or row.get("temporaryHold") is not True' in rehearsal
    assert '"ifMetagenerationMatch": row["metageneration"]' in rehearsal
    assert (
        '"fields": "name,size,generation,metageneration,md5Hash,crc32c,temporaryHold"'
        in rehearsal
    )
    assert 'actual.get("temporaryHold") is not True' in rehearsal
    assert '"verified_temporary_holds": len(verify_rows)' in rehearsal
    assert 'emit "held object-generation restore points" "PASS"' in rehearsal


def test_gcs_generation_hold_python_helpers_compile() -> None:
    backup = _read("scripts/gcp/backup.sh")
    inventory = re.search(
        r"python3 - \"\$LAKEHOUSE_BUCKET\" \"\$BACKUP_PREFIX\" .*? <<'PY'\n(.*?)\nPY",
        backup,
        re.DOTALL,
    )
    final_verify = re.search(
        r"HOLD_RESULT=\"\$\(python3 - \"\$LAKEHOUSE_BUCKET\" .*? <<'PY'\n(.*?)\nPY",
        backup,
        re.DOTALL,
    )
    rehearsal = _read("scripts/gcp/restore-rehearsal.sh")
    restore_verify = re.search(
        r"OBJECT_RESULT=\"\$\(python3 - .*? <<'PY'\n(.*?)\nPY",
        rehearsal,
        re.DOTALL,
    )
    for helper in (inventory, final_verify, restore_verify):
        assert helper
        compile(helper.group(1), "<gcs-generation-hold-helper>", "exec")


def test_gcs_inventory_applies_cas_hold_and_records_readback(
    monkeypatch, tmp_path: Path
) -> None:
    import io
    import urllib.parse
    import urllib.request

    match = re.search(
        r"python3 - \"\$LAKEHOUSE_BUCKET\" \"\$BACKUP_PREFIX\" .*? <<'PY'\n(.*?)\nPY",
        _read("scripts/gcp/backup.sh"),
        re.DOTALL,
    )
    assert match
    object_manifest = tmp_path / "lakehouse_objects.jsonl"
    bucket_evidence = tmp_path / "bucket.json"
    original = {
        "name": "gold/data.parquet",
        "size": "5",
        "generation": "101",
        "metageneration": "7",
        "md5Hash": "CY9rzUYh03PK3k6DJie09g==",
        "crc32c": "GNEjNQ==",
        "etag": "etag-7",
        "updated": "2026-08-11T00:00:00.000Z",
        "storageClass": "STANDARD",
    }
    held = {
        **original,
        "metageneration": "8",
        "etag": "etag-8",
        "temporaryHold": True,
    }
    calls: list[tuple[str, str]] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def response(payload: dict) -> Response:
        return Response(json.dumps(payload).encode())

    def fake_urlopen(request, timeout=0):
        del timeout
        url = request.full_url
        method = request.get_method()
        calls.append((method, url))
        if url.startswith("http://metadata.google.internal/"):
            assert request.get_header("Metadata-flavor") == "Google"
            return response({"access_token": "unit-token"})
        assert request.get_header("Authorization") == "Bearer unit-token"
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/storage/v1/b/unit-bucket":
            return response(
                {
                    "name": "unit-bucket",
                    "location": "EU",
                    "metageneration": "6",
                    "versioning": {"enabled": True},
                    "iamConfiguration": {
                        "uniformBucketLevelAccess": {"enabled": True},
                        "publicAccessPrevention": "enforced",
                    },
                    "softDeletePolicy": {"retentionDurationSeconds": "604800"},
                    "lifecycle": {
                        "rule": [
                            {
                                "action": {
                                    "storageClass": "NEARLINE",
                                    "type": "SetStorageClass",
                                },
                                "condition": {"age": 30},
                            },
                            {
                                "action": {"type": "Delete"},
                                "condition": {
                                    "isLive": False,
                                    "numNewerVersions": 5,
                                },
                            },
                        ]
                    },
                }
            )
        if parsed.path == "/storage/v1/b/unit-bucket/o":
            return response({"items": [original]})
        assert parsed.path.endswith("/o/gold%2Fdata.parquet")
        assert query["generation"] == ["101"]
        if method == "PATCH":
            assert query["ifMetagenerationMatch"] == ["7"]
            assert json.loads(request.data) == {"temporaryHold": True}
            return response(held)
        assert method == "GET"
        assert query["ifMetagenerationMatch"] == ["8"]
        return response(held)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gcs-inventory",
            "unit-bucket",
            "_omega_backups/unit",
            str(object_manifest),
            str(bucket_evidence),
            "EU",
        ],
    )
    exec(compile(match.group(1), "<gcs-inventory>", "exec"), {})

    rows = [
        json.loads(line)
        for line in object_manifest.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["generation"] == "101"
    assert rows[0]["metageneration"] == "8"
    assert rows[0]["temporaryHold"] is True
    assert [method for method, _url in calls].count("PATCH") == 1


def test_gcs_hold_pool_is_concurrent_but_manifest_order_is_deterministic(
    monkeypatch, tmp_path: Path
) -> None:
    import io
    import threading
    import urllib.parse
    import urllib.request

    match = re.search(
        r"python3 - \"\$LAKEHOUSE_BUCKET\" \"\$BACKUP_PREFIX\" .*? <<'PY'\n(.*?)\nPY",
        _read("scripts/gcp/backup.sh"),
        re.DOTALL,
    )
    assert match
    object_manifest = tmp_path / "lakehouse_objects.jsonl"
    bucket_evidence = tmp_path / "bucket.json"
    originals = [
        {
            "name": key,
            "size": "5",
            "generation": str(generation),
            "metageneration": "7",
            "md5Hash": "CY9rzUYh03PK3k6DJie09g==",
            "crc32c": "GNEjNQ==",
            "etag": f"etag-{generation}-7",
            "updated": "2026-08-11T00:00:00.000Z",
            "storageClass": "STANDARD",
        }
        for key, generation in (("a/data.parquet", 101), ("b/data.parquet", 102))
    ]
    by_generation = {row["generation"]: row for row in originals}
    barrier = threading.Barrier(2)
    concurrency_lock = threading.Lock()
    active = 0
    maximum_active = 0

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def response(payload: dict) -> Response:
        return Response(json.dumps(payload).encode())

    def fake_urlopen(request, timeout=0):
        nonlocal active, maximum_active
        del timeout
        url = request.full_url
        if url.startswith("http://metadata.google.internal/"):
            return response({"access_token": "unit-token"})
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/storage/v1/b/unit-bucket":
            return response(
                {
                    "name": "unit-bucket",
                    "location": "EU",
                    "metageneration": "6",
                    "versioning": {"enabled": True},
                    "iamConfiguration": {
                        "uniformBucketLevelAccess": {"enabled": True},
                        "publicAccessPrevention": "enforced",
                    },
                    "softDeletePolicy": {"retentionDurationSeconds": "604800"},
                    "lifecycle": {
                        "rule": [
                            {
                                "action": {
                                    "storageClass": "NEARLINE",
                                    "type": "SetStorageClass",
                                },
                                "condition": {"age": 30},
                            },
                            {
                                "action": {"type": "Delete"},
                                "condition": {
                                    "isLive": False,
                                    "numNewerVersions": 5,
                                },
                            },
                        ]
                    },
                }
            )
        if parsed.path == "/storage/v1/b/unit-bucket/o":
            return response({"items": originals})
        generation = query["generation"][0]
        original = by_generation[generation]
        held = {
            **original,
            "metageneration": "8",
            "temporaryHold": True,
        }
        if request.get_method() == "PATCH":
            with concurrency_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            barrier.wait(timeout=5)
            with concurrency_lock:
                active -= 1
            return response(held)
        return response(held)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gcs-inventory",
            "unit-bucket",
            "_omega_backups/unit",
            str(object_manifest),
            str(bucket_evidence),
            "EU",
        ],
    )
    exec(compile(match.group(1), "<gcs-inventory-concurrent>", "exec"), {})

    rows = [
        json.loads(line)
        for line in object_manifest.read_text(encoding="utf-8").splitlines()
    ]
    assert maximum_active == 2
    assert [row["key"] for row in rows] == ["a/data.parquet", "b/data.parquet"]
    assert all(row["temporaryHold"] is True for row in rows)


def test_restore_object_verifier_rejects_unheld_manifest_row(
    monkeypatch, tmp_path: Path
) -> None:
    match = re.search(
        r"OBJECT_RESULT=\"\$\(python3 - .*? <<'PY'\n(.*?)\nPY",
        _read("scripts/gcp/restore-rehearsal.sh"),
        re.DOTALL,
    )
    assert match
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "object_storage": {
                    "bucket": "unit-bucket",
                    "object_count": 1,
                    "total_bytes": 5,
                }
            }
        ),
        encoding="utf-8",
    )
    object_manifest = tmp_path / "lakehouse_objects.jsonl"
    object_manifest.write_text(
        json.dumps(
            {
                "key": "gold/data.parquet",
                "size_bytes": 5,
                "generation": "101",
                "metageneration": "8",
                "crc32c": "GNEjNQ==",
                "temporaryHold": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["gcs-restore-verify", str(manifest), str(object_manifest), "all"],
    )
    with pytest.raises(SystemExit, match="object manifest"):
        exec(compile(match.group(1), "<gcs-restore-verify>", "exec"), {})


def test_post_record_health_failure_leaves_atomic_state_unchanged(
    tmp_path: Path,
) -> None:
    bundles = tmp_path / "state-bundles"
    bundles.mkdir()
    old = bundles / "old"
    old.mkdir()
    (old / "bootstrap-state.json").write_text("old-bootstrap", encoding="utf-8")
    (old / "runtime-provenance.json").write_text("old-provenance", encoding="utf-8")
    state_link = tmp_path / "runtime-state"
    state_link.symlink_to(old, target_is_directory=True)

    staged = bundles / ".candidate"
    staged.mkdir()
    (staged / "bootstrap-state.json").write_text("new-bootstrap", encoding="utf-8")
    (staged / "runtime-provenance.json").write_text("new-provenance", encoding="utf-8")

    def adoption_transaction(*, health_ok: bool) -> None:
        if not health_ok:  # injected after both records exist, before commit
            raise RuntimeError("injected health failure")
        preview = tmp_path / ".runtime-state.preview"
        preview.symlink_to(staged, target_is_directory=True)
        os.replace(preview, state_link)

    with pytest.raises(RuntimeError, match="injected health failure"):
        adoption_transaction(health_ok=False)
    assert state_link.resolve() == old.resolve()
    assert (state_link / "bootstrap-state.json").read_text() == "old-bootstrap"
    assert (state_link / "runtime-provenance.json").read_text() == "old-provenance"


def test_operation_gate_verifies_atomic_pair_current_and_helper_hashes(
    tmp_path: Path,
) -> None:
    gate = REPO / "infra/terraform-gcp/templates/omega-operation-gate"
    app_root = tmp_path / "modecissions"
    shared = app_root / "shared"
    bundles = shared / "state-bundles"
    release_ref = "a" * 40
    release = app_root / "releases" / release_ref
    helper_root = release / "scripts/gcp"
    helper_root.mkdir(parents=True)
    bundles.mkdir(parents=True)
    (release / "VERSION").write_text("1.45.207-beta\n", encoding="utf-8")
    (release / "infra/terraform-gcp/release").mkdir(parents=True)
    base_compose = release / "infra/docker-compose.yml"
    release_compose = release / "infra/terraform-gcp/release/docker-compose.release.yml"
    shared_env = shared / "infra.env"
    gcp_compose = shared / "docker-compose.gcp.yml"
    base_compose.write_text("services: {}\n", encoding="utf-8")
    release_compose.write_text("services: {}\n", encoding="utf-8")
    shared_env.write_text("APP_ENV=production\n", encoding="utf-8")
    gcp_compose.write_text("services: {}\n", encoding="utf-8")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    reboot = helper_root / "reboot-runtime.sh"
    contract = helper_root / "runtime_contract.py"
    safe_io = helper_root / "safe_io.py"
    reboot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    contract.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    safe_io.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    reboot.chmod(0o755)
    contract.chmod(0o755)
    safe_io.chmod(0o755)
    (app_root / "current").symlink_to(release, target_is_directory=True)

    bundle = bundles / "candidate"
    bundle.mkdir()
    provenance = bundle / "runtime-provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "day2",
                "compose_project": "infra",
                    "deploy_ref": release_ref,
                    "version": "1.45.207-beta",
                    "runtime_input_sha256": {
                        "shared_env": digest(shared_env),
                        "base_compose": digest(base_compose),
                        "gcp_compose": digest(gcp_compose),
                        "release_compose": digest(release_compose),
                    },
                    "services": {},
            }
        ),
        encoding="utf-8",
    )

    (bundle / "bootstrap-state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "state": "complete",
                "deploy_ref": release_ref,
                "version": "1.45.207-beta",
                "runtime_provenance_sha256": digest(provenance),
                "reboot_helper": {
                    "mode": "current",
                    "source_ref": release_ref,
                    "source_artifact_uri": (
                        f"gs://omega-source/deploy-artifacts/{release_ref}/repo.tar.gz"
                    ),
                    "source_artifact_generation": "123",
                    "source_artifact_size_bytes": 456,
                    "source_artifact_sha256": "b" * 64,
                    "reboot_runtime_sha256": digest(reboot),
                    "runtime_contract_sha256": digest(contract),
                    "safe_io_sha256": digest(safe_io),
                },
            }
        ),
        encoding="utf-8",
    )
    (shared / "runtime-state").symlink_to(bundle, target_is_directory=True)
    env = {**os.environ, "OMEGA_GCP_APP_ROOT": str(app_root)}
    verified = subprocess.run(
        [gate], text=True, capture_output=True, env=env, check=False
    )
    assert verified.returncode == 0, verified.stderr
    helpers = subprocess.run(
        [gate, "print-helpers"], text=True, capture_output=True, env=env, check=False
    )
    assert helpers.returncode == 0, helpers.stderr
    assert helpers.stdout.strip() == f"{reboot}\t{contract}\t{safe_io}"

    reboot.write_text("tampered\n", encoding="utf-8")
    rejected = subprocess.run(
        [gate], text=True, capture_output=True, env=env, check=False
    )
    assert rejected.returncode == 76
    assert "checksum differs" in rejected.stderr


def test_restore_rehearsal_isolated_from_live_volumes() -> None:
    rehearsal = _read("scripts/gcp/restore-rehearsal.sh")
    assert "omega_gcp_rehearsal_" in rehearsal
    assert "--network none" in rehearsal
    assert "verified_generations" in rehearsal
    assert "ThreadPoolExecutor" in rehearsal
    assert "schema_migrations" in rehearsal
    assert "live_volumes_untouched=true" in rehearsal
    assert "docker inspect mode_postgres" in rehearsal
    assert "docker inspect mode_postgres_gold" in rehearsal
    assert "docker stop mode_postgres" not in rehearsal
    assert "docker stop mode_postgres_gold" not in rehearsal
    assert "docker rm mode_postgres" not in rehearsal
    assert "docker rm mode_postgres_gold" not in rehearsal
    assert "docker compose" not in rehearsal
    assert 'docker run -d --pull never --name "$OP_CONTAINER"' in rehearsal
    assert 'docker run -d --pull never --name "$GOLD_CONTAINER"' in rehearsal
    assert 'REHEARSAL_ADMIN="omega_rehearsal_${SUFFIX}"' in rehearsal
    assert "filter_pg_dump_bootstrap_role" not in rehearsal
    assert rehearsal.count('POSTGRES_USER="$REHEARSAL_ADMIN"') == 2
    assert rehearsal.count("POSTGRES_DB=postgres") == 2
    assert rehearsal.count("--pull never") == 2
    assert '"$GOLD_IMAGE" postgres -p 5433' in rehearsal
    assert "repo_digest" in rehearsal
    assert "image_id" in rehearsal
    assert "unique role absent from both unmodified dumps" in rehearsal


@pytest.mark.skipif(
    os.environ.get("OMEGA_ENABLE_DOCKER_RESTORE_TESTS") != "1",
    reason="set OMEGA_ENABLE_DOCKER_RESTORE_TESTS=1 for the live Docker restore test",
)
@pytest.mark.parametrize(
    ("source_container", "port"),
    [("mode_postgres", "5432"), ("mode_postgres_gold", "5433")],
)
def test_unmodified_pg_dumpall_restores_with_distinct_bootstrap_role(
    tmp_path: Path, source_container: str, port: str
) -> None:
    suffix = uuid.uuid4().hex[:12]
    target = f"omega_gcp_restore_test_{suffix}"
    volume = target
    admin = f"omega_rehearsal_{suffix}"
    dump = tmp_path / f"{source_container}.sql"

    def docker(
        *arguments: str, input_file: Path | None = None
    ) -> subprocess.CompletedProcess:
        stdin = input_file.open("rb") if input_file else None
        try:
            return subprocess.run(
                ["docker", *arguments],
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        finally:
            if stdin:
                stdin.close()

    source = docker("inspect", source_container)
    assert source.returncode == 0, source.stderr.decode()
    source_info = json.loads(source.stdout)[0]
    image_id = source_info["Image"]
    configured = source_info["Config"]["Image"]
    repository = configured.rsplit(":", 1)[0]
    inspected = docker("image", "inspect", image_id)
    assert inspected.returncode == 0, inspected.stderr.decode()
    repo_digests = [
        value
        for value in json.loads(inspected.stdout)[0].get("RepoDigests") or []
        if value.startswith(repository + "@sha256:")
    ]
    assert len(repo_digests) == 1
    image = repo_digests[0]

    with dump.open("wb") as stream:
        snapshot = subprocess.run(
            [
                "docker",
                "exec",
                source_container,
                "pg_dumpall",
                "-U",
                "postgres",
                "-p",
                port,
                "--clean",
                "--if-exists",
            ],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert snapshot.returncode == 0, snapshot.stderr.decode()
    assert dump.stat().st_size > 0

    assert docker("volume", "create", volume).returncode == 0
    try:
        command = [
            "run",
            "-d",
            "--pull",
            "never",
            "--name",
            target,
            "--network",
            "none",
            "--mount",
            f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "-e",
            f"POSTGRES_USER={admin}",
            "-e",
            "POSTGRES_DB=postgres",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            image,
        ]
        if port != "5432":
            command.extend(["postgres", "-p", port])
        started = docker(*command)
        assert started.returncode == 0, started.stderr.decode()
        for _ in range(60):
            ready = docker(
                "exec", target, "pg_isready", "-h", "127.0.0.1", "-p", port, "-U", admin
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("isolated PostgreSQL did not become ready")

        restored = docker(
            "exec",
            "-i",
            target,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            admin,
            "-d",
            "postgres",
            "-p",
            port,
            input_file=dump,
        )
        assert restored.returncode == 0, restored.stderr.decode()
        role = docker(
            "exec",
            target,
            "psql",
            "-At",
            "-U",
            admin,
            "-d",
            "postgres",
            "-p",
            port,
            "-c",
            f"SELECT count(*) FROM pg_roles WHERE rolname='{admin}' AND rolsuper;",
        )
        assert role.returncode == 0, role.stderr.decode()
        assert role.stdout.strip() == b"1"
    finally:
        docker("rm", "-f", target)
        docker("volume", "rm", volume)


def test_migration_runner_accepts_only_a_safe_explicit_project_and_env() -> None:
    migration = _read("scripts/apply_db_migrations.sh")
    deploy = _read("scripts/gcp/day2-release.sh")
    assert "OMEGA_MIGRATION_ENV_FILE" in migration
    assert "OMEGA_MIGRATION_COMPOSE_PROJECT_NAME" in migration
    assert "^[a-z0-9][a-z0-9_-]*$" in migration
    assert 'COMPOSE+=(--project-name "${COMPOSE_PROJECT_NAME_VALUE}")' in migration
    assert "COMPOSE_DISABLE_ENV_FILE=1" in migration
    assert "env-validate" in migration
    assert "--forbid-prefix OMEGA_MIGRATION_" in migration
    assert 'source "${ENV_FILE}"' not in migration
    assert 'exec -T -e "PGOPTIONS=' not in migration
    assert 'BOOTSTRAP_MODE="${OMEGA_MIGRATION_BOOTSTRAP_MODE:-0}"' in migration
    assert "OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER" in migration
    assert "restricted to local/development/test" in migration
    for binding in (
        "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT=1",
        "OMEGA_MIGRATION_BOOTSTRAP_MODE=0",
        'OMEGA_MIGRATION_OLD_REF="$OLD_REF"',
        'OMEGA_MIGRATION_CANDIDATE_REF="$DEPLOY_REF"',
        'OMEGA_MIGRATION_RELEASE_VERSION="$EXPECTED_VERSION"',
        "OMEGA_MIGRATION_BASELINE_MANIFEST=",
        "OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256=",
        "OMEGA_MIGRATION_RELEASE_MANIFEST=",
        "OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256=",
    ):
        assert binding in deploy


def test_candidate_git_archive_has_no_git_metadata_and_day2_needs_no_git() -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert archive.returncode == 0, archive.stderr.decode()
    listing = subprocess.run(
        ["tar", "-tf", "-"],
        input=archive.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert listing.returncode == 0, listing.stderr.decode()
    names = listing.stdout.decode().splitlines()
    assert not any(name == ".git" or name.startswith(".git/") for name in names)
    deploy = _read("scripts/gcp/day2-release.sh")
    migration_call = deploy[
        deploy.index("OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT=1") : deploy.index(
            'write_operation_state "migrated"'
        )
    ]
    assert 'OMEGA_MIGRATION_CANDIDATE_REF="$DEPLOY_REF"' in migration_call
    assert "git rev-parse" not in migration_call


def test_startup_publishes_shared_runtime_only_after_gcp_reconciliation() -> None:
    runtime = _read("scripts/gcp/bootstrap-runtime.sh")
    shared_copy = runtime.index('mv -Tf "${SHARED_ENV_TMP}" "${SHARED_ENV}"')
    assert shared_copy > runtime.index("set_env S3_ENDPOINT_URL")
    assert shared_copy > runtime.index("if hmac_secret_key=")
    assert '"${SAFE_IO}" fsync-file "${SHARED_ENV_TMP}"' in runtime
    assert 'mv -Tf "${GCP_RUNTIME_COMPOSE_TMP}" "${GCP_RUNTIME_COMPOSE}"' in runtime


def test_startup_is_bootstrap_once_and_fences_partial_reboot_state() -> None:
    startup = _read("infra/terraform-gcp/templates/startup.sh.tftpl")
    runtime = _read("scripts/gcp/bootstrap-runtime.sh")
    guard = _read("infra/terraform-gcp/templates/omega-operation-gate")
    locals = _read("infra/terraform-gcp/locals.tf")
    compute = _read("infra/terraform-gcp/compute.tf")
    outputs = _read("infra/terraform-gcp/outputs.tf")

    assert "operation_guard_base64" in locals
    assert "metadata_startup_script = local.startup_script" in compute
    assert "ignore_changes = [metadata_startup_script]" in compute
    assert "setMetadata" in compute
    assert 'output "source_sha"' in outputs
    assert 'output "source_object"' in outputs
    assert 'output "startup_script_sha256"' in outputs
    assert runtime.index("omega-operation-gate") < runtime.index(
        "docker-ce docker-ce-cli"
    )
    assert runtime.index(
        'if [[ -e "${STATE_LINK}" || -L "${STATE_LINK}" ]]'
    ) < runtime.index("download_source()")
    assert "recovering exact current without download/build/pull" in runtime
    assert '"operation": "bootstrap"' in runtime
    assert 'mv -Tf "${STATE_LINK_TMP}" "${STATE_LINK}"' in runtime
    assert 'rm -f -- "${OPERATION_MARKER}"' in runtime
    assert "OMEGA_GCP_RUNTIME_CONTRACT" in runtime
    assert "systemctl stop docker.service docker.socket containerd.service" in startup
    assert '"$RUNTIME_ROOT/bootstrap-runtime.sh"' in startup
    assert "runtime-state is not an atomic symlink" in guard
    assert "runtime provenance checksum differs" in guard
    assert "reboot helper checksum differs" in guard


def test_startup_template_renders_to_syntax_valid_bash() -> None:
    rendered = _read("infra/terraform-gcp/templates/startup.sh.tftpl")
    values = {
        "startup_config_base64": b64encode(b"{}").decode(),
        "bootstrap_runtime_base64": b64encode(
            _read("scripts/gcp/bootstrap-runtime.sh").encode()
        ).decode(),
        "safe_io_base64": b64encode(_read("scripts/gcp/safe_io.py").encode()).decode(),
        "operation_guard_base64": b64encode(
            _read("infra/terraform-gcp/templates/omega-operation-gate").encode()
        ).decode(),
    }
    for key, value in values.items():
        rendered = rendered.replace("${" + key + "}", value)
    rendered = rendered.replace("$${", "${")
    result = subprocess.run(
        ["bash", "-n"], input=rendered, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_controller_binds_backup_and_deploy_to_live_terraform_render(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_module()
    monkeypatch.setenv("OMEGA_TERRAFORM_BIN", sys.executable)
    deploy_ref = "a" * 40
    artifact = module.ArtifactRef(
        uri=f"gs://omega-source-bucket/deploy-artifacts/{deploy_ref}/repo.tar.gz",
        generation="123",
        size_bytes=456,
        sha256="b" * 64,
    )
    startup_config = {
        "source": {
            "bucket": "omega-source-bucket",
            "object": f"deploy-artifacts/{deploy_ref}/repo.tar.gz",
            "ref": deploy_ref,
            "generation": "123",
            "size_bytes": 456,
            "archive_sha256": "b" * 64,
        }
    }
    startup = "\n".join(
        [
            "#!/usr/bin/env bash",
            "STARTUP_CONFIG_BASE64='"
            + b64encode(json.dumps(startup_config).encode()).decode()
            + "'",
            "",
        ]
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(b64encode(startup.encode()).decode()), ""
        ),
    )
    reviewed = tmp_path / "production.tfvars"
    reviewed.write_text("placeholder\n", encoding="utf-8")
    reviewed.chmod(0o600)
    assert (
        module.validate_terraform_source_contract(
            terraform_dir=REPO / "infra/terraform-gcp",
            var_file=reviewed,
            source_ref=deploy_ref,
            artifact=artifact,
        )
        == hashlib.sha256(startup.encode()).hexdigest()
    )
    with pytest.raises(RuntimeError, match="not byte-bound to the artifact"):
        module.validate_terraform_source_contract(
            terraform_dir=REPO / "infra/terraform-gcp",
            var_file=reviewed,
            source_ref="c" * 40,
            artifact=artifact,
        )

    reviewed.write_text(
        "\n".join(
            [
                'project_id = "omega-production"',
                'project_number = "894064513501"',
                'billing_account_id = "01A3E0-B708F4-6EA299"',
                'source_bucket = "omega-source-bucket"',
                f'source_object = "deploy-artifacts/{deploy_ref}/repo.tar.gz"',
                f'source_sha = "{deploy_ref}"',
                'source_generation = "123"',
                "source_size_bytes = 456",
                'source_archive_sha256 = "' + "b" * 64 + '"',
                'app_machine_type = "e2-standard-4"',
                "boot_disk_size_gb = 60",
                "data_disk_size_gb = 150",
                'public_console_domain = "gcp-console.example.com"',
                'public_workspace_domain = "gcp-workspace.example.com"',
                "enable_https = true",
                "enable_airflow_scheduler = true",
                'monthly_budget_currency = "EUR"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    module.validate_reviewed_terraform_inputs(
        terraform_dir=REPO / "infra/terraform-gcp",
        var_file=reviewed,
        source_ref=deploy_ref,
        artifact=artifact,
        project_id="omega-production",
        project_number="894064513501",
        billing_account_id="01A3E0-B708F4-6EA299",
        public_console_domain="gcp-console.example.com",
        public_workspace_domain="gcp-workspace.example.com",
    )
    reviewed.write_text(reviewed.read_text().replace("60", "30", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="boot_disk_size_gb"):
        module.validate_reviewed_terraform_inputs(
            terraform_dir=REPO / "infra/terraform-gcp",
            var_file=reviewed,
            source_ref=deploy_ref,
            artifact=artifact,
            project_id="omega-production",
            project_number="894064513501",
            billing_account_id="01A3E0-B708F4-6EA299",
            public_console_domain="gcp-console.example.com",
            public_workspace_domain="gcp-workspace.example.com",
        )

    controller = _read("scripts/gcp_release.py")
    backup_call = controller[controller.index("def command_backup") :]
    deploy_call = controller[controller.index("def command_deploy") :]
    assert backup_call.index("validate_terraform_source_contract") < backup_call.index(
        'script=REMOTE_ROOT / "backup.sh"'
    )
    assert backup_call.index("validate_reviewed_terraform_inputs") < backup_call.index(
        'script=REMOTE_ROOT / "backup.sh"'
    )
    assert backup_call.index("validate_startup_metadata") < backup_call.index(
        'script=REMOTE_ROOT / "backup.sh"'
    )
    assert "args.current_live_ref" in backup_call
    assert deploy_call.index("validate_terraform_source_contract") < deploy_call.index(
        'script=REMOTE_ROOT / "day2-release.sh"'
    )


def test_terraform_binary_defaults_to_tofu_and_fails_closed(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.delenv("OMEGA_TERRAFORM_BIN", raising=False)
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda value: "/usr/bin/tofu" if value == "tofu" else None,
    )
    assert module.terraform_binary() == "/usr/bin/tofu"
    monkeypatch.setattr(module.shutil, "which", lambda _value: None)
    with pytest.raises(RuntimeError, match="binary is unavailable"):
        module.terraform_binary()


def test_startup_metadata_cas_rejects_stale_hash_and_preserves_runtime_identity(
    monkeypatch,
) -> None:
    module = _load_module()
    old_startup = "#!/usr/bin/env bash\necho old\n"
    new_startup = "#!/usr/bin/env bash\necho new\n"
    before = module.InstanceMetadataSnapshot(
        fingerprint="oldFingerprint=",
        items={"enable-oslogin": "TRUE", "startup-script": old_startup},
        instance_id="12345",
        status="RUNNING",
        last_start_timestamp="2026-08-07T00:00:00Z",
    )
    after = module.InstanceMetadataSnapshot(
        fingerprint="newFingerprint=",
        items={"enable-oslogin": "TRUE", "startup-script": new_startup},
        instance_id="12345",
        status="RUNNING",
        last_start_timestamp="2026-08-07T00:00:00Z",
    )
    reads = iter([before, after])
    monkeypatch.setattr(module, "read_instance_metadata", lambda **_kwargs: next(reads))
    captured = {}

    def set_metadata(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(module, "_set_instance_metadata_cas", set_metadata)
    old_sha = hashlib.sha256(old_startup.encode()).hexdigest()
    used_before, used_after = module.replace_startup_metadata_cas(
        project="omega-production",
        zone="us-central1-a",
        instance="omega-production-app",
        expected_old_sha256=old_sha,
        new_startup=new_startup,
    )
    assert used_before == before
    assert used_after == after
    assert captured["fingerprint"] == before.fingerprint
    assert captured["items"]["enable-oslogin"] == "TRUE"
    assert captured["items"]["startup-script"] == new_startup

    monkeypatch.setattr(module, "read_instance_metadata", lambda **_kwargs: before)
    with pytest.raises(RuntimeError, match="differs from the reviewed old hash"):
        module.replace_startup_metadata_cas(
            project="omega-production",
            zone="us-central1-a",
            instance="omega-production-app",
            expected_old_sha256="0" * 64,
            new_startup=new_startup,
        )

    monkeypatch.setattr(
        module,
        "_set_instance_metadata_cas",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("GCE metadata fingerprint changed; CAS rejected")
        ),
    )
    with pytest.raises(RuntimeError, match="fingerprint changed; CAS rejected"):
        module.replace_startup_metadata_cas(
            project="omega-production",
            zone="us-central1-a",
            instance="omega-production-app",
            expected_old_sha256=old_sha,
            new_startup=new_startup,
            before=before,
        )


def test_startup_metadata_backup_is_byte_exact_and_restore_bound(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_module()
    startup = "#!/usr/bin/env bash\nprintf 'prior bytes\\n'\n"
    snapshot = module.InstanceMetadataSnapshot(
        fingerprint="exactFingerprint=",
        items={"enable-oslogin": "TRUE", "startup-script": startup},
        instance_id="12345",
        status="RUNNING",
        last_start_timestamp="2026-08-07T00:00:00Z",
    )
    sha = hashlib.sha256(startup.encode()).hexdigest()

    def fake_run(command, **_kwargs):
        if "describe" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "generation": "123456",
                        "size": len(startup.encode()),
                        "metadata": {
                            "omega-startup-sha256": sha,
                            "omega-metadata-fingerprint": snapshot.fingerprint,
                            "omega-instance-id": snapshot.instance_id,
                        },
                    }
                ),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module, "run", fake_run)
    backup = module.backup_startup_metadata(
        snapshot=snapshot,
        bucket="omega-source-bucket",
        instance="omega-production-app",
        evidence_dir=tmp_path,
    )
    assert (tmp_path / "prior-startup.sh").read_bytes() == startup.encode()
    assert backup["generation"] == "123456"
    assert backup["sha256"] == sha
    assert backup["fingerprint"] == snapshot.fingerprint


def test_ghcr_saved_plan_allows_exactly_seven_staged_creates(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_module()
    plan = tmp_path / "ghcr.tfplan"
    plan.write_bytes(b"saved-plan")

    def payload(actions: list[str] | None = None) -> dict:
        changes = []
        for address, expected in module.GHCR_PLAN_ACTIONS.items():
            secret_name = "ghcr_pull_credentials"
            match = re.search(r'\["([^"]+)"\]', address)
            if match:
                secret_name = match.group(1)
            after = {
                "project": "omega-production",
                "secret_id": f"omega-production-{secret_name}",
            }
            if "iam_member" in address:
                after.update(
                    {
                        "role": "roles/secretmanager.secretAccessor",
                        "member": (
                            "serviceAccount:omega-production-app@"
                            "omega-production.iam.gserviceaccount.com"
                        ),
                    }
                )
            changes.append(
                {
                    "address": address,
                    "change": {
                        "actions": actions or expected,
                        "before": None,
                        "after": after,
                        "replace_paths": None,
                    },
                }
            )
        changes.append(
            {
                "address": "google_service_account.app",
                "change": {"actions": ["no-op"], "replace_paths": None},
            }
        )
        return {"resource_changes": changes, "output_changes": None}

    monkeypatch.setenv("OMEGA_TERRAFORM_BIN", sys.executable)
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(payload()), ""
        ),
    )
    module.validate_ghcr_saved_plan(
        terraform_dir=REPO / "infra/terraform-gcp",
        plan_path=plan,
        project="omega-production",
        environment="production",
    )

    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(payload(["update"])), ""
        ),
    )
    with pytest.raises(RuntimeError, match="actions differ"):
        module.validate_ghcr_saved_plan(
            terraform_dir=REPO / "infra/terraform-gcp",
            plan_path=plan,
            project="omega-production",
            environment="production",
        )

    wrong_target = payload()
    wrong_target["resource_changes"][0]["change"]["after"]["project"] = "other-project"
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(wrong_target), ""
        ),
    )
    with pytest.raises(RuntimeError, match="target differs"):
        module.validate_ghcr_saved_plan(
            terraform_dir=REPO / "infra/terraform-gcp",
            plan_path=plan,
            project="omega-production",
            environment="production",
        )


def test_startup_restore_and_saved_plan_only_are_operator_contracts() -> None:
    controller = _read("scripts/gcp_release.py")
    compute = _read("infra/terraform-gcp/compute.tf")
    runbook = _read("docs/runbook/16_gcp_canonical_day2_release.md")
    apply = controller[controller.index("def command_apply_ghcr_access") :]
    apply = apply[: apply.index("def command_plan_backup_storage")]
    assert "ignore_changes = [metadata_startup_script]" in compute
    assert "backup_startup_metadata" in controller
    assert "restore_contract" in controller
    assert "backup_generation" in controller
    assert "expected_current_sha256" in controller
    assert "setMetadata" in controller
    assert '"apply"' in apply
    assert '"-target=' not in apply
    assert "exactly seven creates" in runbook
    assert "inverse CAS" in runbook


def test_day2_ci_runs_focal_tests_and_static_scanners() -> None:
    focal = _read(".github/workflows/control-room-postgres-rls.yml")
    lint = _read(".github/workflows/lint.yml")
    security = _read(".github/workflows/security.yml")
    changed = _read("scripts/ci_changed_areas.py")
    for test in (
        "tests/test_gcp_day2_release.py",
        "tests/test_gcp_operation_gate.py",
        "tests/test_gcp_safe_io.py",
        "tests/test_gcp_terraform_contract.py",
        "tests/test_release_image_promotion.py",
    ):
        assert test in focal
    assert lint.count("scripts/gcp_release.py scripts/release_images.py") == 2
    assert lint.count(
        "scripts/gcp/runtime_contract.py scripts/gcp/safe_io.py"
    ) == 2
    assert (
        lint.count("scripts/migration_guard.py scripts/generate_migration_manifests.py")
        == 2
    )
    assert security.count("scripts/gcp_release.py scripts/release_images.py") == 2
    assert security.count(
        "scripts/gcp/runtime_contract.py scripts/gcp/safe_io.py"
    ) == 2
    assert (
        security.count(
            "scripts/migration_guard.py scripts/generate_migration_manifests.py"
        )
        == 2
    )
    assert '"scripts/gcp_release.py"' in changed
    assert '"scripts/release_images.py"' in changed
    assert '"scripts/gcp/"' in changed
    assert '"scripts/migration_guard.py"' in changed
    assert '"scripts/generate_migration_manifests.py"' in changed


def test_authenticated_image_preflight_is_15_of_15_and_server_owned() -> None:
    preflight = _read("scripts/gcp/image-preflight.sh")
    assert "OMEGA_GCP_IMAGE_PREFLIGHT_JSON" in preflight
    assert 'image_count":15' in preflight
    assert "ghcr-auth-run.sh" in preflight
    assert "preflight-release-images.sh" in preflight
    assert "versions/latest" not in preflight
    assert "GHCR_SECRET_VERSION" in preflight
    assert "credential output suppressed" in preflight
    assert (
        "image-preflights/${PURPOSE}-${TARGET_KIND}-${TARGET_TAG}-${TARGET_REF}-by-${HELPER_REF}"
        in preflight
    )


def test_operator_controller_validates_identity_and_redacts_evidence(
    monkeypatch,
) -> None:
    module = _load_module()
    assert module.redact("password=supersecret") == "[REDACTED]"
    assert "[REDACTED]" in module.redact("token: ghp_examplevalue")
    assert module.TAG_RE.fullmatch("v1.45.207-beta")
    assert not module.TAG_RE.fullmatch("latest")

    git_values = {
        ("remote", "get-url", "origin"): (
            "git@github.com:emmanuelnavaromero02-commits/CONSOLA-V1.git"
        ),
        ("cat-file", "-t", "refs/tags/v1.45.207-beta"): "tag",
        ("rev-parse", "refs/tags/v1.45.207-beta"): "b" * 40,
        ("rev-parse", "refs/tags/v1.45.207-beta^{commit}"): "a" * 40,
        ("rev-parse", "origin/main"): "a" * 40,
        ("show", f"{'a' * 40}:VERSION"): "1.45.207-beta",
    }
    monkeypatch.setattr(module, "git", lambda *args, **_kwargs: git_values[args])
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            (
                f"{'b' * 40}\trefs/tags/v1.45.207-beta\n"
                f"{'a' * 40}\trefs/tags/v1.45.207-beta^{{}}\n"
            ),
            "",
        ),
    )
    assert (
        module.validate_release_identity("v1.45.207-beta", "a" * 40) == "1.45.207-beta"
    )


def test_remote_evidence_parser_rejects_missing_or_failed_gates(tmp_path: Path) -> None:
    module = _load_module()
    stdout = (
        "OMEGA_GCP_RELEASE_CHECK\tartifact\tPASS\tsha256=abc\n"
        'OMEGA_GCP_RELEASE_JSON={"status":"PASS","deploy_ref":"abc"}\n'
    )
    checks, payload = module.parse_remote(
        stdout, "OMEGA_GCP_RELEASE_CHECK", "OMEGA_GCP_RELEASE_JSON"
    )
    assert checks == [{"name": "artifact", "status": "PASS", "evidence": "sha256=abc"}]
    result = module.RemoteResult(0, stdout, "")
    assert (
        module.write_evidence(
            tmp_path,
            operation="Deploy",
            result=result,
            checks=checks,
            payload=payload,
        )
        == "PASS"
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["secrets_included"] is False
    assert summary["status"] == "PASS"


def test_make_targets_and_runbook_define_no_blind_n_minus_one_rollback() -> None:
    makefile = _read("Makefile")
    runbook = _read("docs/runbook/16_gcp_canonical_day2_release.md")
    assert "backup-gcp-canonical:" in makefile
    assert "deploy-gcp-canonical:" in makefile
    assert "restore-rehearsal-gcp:" in makefile
    assert "adopt-gcp-startup-metadata:" in makefile
    assert "restore-gcp-startup-metadata:" in makefile
    assert "plan-gcp-ghcr-access:" in makefile
    assert "apply-gcp-ghcr-access:" in makefile
    assert "There is intentionally no blind N-1" in runbook
    assert "AWS is DR/standby, not a second writer" in runbook
    assert "GCP_OBJECT_VERIFY_MODE=all" in runbook
    assert "/opt/modecissions/shared/bin/ghcr-auth-run" in runbook
