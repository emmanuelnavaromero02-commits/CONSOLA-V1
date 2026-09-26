from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _runtime_service_loop(src: str) -> str:
    for line in src.splitlines():
        if line.startswith("for service in console workspace refinement"):
            return line
    raise AssertionError("runtime service loop is missing from deploy_main_aws.py")


def test_airflow_scheduler_is_not_a_runtime_service():
    loop = _runtime_service_loop(_read("scripts/deploy_main_aws.py"))
    assert "airflow " in loop
    assert "airflow-scheduler" not in loop


def test_image_pull_carries_server_owned_ghcr_auth():
    src = _read("scripts/deploy_main_aws.py")
    pull = next(
        line for line in src.splitlines() if "docker compose $COMPOSE_FILES pull" in line
    )
    assert "OMEGA_GHCR_AUTH_ACTIVE=1" in pull
    assert "DOCKER_CONFIG=/root/.docker" in pull


def test_app_is_stopped_between_pull_and_migrations():
    src = _read("scripts/deploy_main_aws.py")
    pull = src.index("docker compose $COMPOSE_FILES pull")
    stop = src.index("docker compose $COMPOSE_FILES stop")
    migrations = src.index("apply_db_migrations.sh")
    assert pull < stop < migrations
    assert 'emit "stop app before migrations"' in src


def test_migrations_run_under_a_deterministic_locale():
    src = _read("scripts/deploy_main_aws.py")
    migration_call = next(
        line
        for line in src.splitlines()
        if 'bash "$DEPLOY_DIR/apply_db_migrations.sh"' in line
    )
    assert "LC_ALL=C" in migration_call
    assert "LANG=C" in migration_call


def _remote_script() -> str:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import deploy_main_aws

    return deploy_main_aws._remote_deploy_script(
        artifact_bucket="b", artifact_key="k", artifact_sha256="0" * 64,
        deploy_ref="a" * 40, image_tag="v0.0.0", version="0.0.0",
        run_migrations=False, images_overlay_b64="", images_overlay_sha256="0" * 64,
    )


def test_required_env_preflight_runs_before_the_host_changes():
    script = _remote_script()
    extracted = script.index('emit "artifact extracted"')
    preflight = script.index('required_env_preflight "$release_dir/infra/terraform/deploy"')
    sync = script.index("<<'PYSYNC'")
    version = script.index('> "$REPO_DIR/VERSION"')
    set_ref = script.index("set_env_value DEPLOY_REF")
    late_config = script.index('emit "compose config" "PASS"')
    assert extracted < preflight < sync < version < set_ref < late_config
    gate = script[preflight:sync]
    assert gate.count("exit 30") == 2
    assert 'emit "required env preflight" "PASS"' in gate
    assert "(names only)" in gate


FAKE_COMPOSE = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_LOG"
env_file=""
args=("$@")
for i in "${!args[@]}"; do
  if [[ "${args[$i]}" == "--env-file" ]]; then env_file="${args[$((i + 1))]}"; fi
done
if [[ "${FAKE_MODE:-names}" == "broken" ]]; then
  echo "services.console.ports contains an invalid type" >&2
  exit 15
fi
for name in $FAKE_REQUIRED; do
  if ! grep -Eq "^${name}=.+" "$env_file"; then
    echo "error while interpolating services.x.environment.${name}: required variable ${name} is missing a value: ${name} is required" >&2
    exit 1
  fi
done
"""


def _run_preflight(tmp_path, *, env_text: str, required: str, mode: str = "names"):
    import subprocess

    script = _remote_script()
    helpers = script[script.index("env_value() {"):script.index("set_env_value() {")]
    function = script[
        script.index("required_env_preflight() {"):script.index("<<'PYSYNC'")
    ].rsplit("python3 - ", 1)[0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_COMPOSE, encoding="utf-8")
    docker.chmod(0o755)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / ".env").write_text(env_text, encoding="utf-8")
    release = tmp_path / "release" / "infra" / "terraform" / "deploy"
    release.mkdir(parents=True)
    for name in ("docker-compose.aws.yml", "docker-compose.cartridges.yml"):
        (release / name).write_text("services: {}\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    harness = (
        "set -euo pipefail\n"
        "emit() { printf 'CHECK\\t%s\\t%s\\t%s\\n' \"$1\" \"$2\" \"${3:-}\"; }\n"
        f"DEPLOY_DIR={deploy}\nworkdir={work}\nrelease_dir={tmp_path / 'release'}\n"
        f"{helpers}\n{function}\necho reached-sync\n"
    )
    log = tmp_path / "docker.log"
    result = subprocess.run(
        ["bash", "-c", harness],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_LOG": str(log),
             "FAKE_REQUIRED": required, "FAKE_MODE": mode},
        text=True, capture_output=True, check=False,
    )
    return result, deploy, release, work, log


def test_required_env_preflight_names_every_missing_variable_without_values(tmp_path):
    secret = "super-secret-value-123"
    env_text = f"JWT_SECRET_KEY={secret}\nDEPLOY_CARTRIDGES_SAME_HOST=true\nAIRFLOW_SECRET_KEY=\n"
    result, deploy, release, work, log = _run_preflight(
        tmp_path, env_text=env_text,
        required="JWT_SECRET_KEY AIRFLOW_FERNET_KEY AIRFLOW_SECRET_KEY",
    )
    assert result.returncode == 30, result.stderr
    assert "reached-sync" not in result.stdout
    assert (
        "required env preflight\tFAIL\thost .env lacks required variables: "
        "AIRFLOW_FERNET_KEY,AIRFLOW_SECRET_KEY (names only)"
    ) in result.stdout
    assert secret not in result.stdout + result.stderr
    assert (deploy / ".env").read_text(encoding="utf-8") == env_text
    assert list(work.iterdir()) == []
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    assert calls[0].startswith(f"compose --project-directory {deploy} --env-file {work}/")
    assert calls[0].endswith(
        f"-f {release}/docker-compose.aws.yml -f {release}/docker-compose.cartridges.yml config --quiet"
    )


def test_required_env_preflight_passes_and_reports_other_errors_generically(tmp_path):
    env_text = "AIRFLOW_FERNET_KEY=present\nDEPLOY_CARTRIDGES_SAME_HOST=false\n"
    (tmp_path / "ok").mkdir()
    (tmp_path / "broken").mkdir()
    ok, _deploy, _release, _work, log = _run_preflight(
        tmp_path / "ok", env_text=env_text, required="AIRFLOW_FERNET_KEY"
    )
    assert ok.returncode == 0, ok.stderr
    assert "required env preflight\tPASS" in ok.stdout
    assert "reached-sync" in ok.stdout
    assert "docker-compose.cartridges.yml" not in log.read_text(encoding="utf-8")

    broken, _deploy, _release, work, _log = _run_preflight(
        tmp_path / "broken", env_text=env_text, required="", mode="broken"
    )
    assert broken.returncode == 30
    assert "compose config error; run docker compose config --quiet on the host" in broken.stdout
    assert "invalid type" not in broken.stdout
    assert list(work.iterdir()) == []
