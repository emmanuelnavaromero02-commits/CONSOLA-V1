from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_makefile_exposes_production_readiness_and_dr_rehearsal_targets():
    makefile = _read("Makefile")
    assert "production-readiness:" in makefile
    assert "bash scripts/production_readiness.sh" in makefile
    assert "production-readiness-aws:" in makefile
    assert "OMEGA_PRODUCTION_READINESS_REMOTE=1 bash scripts/production_readiness.sh" in makefile
    assert "dr-rehearsal:" in makefile
    assert "bash scripts/run_dr_rehearsal.sh" in makefile


def test_production_readiness_gate_checks_real_runtime_surfaces():
    script = _read("scripts/production_readiness.sh")
    for needle in (
        "docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap config -q",
        "bash scripts/wait_for_health.sh",
        "/readyz?require_data=1",
        "/api/v1/security/login",
        "make test",
        "make smoke",
        "OPEN_REPORT=0 make e2e",
        "make acceptance",
        "OMEGA_STRESS_PROFILE",
        "make stress",
    ):
        assert needle in script
    assert "OMEGA_REQUIRE_LIVE_LLM=1" in script
    assert "ANTHROPIC_API_KEY is required" in script
    assert "gemini" not in script.lower()


def test_production_readiness_gate_has_remote_aws_mode():
    script = _read("scripts/production_readiness.sh")
    for needle in (
        "OMEGA_PRODUCTION_READINESS_REMOTE",
        "check_public_runtime",
        "${CONSOLE_URL}/healthz",
        "${CONSOLE_URL}/readyz",
        "/readyz?require_data=1",
        "OMEGA_REQUIRE_SUPERSET_LOGIN",
        "OMEGA_PRODUCTION_READINESS_REMOTE_RUN_E2E",
        'BASE_URL="${CONSOLE_URL}"',
        "run_remote_gate",
    ):
        assert needle in script
    remote_index = script.index('if [[ "${REMOTE_MODE}" == "1" ]]')
    docker_index = script.index("require_command docker")
    assert remote_index < docker_index, "remote AWS readiness must not require local Docker"


def test_stress_runner_has_beta_and_production_profiles_with_isolation_probe():
    script = _read("scripts/run_stress.sh")
    locust = _read("tests/stress/locustfile.py")
    assert "OMEGA_STRESS_PROFILE" in script
    assert "DEFAULT_USERS=100" in script
    assert "DEFAULT_USERS=500" in script
    assert "DEFAULT_INTERNAL_PROBES" in script
    assert "forged_workspace_isolation_probe" in locust
    assert "forged workspace returned rows" in locust


def test_dr_rehearsal_is_guarded_and_post_restore_checked():
    script = _read("scripts/run_dr_rehearsal.sh")
    assert "OMEGA_DR_REHEARSAL_EXECUTE" in script
    assert "CONFIRM_RESTORE=modecissions" in script
    assert "pg_dumpall -U postgres --clean --if-exists" in script
    assert "manifest.json" in script
    assert "make smoke" in script
    assert "/readyz?require_data=1" in script
