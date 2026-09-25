"""The reproducible GCP canonical deploy driver — structural safety contract.

The driver deploys to the canonical GCP writer, so it cannot be exercised in CI.
What we pin here is that it stays fail-closed, backs up before mutating, never
targets AWS, and keeps the dry-run non-destructive — so a review catches a
regression before a human runs it against production.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
from pathlib import Path

import pytest

from scripts.release_image_promotion import CANONICAL_SERVICES, OCI_MANIFEST
from scripts.gcp.render_gcp_compose_override import RenderError, render_file

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "scripts" / "gcp" / "gcp-canonical-deploy.sh"
REMOTE = REPO / "scripts" / "gcp" / "gcp-canonical-deploy-remote.sh"
TAG = "v1.2.3"
SOURCE_SHA = "a" * 40


def _release_assets(
    tmp_path: Path,
    *,
    repository: str = "omega-owner/CONSOLA-V1",
) -> tuple[Path, Path, dict[str, object]]:
    owner = repository.split("/", 1)[0].lower()
    images = []
    for index, service in enumerate(CANONICAL_SERVICES, start=1):
        digest = f"sha256:{index:064x}"
        image = f"ghcr.io/{owner}/{service}"
        images.append(
            {
                "build_run_id": 42,
                "digest": digest,
                "digest_reference": f"{image}@{digest}",
                "image": image,
                "manifest_media_type": OCI_MANIFEST,
                "manifest_size": 123,
                "release_tag": TAG,
                "schema_version": 2,
                "service": service,
                "source_sha": SOURCE_SHA,
            }
        )
    value: dict[str, object] = {
        "build_run_id": 42,
        "images": images,
        "kind": "omega-release-manifest",
        "release_tag": TAG,
        "repository": repository,
        "schema_version": 2,
        "source_sha": SOURCE_SHA,
    }
    manifest = tmp_path / f"omega-release-manifest-{TAG}.json"
    checksum = tmp_path / f"{manifest.name}.sha256"
    _write_release_assets(manifest, checksum, value)
    return manifest, checksum, value


def _write_release_assets(
    manifest: Path, checksum: Path, value: dict[str, object]
) -> None:
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    manifest.write_bytes(raw)
    checksum.write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {manifest.name}\n",
        encoding="ascii",
    )


def _run_local_manifest_preflight(
    tmp_path: Path,
    manifest: Path,
    checksum: Path,
    *,
    owner: str = "omega-owner",
    image_pull_min_free_gib: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "OMEGA_GHCR_OWNER": owner,
        "OMEGA_GHCR_PULL_SECRET_VERSION": "1",
        "OMEGA_INSTANCE": "omega-staging-app",
        "OMEGA_PROJECT_ID": "omega-project",
        "OMEGA_RELEASE_MANIFEST": str(manifest),
        "OMEGA_RELEASE_MANIFEST_CHECKSUM": str(checksum),
        "OMEGA_SOURCE_BUCKET": "omega-source-bucket",
        "OMEGA_ZONE": "us-central1-a",
    }
    if image_pull_min_free_gib is not None:
        env["OMEGA_IMAGE_PULL_MIN_FREE_GIB"] = image_pull_min_free_gib
    return subprocess.run(
        ["bash", str(LOCAL), TAG, SOURCE_SHA],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("path", [LOCAL, REMOTE])
def test_scripts_exist_and_parse(path):
    assert path.exists(), f"{path} is missing"
    r = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, f"{path.name} syntax error:\n{r.stderr}"


def test_driver_never_targets_aws():
    # The AWS deploy surface must never be invoked (it may be named only in a
    # "must never run" warning comment, which documents the boundary).
    for path in (LOCAL, REMOTE):
        text = path.read_text(encoding="utf-8")
        assert "aws ssm" not in text
        assert "docker-compose.aws.yml" not in text
        assert "deploy_main_aws.py" not in text.replace(
            "scripts/deploy_main_aws.py is the", ""
        )  # allow the single boundary-warning reference, forbid any invocation


def test_remote_backs_up_both_dbs_before_mutation():
    text = REMOTE.read_text(encoding="utf-8")
    # Slow staging/pulls finish before the short maintenance window. Inside
    # that window, quiescence precedes pg_dump, recreate and migrations.
    preflight = text.index("preflight images")
    quiesce = text.index("step 1 quiesce")
    backup = text.index("step 1 backup")
    databases = text.index("step 4 databases")
    migrations = text.index("step 5 migrations")
    assert preflight < quiesce < backup < databases < migrations
    assert "modecissions" in text and "modecissions_gold" in text
    assert "pg_dump" in text and "SHA256SUMS" in text
    # An empty dump is refused (never a false safety net).
    assert "backup dump is empty" in text


def test_remote_quiesces_all_writers_and_fences_database_sessions():
    text = REMOTE.read_text(encoding="utf-8")
    quiesce = text.index('log "step 1 quiesce')
    first_dump = text.index('docker exec "${main_pg}" pg_dump', quiesce)
    recreate = text.index('log "step 4 databases', first_dump)
    migrations = text.index('log "step 5 migrations', recreate)
    lift_fence = text.index(
        'restore_database_access || die "could not restore database connection limits',
        migrations,
    )
    full_stack = text.index('log "step 5b deploy', lift_fence)
    assert quiesce < first_dump < recreate < migrations < lift_fence < full_stack
    assert "running_writer_containers" in text
    assert "docker stop --time 45" in text
    assert "CONNECTION LIMIT 0" in text
    assert "pg_stat_activity" in text
    assert "assert_zero_competing_sessions" in text
    assert ".gcp-canonical-deploy.lock" in text
    assert "flock -n 8" in text


def test_remote_recreates_db_then_migrates_then_full_stack():
    """Deploy order fixes two real bugs found running it live: (1) recreate the
    DBs from the new release so migrations see the new init mount; (2) migrate
    BEFORE the app/airflow start so they come up against a ready schema."""
    text = REMOTE.read_text(encoding="utf-8")
    databases = text.index("step 4 databases")
    migrations = text.index("step 5 migrations")
    full = text.index("step 5b deploy")
    assert databases < migrations < full
    # airflow runs as uid 50000 and writes logs; its dirs must be chowned or it
    # crash-loops ("Unable to configure handler 'processor'").
    assert "chown -R 50000:0" in text and "airflow/logs" in text
    # A slow-booting service is retried rather than aborting the whole deploy.
    assert "compose up attempt" in text


def test_remote_is_fail_closed_with_verified_restore():
    text = REMOTE.read_text(encoding="utf-8")
    assert "trap on_err ERR EXIT" in text
    assert "rollback()" in text
    # Restore is checksum-verified AND atomic (all-or-nothing → no partial-drop
    # data loss), with competing backends terminated first.
    assert "restore_db" in text
    assert "want_sha" in text and "sha256_of" in text
    assert "--single-transaction" in text
    assert "pg_terminate_backend" in text
    assert "MUTATED" in text and "PROMOTED" in text


def test_rollback_stops_candidate_then_restores_and_always_attempts_previous():
    text = REMOTE.read_text(encoding="utf-8")
    start = text.index("rollback()")
    end = text.index("on_err()", start)
    rollback = text[start:end]
    assert rollback.index("stop_all_writers") < rollback.index("restore_db")
    assert rollback.index("restore_db") < rollback.index("resume_previous_release")
    # Previous services resume only after exclusivity, every required restore,
    # and environment rollback were all verified. A failed restore leaves the
    # database fenced and writers stopped for manual recovery.
    restart_gate = rollback.index(
        '"${exclusive_ok}" -eq 1 && "${restore_ok}" -eq 1 && "${environment_ok}" -eq 1'
    )
    resume = rollback.index("resume_previous_release")
    assert restart_gate < resume
    assert 'contain_blocked_recovery "previous release resume failed or was partial"' in rollback
    assert 'contain_blocked_recovery "recovery prerequisites were not verified"' in rollback
    containment_start = text.index("contain_blocked_recovery()")
    containment = text[containment_start:start]
    assert containment.index("stop_all_writers") < containment.index("fence_databases")
    assert "rollback BLOCKED: writers stopped and database fences active" in containment
    assert "QUIESCED:-0" in text
    assert 'MUTATED:-0}" -eq 1 || "${QUIESCED:-0}" -eq 1' in text


def test_quiesced_pre_mutation_failure_can_resume_previous_without_db_restore():
    text = REMOTE.read_text(encoding="utf-8")
    start = text.index("rollback()")
    end = text.index("on_err()", start)
    rollback = text[start:end]
    mutated_branch = rollback.index('if [[ "${MUTATED}" -eq 1 ]]')
    environment_restore = rollback.index("restore_environment", mutated_branch)
    restart_gate = rollback.index('"${exclusive_ok}" -eq 1', environment_restore)
    assert "restore_ok=1" in rollback[:mutated_branch]
    # restore_ok can become false only inside the MUTATED=1 branch. Therefore
    # a post-quiesce backup failure (MUTATED=0) reaches the restart gate.
    assert "restore_ok=0" in rollback[mutated_branch:environment_restore]
    assert "restore_ok=0" not in rollback[environment_restore:]
    assert environment_restore < restart_gate


def test_failed_partial_previous_resume_is_recontained_behaviorally(tmp_path):
    text = REMOTE.read_text(encoding="utf-8")
    start = text.index("contain_blocked_recovery()")
    end = text.index("on_err()", start)
    recovery_functions = text[start:end]
    harness = f"""
{recovery_functions}
events=()
stop_calls=0
log() {{ events+=("log:$*"); }}
stop_all_writers() {{
  stop_calls=$((stop_calls + 1))
  events+=("stop${{stop_calls}}")
  return 0
}}
fence_databases() {{ events+=("fence"); return 0; }}
restore_environment() {{ events+=("environment"); return 0; }}
restore_db() {{ events+=("restore"); return 0; }}
resume_previous_release() {{ events+=("resume-partial"); return 1; }}
MUTATED=0
BACKUP_DONE=0
PREV_TARGET={shlex.quote(str(tmp_path))}
rollback
printf '%s\n' "${{events[@]}}"
"""
    result = subprocess.run(
        ["bash"], input=harness, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    events = result.stdout.splitlines()
    resume = events.index("resume-partial")
    assert events.index("stop2") > resume
    assert events.index("fence") > events.index("stop2")
    assert any(
        event.startswith("log:rollback BLOCKED: writers stopped")
        for event in events[resume:]
    )


def test_remote_compose_runs_from_release_root_not_infra():
    """compose_up uses infra/-relative -f paths, so both callers must cd to the
    release ROOT, never into infra/ (the audit P0: cd .../infra -> infra/infra/)."""
    text = REMOTE.read_text(encoding="utf-8")
    assert 'cd "${RELEASE_DIR}/infra"' not in text
    assert 'cd "${PREV_TARGET}/infra"' not in text
    assert 'cd "${RELEASE_DIR}" && compose_up' in text
    assert 'cd "${PREV_TARGET}" && compose_up' in text


def test_dryrun_exits_before_quiescence_or_any_db_service_mutation():
    text = REMOTE.read_text(encoding="utf-8")
    dry = text.index('DEPLOY_MODE:-apply}" == "dryrun"')
    quiesce = text.index("step 1 quiesce")
    databases = text.index("step 4 databases")
    up = text.index("step 5b deploy")
    # Dry-run validates the staged candidate and exits before the maintenance
    # window, so it cannot stop services, fence/dump DBs, or need a restart.
    assert dry < quiesce < databases < up
    # MUTATED is only armed after the dry-run exit, so a dry-run leaves nothing to roll back.
    assert text.index("MUTATED=1") > dry
    dry_block = text[dry:quiesce]
    assert "OMEGA_SECRET_HYDRATION_MODE=check" in dry_block
    assert "restore_environment" not in dry_block
    assert "stop_all_writers" not in dry_block
    assert "fence_databases" not in dry_block
    assert "pg_dump" not in dry_block
    assert "resume_previous_release" not in dry_block


def test_remote_reuses_audited_safety_and_pins_by_tag():
    text = REMOTE.read_text(encoding="utf-8")
    assert "apply_db_migrations.sh" in text          # forward-only + drift guard
    assert "ghcr-auth-run.sh" in text                # server-owned pull
    assert "pull_policy: never" not in text          # remote pulls explicitly; overlay carries it
    local = LOCAL.read_text(encoding="utf-8")
    assert "pull_policy: never" in local             # the generated overlay pins it
    # Health gate is strict on version + app_env + readiness.
    assert "/healthz" in text and "/readyz" in text and "app_env" in text


def test_local_requires_provenance_and_explicit_target():
    text = LOCAL.read_text(encoding="utf-8")
    assert "^[0-9a-f]{40}$" in text                   # ref is a full commit SHA
    assert 'git rev-list -n 1 "${TARGET_TAG}"' in text  # tag must resolve to the ref
    for var in ("OMEGA_PROJECT_ID", "OMEGA_INSTANCE", "OMEGA_SOURCE_BUCKET", "OMEGA_GHCR_OWNER"):
        assert var in text


def test_local_uses_canonical_manifest_asset_checksum_and_exact_digest_lock():
    text = LOCAL.read_text(encoding="utf-8")
    assert "release_digest_env.py" in text
    assert "OMEGA_RELEASE_MANIFEST_CHECKSUM" in text
    assert '--checksum "${MANIFEST_CHECKSUM}"' in text
    assert '--repository "${OMEGA_GHCR_OWNER}/CONSOLA-V1"' in text
    assert '--build-run-id "${BUILD_RUN_ID}"' in text
    assert "canonical release manifest/checksum/schema/owner/digest" in text
    # The former permissive JSON loop selected a digest by basename and ignored
    # schema, owner, duplicate services and the published checksum asset.
    assert 'it.get("image", "").rsplit' not in text


@pytest.mark.parametrize(
    "attack",
    ["command-substitution", "semicolon", "foreign-owner", "schema", "checksum"],
)
def test_local_manifest_preflight_rejects_adversarial_assets_without_execution(
    tmp_path: Path, attack: str
):
    manifest, checksum, value = _release_assets(tmp_path)
    sentinel = tmp_path / "manifest-command-executed"
    images = value["images"]
    assert isinstance(images, list) and isinstance(images[0], dict)
    if attack == "command-substitution":
        images[0]["digest_reference"] += f"$(touch {sentinel})"
        _write_release_assets(manifest, checksum, value)
    elif attack == "semicolon":
        images[0]["digest_reference"] += f";touch {sentinel}"
        _write_release_assets(manifest, checksum, value)
    elif attack == "foreign-owner":
        manifest, checksum, value = _release_assets(
            tmp_path, repository="foreign-owner/CONSOLA-V1"
        )
    elif attack == "schema":
        value["schema_version"] = 1
        _write_release_assets(manifest, checksum, value)
    else:
        checksum.write_text(
            f"{'0' * 64}  {manifest.name}\n", encoding="ascii"
        )

    result = _run_local_manifest_preflight(tmp_path, manifest, checksum)

    assert result.returncode != 0
    assert "BLOCKED" in result.stderr or "validation failed" in result.stderr
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "owner",
    ["omega-owner$(touch should-not-run)", "omega-owner;touch-should-not-run"],
)
def test_local_rejects_shell_syntax_in_owner_before_any_tool_call(
    tmp_path: Path, owner: str
):
    manifest, checksum, _value = _release_assets(tmp_path)

    result = _run_local_manifest_preflight(
        tmp_path, manifest, checksum, owner=owner
    )

    assert result.returncode != 0
    assert "OMEGA_GHCR_OWNER" in result.stderr


@pytest.mark.parametrize(
    "margin", ["0", "4", "08", "010", "1025", "20GiB", "$(id)"]
)
def test_local_rejects_unsafe_image_pull_free_space_margin(
    tmp_path: Path, margin: str
):
    manifest, checksum, _value = _release_assets(tmp_path)

    result = _run_local_manifest_preflight(
        tmp_path,
        manifest,
        checksum,
        image_pull_min_free_gib=margin,
    )

    assert result.returncode != 0
    assert "OMEGA_IMAGE_PULL_MIN_FREE_GIB" in result.stderr


def test_source_archive_is_bound_to_checksum_and_immutable_gcs_generation():
    local = LOCAL.read_text(encoding="utf-8")
    remote = REMOTE.read_text(encoding="utf-8")
    assert "git archive --format=tar.gz" in local
    assert "SOURCE_SHA256" in local and "SOURCE_GENERATION" in local
    assert "storage objects describe" in local
    assert "--if-generation-match=0" in local
    assert '"${SOURCE_URI}#${SOURCE_GENERATION}"' in local
    assert 'generation=${SOURCE_GENERATION}' in remote
    assert 'sha256_of "${SOURCE_ARCHIVE_TMP}"' in remote
    assert ".omega-source-artifact-v1" in remote
    assert "source_receipt_matches" in remote


def test_remote_snapshots_mutable_overlay_and_verifies_the_exact_compose_copy():
    text = REMOTE.read_text(encoding="utf-8")
    lock = text.index("flock -n 8")
    snapshot = text.index(
        'cp -- "${IMAGES_OVERLAY}" "${TRUSTED_IMAGES_OVERLAY}"', lock
    )
    trusted_checksum = text.index(
        'sha256_of "${TRUSTED_IMAGES_OVERLAY}"', snapshot
    )
    candidate_copy = text.index(
        '    "${CANDIDATE_IMAGES_OVERLAY}" \\\n'
        '    "${IMAGES_OVERLAY_SHA256}"',
        trusted_checksum,
    )
    candidate_checksum = text.index(
        'sha256_of "${CANDIDATE_IMAGES_OVERLAY}"', candidate_copy
    )
    digest_parse = text.index(
        '"${CANDIDATE_IMAGES_OVERLAY}" | sort -u', candidate_checksum
    )

    assert lock < snapshot < trusted_checksum < candidate_copy
    assert candidate_copy < candidate_checksum < digest_parse
    # After the one root-owned snapshot, the caller-writable /tmp pathname is
    # never copied, parsed or handed to Compose. A racing scp can only make the
    # snapshot checksum fail; it cannot change the verified candidate later.
    assert text[snapshot:].count('"${IMAGES_OVERLAY}"') == 1
    assert 'mktemp "${APP_ROOT}/.omega-images-${DEPLOY_REF}.' in text
    assert 'chmod 0400 "${TRUSTED_IMAGES_OVERLAY}"' in text


def test_day2_renders_exact_release_gcp_template_atomically(tmp_path: Path):
    remote = REMOTE.read_text(encoding="utf-8")
    assert 'cp -f "${CURRENT}/infra/docker-compose.gcp.yml"' not in remote
    assert 'GCP_OVERLAY_TEMPLATE="${RELEASE_DIR}/infra/terraform-gcp/templates/' in remote
    assert 'python3 -I "${GCP_OVERLAY_RENDERER}"' in remote
    assert '"${GCP_OVERLAY_TEMPLATE}" "${CANDIDATE_GCP_OVERLAY}"' in remote
    assert "config --quiet --no-interpolate" in remote

    source_template = REPO / "infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl"
    changed_template = tmp_path / "docker-compose.gcp.yml.tftpl"
    marker = "# candidate-template-change-reached-day2"
    changed_template.write_text(
        source_template.read_text(encoding="utf-8") + marker + "\n",
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate" / "docker-compose.gcp.yml"
    render_file(changed_template, candidate)

    rendered = candidate.read_text(encoding="utf-8")
    assert rendered == changed_template.read_text(encoding="utf-8").replace(
        "$${", "${"
    )
    assert marker in rendered
    assert "$${" not in rendered
    assert "%{" not in rendered
    assert "${LAKEHOUSE_PROVIDER:-gcs}" in rendered
    assert candidate.stat().st_mode & 0o777 == 0o600

    before = candidate.read_bytes()
    invalid = tmp_path / "invalid.tftpl"
    invalid.write_text(
        source_template.read_text(encoding="utf-8") + "${terraform_expression}\n",
        encoding="utf-8",
    )
    with pytest.raises(RenderError, match="Terraform syntax"):
        render_file(invalid, candidate)
    assert candidate.read_bytes() == before


def test_remote_pull_keeps_validated_digests_as_quoted_positional_arguments():
    text = REMOTE.read_text(encoding="utf-8")
    assert 'awk \'$1 == "image:" && NF == 2 { print $2 }\'' in text
    assert '"${ref#${prefix}}" =~ ^[0-9a-f]{64}$' in text
    assert 'for ref in "$@"' in text
    assert 'omega-image-pull "${MISSING_DIGEST_REFS[@]}"' in text
    assert "declare -f pull_all" not in text
    assert "DIGEST_REFS=(${DIGEST_REFS[*]})" not in text


def test_remote_pull_body_does_not_execute_shell_syntax_in_arguments(tmp_path: Path):
    text = REMOTE.read_text(encoding="utf-8")
    match = re.search(
        r"bash -c '([^']+)' \\\n\s+omega-image-pull", text, flags=re.DOTALL
    )
    assert match is not None
    command = match.group(1)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "docker-argv"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$DOCKER_CAPTURE\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    sentinel = tmp_path / "shell-syntax-executed"
    attacks = [
        f"ghcr.io/omega-owner/console@sha256:{'1' * 64}$(touch {sentinel})",
        f"ghcr.io/omega-owner/airflow@sha256:{'2' * 64};touch {sentinel}",
    ]
    result = subprocess.run(
        ["bash", "-c", command, "omega-image-pull", *attacks],
        env={
            **os.environ,
            "DOCKER_CAPTURE": str(capture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()
    captured = capture.read_text(encoding="utf-8")
    assert all(attack in captured for attack in attacks)


def test_remote_command_serializes_each_environment_assignment_as_argv():
    text = LOCAL.read_text(encoding="utf-8")
    assert "remote_argv=(sudo bash -c" in text
    assert "git show \"${DEPLOY_REF}:scripts/gcp/gcp-canonical-deploy-remote.sh\"" in text
    assert 'REMOTE_DEPLOYER_SHA256="$(sha256_file "${remote_deployer}")"' in text
    assert "REMOTE_BOOTSTRAP=\"$(cat <<'OMEGA_REMOTE_BOOTSTRAP'" in text
    assert 'env "$@" bash "${trusted_deployer}"' in text
    assert 'chown root:root "${trusted_deployer}"' in text
    assert 'chmod 0500 "${trusted_deployer}"' in text
    assert "${STAGE_NONCE}" in text
    assert "printf -v remote_command '%q '" in text
    assert 'SOURCE_SHA256=${SOURCE_SHA256}' in text
    assert 'SOURCE_GENERATION=${SOURCE_GENERATION}' in text
    assert 'IMAGES_OVERLAY_SHA256=${IMAGES_OVERLAY_SHA256}' in text
    assert 'REMOTE_STAGE_PATHS=("${REMOTE_OVERLAY_STAGED}" "${REMOTE_DEPLOYER_STAGED}")' in text


def _remote_bootstrap() -> str:
    match = re.search(
        r"REMOTE_BOOTSTRAP=\"\$\(cat <<'OMEGA_REMOTE_BOOTSTRAP'\n"
        r"(?P<body>.*?)\nOMEGA_REMOTE_BOOTSTRAP\n\)\"",
        LOCAL.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group("body")


def _bootstrap_paths() -> tuple[Path, Path]:
    identity = f"{'a' * 40}-{secrets.token_hex(16)}"
    return (
        Path(f"/tmp/gcp-canonical-deploy-remote-{identity}.sh"),
        Path(f"/tmp/omega-images-{identity}.yml"),
    )


def _bootstrap_test_env(tmp_path: Path, *, racing_cp: bool) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    chown = fake_bin / "chown"
    chown.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    chown.chmod(0o755)
    if racing_cp:
        cp = fake_bin / "cp"
        cp.write_text(
            "#!/bin/sh\n"
            '"$REAL_CP" "$@"\n'
            "printf '%s\\n' '#!/bin/sh' 'touch \"$MALICIOUS_SENTINEL\"' > \"$RACE_SOURCE\"\n",
            encoding="utf-8",
        )
        cp.chmod(0o755)
    else:
        (fake_bin / "cp").unlink(missing_ok=True)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REAL_CP": "/bin/cp",
    }


def test_sudo_bootstrap_snapshots_before_race_and_rejects_bad_checksum(tmp_path: Path):
    trusted_root = tmp_path / "root"
    trusted_root.mkdir()
    bootstrap = _remote_bootstrap().replace("/opt/modecissions", str(trusted_root))

    staged, overlay = _bootstrap_paths()
    good_sentinel = tmp_path / "trusted-ran"
    malicious_sentinel = tmp_path / "raced-source-ran"
    payload = '#!/bin/sh\ntouch "$TEST_SENTINEL"\nprintf "%s" "$UNTRUSTED_VALUE" > "$VALUE_CAPTURE"\n'
    staged.write_text(payload, encoding="utf-8")
    overlay.write_text("services: {}\n", encoding="utf-8")
    expected_sha = hashlib.sha256(payload.encode()).hexdigest()
    env = _bootstrap_test_env(tmp_path, racing_cp=True)
    env.update(
        {
            "RACE_SOURCE": str(staged),
            "MALICIOUS_SENTINEL": str(malicious_sentinel),
        }
    )
    capture = tmp_path / "value"
    attack = f"$(touch {tmp_path / 'interpolation-ran'})"
    result = subprocess.run(
        [
            "bash",
            "-c",
            bootstrap,
            "omega-remote-bootstrap",
            str(staged),
            expected_sha,
            str(overlay),
            f"TEST_SENTINEL={good_sentinel}",
            f"VALUE_CAPTURE={capture}",
            f"UNTRUSTED_VALUE={attack}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert good_sentinel.exists()
    assert not malicious_sentinel.exists()
    assert capture.read_text(encoding="utf-8") == attack
    assert not (tmp_path / "interpolation-ran").exists()
    assert not staged.exists() and not overlay.exists()

    staged, overlay = _bootstrap_paths()
    rejected_sentinel = tmp_path / "bad-checksum-ran"
    staged.write_text(
        f"#!/bin/sh\ntouch {shlex.quote(str(rejected_sentinel))}\n",
        encoding="utf-8",
    )
    overlay.write_text("services: {}\n", encoding="utf-8")
    bad = subprocess.run(
        [
            "bash",
            "-c",
            bootstrap,
            "omega-remote-bootstrap",
            str(staged),
            expected_sha,
            str(overlay),
        ],
        env=_bootstrap_test_env(tmp_path, racing_cp=False),
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 75
    assert not rejected_sentinel.exists()
    assert not staged.exists() and not overlay.exists()


def test_image_pull_disk_gate_precedes_pull_and_quiesce_and_skips_when_cached():
    text = REMOTE.read_text(encoding="utf-8")
    missing = text.index('if [[ "${#MISSING_DIGEST_REFS[@]}" -gt 0 ]]')
    disk = text.index('log "preflight disk:', missing)
    pull = text.index('docker pull --quiet "$ref"', disk)
    cached = text.index("16/16 immutable digests already cached", pull)
    branch_end = text.index("\nfi", cached)
    quiesce = text.index('log "step 1 quiesce', branch_end)

    assert missing < disk < pull < cached < branch_end < quiesce
    assert "findmnt -n -T /var/lib/containerd -o TARGET" in text[missing:disk]
    assert "IMAGE_PULL_MIN_FREE_GIB:-20" in text
    assert 'omega-image-pull "${MISSING_DIGEST_REFS[@]}"' in text[missing:cached]
    # The cached else branch contains neither a disk query nor a pull.
    cached_branch = text[cached:branch_end]
    assert "findmnt" not in cached_branch
    assert "docker pull" not in cached_branch
