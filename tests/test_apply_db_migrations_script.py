"""Sprint v1.32 — existing DB volumes need an explicit migration path."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "scripts/run_db_migrations.py"
LAUNCHER_SPEC = importlib.util.spec_from_file_location("migration_launcher", LAUNCHER)
assert LAUNCHER_SPEC is not None and LAUNCHER_SPEC.loader is not None
launcher = importlib.util.module_from_spec(LAUNCHER_SPEC)
LAUNCHER_SPEC.loader.exec_module(launcher)


def test_makefile_exposes_migrate_target():
    makefile = (REPO_ROOT / "Makefile").read_text()

    phony_lines = [line for line in makefile.splitlines() if line.startswith(".PHONY:")]
    phony_text = "\n".join(phony_lines)
    for target in (
        "help",
        "up",
        "up-core",
        "down",
        "test",
        "smoke",
        "migrate",
        "rotate-keys",
        "verify-release",
    ):
        assert target in phony_text
    assert "migrate:" in makefile
    assert "scripts/run_db_migrations.py" in makefile


def test_apply_db_migrations_tracks_schema_migrations_without_secret_argv():
    script = (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()
    launcher = LAUNCHER.read_text(encoding="utf-8")
    guard = (REPO_ROOT / "scripts/migration_guard.py").read_text()

    assert "schema_migrations" in script
    assert "verified immutable snapshot" in guard
    assert "_verified_pending_sql_snapshot" in guard
    assert "99zzt_analytic_app_dataset_grants.sql" in guard
    assert "99zzu_analytic_app_manifest_registry.sql" in guard
    assert "both databases are inspected" in script
    assert "checksum_manifest_sha256" in guard
    assert "checksum_evidence_kind" in guard
    assert "checksum_guarded_at" in guard
    assert "baseline_expected" in guard
    assert "guarded_transaction" in guard
    assert "PSQL_GOLD" in script
    assert "docker compose" in script
    assert 'source "${ENV_FILE}"' not in script
    assert "PGOPTIONS=" not in script
    assert "PASSWORD:-" not in script
    assert "env-validate" in script
    assert "--forbid-prefix OMEGA_MIGRATION_" in script
    assert '--env-file "$ENV_FILE"' in script
    assert "COMPOSE_DISABLE_ENV_FILE=1" in script
    assert 'exec -T -e "PGOPTIONS=' not in script
    assert "verify-release-attestation" in script
    assert "live GCS generation verified" in guard
    assert "--exclude .omega-release.json --require-read-only" in script
    assert "OMEGA_MIGRATION_RELEASE_ATTESTATION" in script
    assert "local migration candidate must be the existing exact Git HEAD" in script
    assert "clean relevant Git tree" in script
    assert "verify-read-only-mount" in script
    assert (
        'docker exec -i -e "PGAPPNAME=$MIGRATION_RUN_ID" '
        '"$POSTGRES_CONTAINER_ID"' in script
    )
    assert (
        'docker exec -i -e "PGAPPNAME=$MIGRATION_RUN_ID" '
        '"$POSTGRES_GOLD_CONTAINER_ID"' in script
    )
    backend_control = (REPO_ROOT / "scripts/migration_backend_control.py").read_text(
        encoding="utf-8"
    )
    assert "pg_terminate_backend" in backend_control
    assert "application_name = :'run_id'" in backend_control
    assert 'python3 "$BACKEND_CONTROL"' in script
    assert '"${COMPOSE[@]}" exec -T' not in script
    assert "container changed while binding the migration pair" in script
    assert "is still in a temporary or unexpected entrypoint" in script
    assert "cat /proc/1/comm" in script
    assert "pwd -P" in script
    assert 'DAY2_LOCK="/var/lock/omega-gcp-day2.lock"' in script
    assert 'readlink -f "/proc/$$/fd/9"' in script
    assert "/usr/bin/flock -w 30 9" in script
    assert '"/usr/bin/env"' in launcher
    assert '"-i"' in launcher
    assert '"/bin/bash"' in launcher
    assert '"--noprofile"' in launcher
    assert '"--norc"' in launcher
    assert "start_new_session=True" in launcher
    assert "TIMEOUT_SECONDS" in launcher
    assert "BASH_ENV" not in launcher


def test_launcher_injects_receipt_authority_without_accepting_a_host_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attacker = tmp_path / "attacker"
    canonical = Path(
        "/opt/modecissions/shared/operation-receipts/migration-"
        + "a" * 40
        + "-20260812T010203Z-123"
    )
    monkeypatch.setenv("OMEGA_MIGRATION_RECEIPT_DIR", str(attacker))
    environment = launcher._closed_environment(
        REPO_ROOT, canonical, "omega_migration_123_456"
    )
    assert environment["OMEGA_MIGRATION_RECEIPT_DIR"] == str(canonical)
    assert str(attacker) not in environment.values()
    assert environment["OMEGA_MIGRATION_RUN_ID"] == "omega_migration_123_456"


def test_launcher_writes_a_canonical_immutable_intent(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        launcher._write_receipt_intent(
            descriptor,
            candidate="a" * 40,
            version="1.45.207-beta",
            manifest_sha="b" * 64,
            run_id="omega_migration_123_456",
        )
    finally:
        os.close(descriptor)
    intent = tmp_path / "00-launcher-intent.json"
    assert intent.stat().st_mode & 0o777 == 0o400
    raw = intent.read_bytes()
    assert raw.endswith(b"\n")
    payload = json.loads(raw)
    assert payload == {
        "candidate_ref": "a" * 40,
        "database_commit_state": "none",
        "exit_code": 0,
        "operation": "gcp_day2_database_migration",
        "phase": "launcher_intent",
        "recorded_at": payload["recorded_at"],
        "release_manifest_sha256": "b" * 64,
        "release_version": "1.45.207-beta",
        "run_id": "omega_migration_123_456",
        "schema_version": 1,
        "status": "IN_PROGRESS",
    }


def test_launcher_persistently_fences_any_prior_candidate_attempt(
    tmp_path: Path,
) -> None:
    candidate = "a" * 40
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        launcher._assert_candidate_receipt_absent(descriptor, candidate)
        prior = tmp_path / f"migration-{candidate}-20260812T010203Z-123"
        prior.mkdir()
        with pytest.raises(SystemExit, match="requires canonical reconciliation"):
            launcher._assert_candidate_receipt_absent(descriptor, candidate)
    finally:
        os.close(descriptor)


def test_launcher_acquires_and_reuses_exact_inherited_day2_lock(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "omega-gcp-day2.lock"
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    expected_uid = os.geteuid()
    expected_gid = os.getegid()
    descriptor = 77
    first = launcher._acquire_day2_lock(
        lock,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        descriptor=descriptor,
        timeout_seconds=0.1,
    )
    assert first == descriptor
    assert os.get_inheritable(first)
    try:
        first_info = os.fstat(first)
        second = launcher._acquire_day2_lock(
            lock,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            descriptor=descriptor,
            timeout_seconds=0.1,
        )
        assert second == first
        second_info = os.fstat(second)
        assert (first_info.st_dev, first_info.st_ino) == (
            second_info.st_dev,
            second_info.st_ino,
        )
    finally:
        os.close(first)


def test_launcher_rejects_an_unrelated_inherited_fd9(tmp_path: Path) -> None:
    lock = tmp_path / "omega-gcp-day2.lock"
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    unrelated = tmp_path / "unrelated"
    unrelated.touch(mode=0o600)
    unrelated.chmod(0o600)
    test_descriptor = 77
    descriptor = os.open(unrelated, os.O_RDWR)
    os.dup2(descriptor, test_descriptor)
    if descriptor != test_descriptor:
        os.close(descriptor)
    try:
        with pytest.raises(SystemExit, match="FD 9 is not the canonical"):
            launcher._acquire_day2_lock(
                lock,
                expected_uid=os.geteuid(),
                expected_gid=os.getegid(),
                descriptor=test_descriptor,
                timeout_seconds=0.1,
            )
    finally:
        os.close(test_descriptor)


def test_shell_persists_every_partial_database_transition_in_order() -> None:
    script = (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()
    markers = (
        "05-runner-started.json",
        "10-preflight-passed.json",
        "15-gold-commit-intent.json",
        "20-gold-committed.json",
        "25-operational-commit-intent.json",
        "30-operational-committed.json",
        "40-operational-authority-committed.json",
        "50-gold-authority-committed.json",
        "60-postflight-passed.json",
    )
    positions = [script.rindex(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "90-terminal.json" in script
    assert "gold_committed_operational_pending" in script
    assert "both_databases_committed_authority_pending" in script
    assert "database_authority_committed_postflight_pending" in script
    assert "set -o noclobber" in script
    assert 'fsync-file "$destination"' in script
    assert 'fsync-dir "$RECEIPT_DIR"' in script


def test_launcher_strips_hostile_shell_and_python_environment(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    bash_env = tmp_path / "bash-env"
    bash_env.write_text(f"touch {marker}\n", encoding="utf-8")
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    environment = {
        **os.environ,
        "BASH_ENV": str(bash_env),
        "ENV": str(bash_env),
        "PYTHONPATH": str(tmp_path),
        "PYTHONSTARTUP": str(bash_env),
        "DOCKER_HOST": "tcp://attacker.invalid:2375",
        "OMEGA_GCP_SAFE_IO": str(tmp_path / "attacker.py"),
        "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
        "OMEGA_MIGRATION_ENV_FILE": str(tmp_path / "absent.env"),
        "OMEGA_MIGRATION_ENVIRONMENT": "test",
    }
    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not marker.exists()
    assert "attacker.invalid" not in result.stderr


def test_launcher_forwards_termination_and_reaps_its_private_process_group(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied_launcher = scripts / LAUNCHER.name
    shutil.copy2(LAUNCHER, copied_launcher)
    copied_launcher.write_text(
        copied_launcher.read_text(encoding="utf-8").replace(
            "TERMINATION_GRACE_SECONDS = 60",
            "TERMINATION_GRACE_SECONDS = 1",
        ),
        encoding="utf-8",
    )
    copied_launcher.chmod(0o500)
    child_pid = tmp_path / "child.pid"
    runner = scripts / "apply_db_migrations.sh"
    runner.write_text(
        "#!/bin/bash\n"
        "set -Eeuo pipefail\n"
        f"printf '%s' \"$$\" > {child_pid!s}\n"
        "trap '' TERM\n"
        "while :; do /bin/sleep 1; done\n",
        encoding="utf-8",
    )
    runner.chmod(0o500)
    process = subprocess.Popen(
        [str(copied_launcher)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while not child_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child_pid.exists()
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    assert process.returncode == 128 + signal.SIGTERM
    pid = int(child_pid.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_launcher_cannot_orphan_a_child_when_signaled_inside_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded: list[int] = []

    class Spawned:
        pid = 424242

        def wait(self, *, timeout: float | None = None) -> int:
            del timeout
            return 0

    def spawn(*_args: object, **_kwargs: object) -> Spawned:
        os.kill(os.getpid(), signal.SIGTERM)
        return Spawned()

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        launcher,
        "_terminate_process_group",
        lambda _process, signum: forwarded.append(signum),
    )
    assert launcher._run_bounded(["ignored"], pass_fds=()) == 128 + signal.SIGTERM
    assert forwarded == [signal.SIGTERM]


def test_apply_db_migrations_rejects_control_plane_or_executable_dotenv(
    tmp_path: Path,
):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OMEGA_MIGRATION_CANDIDATE_REF=" + "1" * 40 + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    environment = {
        **os.environ,
        "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
        "OMEGA_MIGRATION_ENV_FILE": str(env_file),
        "OMEGA_MIGRATION_ENVIRONMENT": "test",
    }
    forbidden = subprocess.run(
        [str(LAUNCHER)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert forbidden.returncode != 0
    assert "forbidden control-plane prefix" in forbidden.stderr

    marker = tmp_path / "must-not-exist"
    env_file.write_text(f"SAFE_VALUE=$(touch {marker})\n", encoding="utf-8")
    executable = subprocess.run(
        [str(LAUNCHER)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert executable.returncode != 0
    assert "executable/interpolated syntax" in executable.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    "flag",
    (
        "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT",
        "OMEGA_MIGRATION_BOOTSTRAP_MODE",
        "OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER",
    ),
)
def test_apply_db_migrations_requires_strict_boolean_flags(
    tmp_path: Path, flag: str
) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    environment = {
        **os.environ,
        "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
        "OMEGA_MIGRATION_ENV_FILE": str(tmp_path / "absent.env"),
        "OMEGA_MIGRATION_ENVIRONMENT": "test",
        flag: "true",
    }
    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 13
    assert f"{flag} must be exactly 0 or 1" in result.stderr


def test_local_fallback_rejects_a_well_formed_but_false_candidate(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    environment = {
        **os.environ,
        "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
        "OMEGA_MIGRATION_ENV_FILE": str(tmp_path / "absent.env"),
        "OMEGA_MIGRATION_ENVIRONMENT": "test",
        "OMEGA_MIGRATION_CANDIDATE_REF": "1" * 40,
    }
    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 14
    assert "existing exact Git HEAD" in result.stderr


def test_local_fallback_rejects_relevant_untracked_tree_drift(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    candidate = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    marker = REPO_ROOT / "infra/migrations/manifests/omega_migration_dirty_probe.json"
    assert not marker.exists()
    try:
        marker.write_text("must be detected\n", encoding="utf-8")
        environment = {
            **os.environ,
            "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
            "OMEGA_MIGRATION_ENV_FILE": str(tmp_path / "absent.env"),
            "OMEGA_MIGRATION_ENVIRONMENT": "test",
            "OMEGA_MIGRATION_CANDIDATE_REF": candidate,
        }
        result = subprocess.run(
            [str(LAUNCHER)],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        marker.unlink(missing_ok=True)
    assert result.returncode == 14
    assert "clean relevant Git tree" in result.stderr


def test_current_symlink_resolves_to_the_physical_release_root(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.symlink_to(REPO_ROOT, target_is_directory=True)
    candidate = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    manifests = REPO_ROOT / "infra/migrations/manifests"
    environment = {
        **os.environ,
        "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT": "1",
        "OMEGA_MIGRATION_COMPOSE_PROJECT_NAME": "infra",
        "OMEGA_MIGRATION_BOOTSTRAP_MODE": "0",
        "OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER": "0",
        "OMEGA_MIGRATION_OLD_REF": "6b12883c5b5ea0537120279ccbee4947137998a2",
        "OMEGA_MIGRATION_CANDIDATE_REF": candidate,
        "OMEGA_MIGRATION_RELEASE_VERSION": "1.45.207-beta",
        "OMEGA_MIGRATION_BASELINE_MANIFEST": str(
            manifests / "gcp-live-6b12883c5b5ea0537120279ccbee4947137998a2.json"
        ),
        "OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256": (
            "b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18"
        ),
        "OMEGA_MIGRATION_RELEASE_MANIFEST": str(manifests / "v1.45.207-beta.json"),
        "OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256": (
            "79a607d045b853fba26812311b930b21d84e676d6cbde43f05ad4ec8150700b2"
        ),
        "OMEGA_MIGRATION_RELEASE_ATTESTATION": str(REPO_ROOT / ".omega-release.json"),
        "OMEGA_MIGRATION_ENV_FILE": str(tmp_path / "absent.env"),
    }
    result = subprocess.run(
        [str(current / "scripts/run_db_migrations.py")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 14
    assert "immutable production release tree verification failed" in result.stderr
    assert "must be ROOT/.omega-release.json" not in result.stderr


def test_gold_role_migration_exists_for_fresh_gold_volumes():
    sql = (REPO_ROOT / "infra/init_gold/34_postgres_gold_role.sql").read_text()

    assert "CREATE ROLE omega_refinement_gold" in sql
    assert "GRANT USAGE, CREATE ON SCHEMA public TO omega_refinement_gold" in sql
    assert "app.omega_refinement_gold_password" in sql


def test_migration_46_picked_up_by_runner_glob():
    """v1.43.3: the runner uses ``infra/init/[0-9][0-9]_*.sql`` and
    bash globs lexicographically. Migration 46 must be the strictly
    largest filename under that pattern after this hotfix lands, so
    fresh boots apply 45's CASCADE→RESTRICT swap BEFORE 46 fixes the
    jobs ownership that 45 doesn't touch. If anyone later adds a 47+
    that re-touches jobs, this test still passes — we only assert
    "46 follows 45".
    """
    init = REPO_ROOT / "infra/init"
    matched = sorted(p.name for p in init.glob("[0-9][0-9]_*.sql"))
    assert "45_cascade_to_restrict.sql" in matched
    assert "46_sap_jobs_permissions.sql" in matched
    assert matched.index("45_cascade_to_restrict.sql") < matched.index(
        "46_sap_jobs_permissions.sql"
    )
