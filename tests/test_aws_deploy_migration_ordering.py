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
