from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_makefile_exposes_production_readiness_and_dr_rehearsal_targets():
    makefile = _read("Makefile")
    assert "production-readiness:" in makefile
    assert "bash scripts/production_readiness.sh" in makefile
    assert "v1-live-readiness:" in makefile
    assert "OMEGA_PRODUCTION_READINESS_V1=1 bash scripts/production_readiness.sh" in makefile
    assert "production-readiness-aws:" in makefile
    assert "OMEGA_PRODUCTION_READINESS_REMOTE=1 bash scripts/production_readiness.sh" in makefile
    assert "dr-rehearsal:" in makefile
    assert "bash scripts/run_dr_rehearsal.sh" in makefile
    assert "multiuser-simulation:" in makefile
    assert "bash scripts/run_multiuser_isolation_simulation.sh" in makefile
    assert "$(PYTEST) --import-mode=importlib cartridges -q" in makefile


def test_production_readiness_gate_checks_real_runtime_surfaces():
    script = _read("scripts/production_readiness.sh")
    for needle in (
        "docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap config -q",
        "bash scripts/wait_for_health.sh",
        "/readyz?require_data=1",
        "/api/v1/security/login",
        "make test",
        "make smoke",
        "prepare_local_browser_e2e_env",
        "OPEN_REPORT=0 make e2e",
        "make acceptance",
        "run_scope_regression_tests",
        "vault/tests/test_vault_workspace_scope.py",
        "tests/test_intelligence_engine_contract.py",
        "tests/test_operational_native_rls.py",
        "OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM",
        "scripts/run_multiuser_isolation_simulation.sh",
        "OMEGA_STRESS_PROFILE",
        "make stress",
    ):
        assert needle in script
    assert "OMEGA_REQUIRE_LIVE_LLM=1" in script
    assert "ANTHROPIC_API_KEY is required" in script
    assert "gemini" not in script.lower()
    local_gate = script[script.index('if [[ "${REMOTE_MODE}" == "1" ]]'):]
    assert local_gate.index("make acceptance") < local_gate.index("check_readyz_data")
    assert local_gate.index("prepare_local_browser_e2e_env") < local_gate.index("OPEN_REPORT=0 make e2e")


def test_production_readiness_e2e_uses_host_published_service_urls():
    script = _read("scripts/production_readiness.sh")
    for needle in (
        "AIRFLOW_URL=\"${OMEGA_E2E_AIRFLOW_URL:-http://127.0.0.1:8082}\"",
        "SUPERSET_URL=\"${OMEGA_E2E_SUPERSET_URL:-http://127.0.0.1:8088}\"",
        "MINIO_CONSOLE_URL=\"${OMEGA_E2E_MINIO_CONSOLE_URL:-http://127.0.0.1:9001}\"",
        "MAILHOG_URL=\"${OMEGA_E2E_MAILHOG_URL:-http://127.0.0.1:8025}\"",
        "HUBSPOT_URL=\"${OMEGA_E2E_HUBSPOT_URL:-http://127.0.0.1:8210}\"",
        "REPLICON_URL=\"${OMEGA_E2E_REPLICON_URL:-http://127.0.0.1:8201}\"",
        "SAP_HCM_URL=\"${OMEGA_E2E_SAP_HCM_URL:-http://127.0.0.1:8202}\"",
        "SAP_SF_URL=\"${OMEGA_E2E_SAP_SF_URL:-http://127.0.0.1:8203}\"",
        "SAP_S4_URL=\"${OMEGA_E2E_SAP_S4_URL:-http://127.0.0.1:8204}\"",
    ):
        assert needle in script


def test_v1_live_readiness_requires_no_skips_multiuser_stress_and_llm():
    script = _read("scripts/production_readiness.sh")
    for needle in (
        "OMEGA_PRODUCTION_READINESS_V1",
        "v1 live readiness refuses OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1",
        'export OMEGA_REQUIRE_LIVE_LLM="${OMEGA_REQUIRE_LIVE_LLM:-1}"',
        'export OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM="${OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM:-1}"',
        'export OMEGA_STRESS_REQUIRE_LIVE_LLM="${OMEGA_STRESS_REQUIRE_LIVE_LLM:-1}"',
        "BLOCKED: ANTHROPIC_API_KEY is required for OMEGA_PRODUCTION_READINESS_V1=1",
        "BLOCKED: E2E_ADMIN_PASSWORD or TEST_PASSWORD is required for OMEGA_PRODUCTION_READINESS_V1=1",
        "require_intelligence=1",
        "docker exec mode_console python",
    ):
        assert needle in script


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
        "docker exec mode_console python",
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
    assert "OMEGA_STRESS_REQUIRE_LIVE_LLM" in script
    assert "OMEGA_STRESS_REQUIRE_LIVE_LLM" in locust
    assert "/api/copilot/drafts/generate" in locust
    assert "copilot:live-llm-probe" in locust
    assert "LIVE_LLM_PROBE_COMPLETE" in locust
    assert "forged_workspace_isolation_probe" in locust
    assert "forged workspace returned rows" in locust


def test_multiuser_isolation_simulation_exercises_api_and_direct_rls():
    shell = _read("scripts/run_multiuser_isolation_simulation.sh")
    script = _read("scripts/run_multiuser_isolation_simulation.py")
    for needle in (
        "OMEGA_MULTIUSER_SIM_ADMINS",
        "OMEGA_MULTIUSER_SIM_CONCURRENT_OPS",
        "CONSOLE_URL",
        "DATABASE_URL",
    ):
        assert needle in shell
    for needle in (
        "INSERT INTO tenants",
        "INSERT INTO workspaces",
        "INSERT INTO users",
        "INSERT INTO intelligence_signals",
        "INSERT INTO evidence_packs",
        "INSERT INTO vault_entries",
        "INSERT INTO pipeline_runs",
        "/api/intelligence/signals",
        "/api/v1/intelligence/run",
        "/api/decisions",
        "omega_workspace",
        "omega_mcp_infra",
        "omega_vault",
        "set_config('app.tenant_id'",
        "SimulationFailure",
    ):
        assert needle in script


def test_dr_rehearsal_is_guarded_and_post_restore_checked():
    script = _read("scripts/run_dr_rehearsal.sh")
    assert "OMEGA_DR_REHEARSAL_EXECUTE" in script
    assert "CONFIRM_RESTORE=modecissions" in script
    assert "pg_dumpall -U postgres --clean --if-exists" in script
    assert "manifest.json" in script
    assert "make smoke" in script
    assert "/readyz?require_data=1" in script
    assert "OMEGA_REQUIRE_LIVE_LLM" in script
    assert "ANTHROPIC_API_KEY is required" in script
    assert "checking live Anthropic chat path after restore" in script
    assert "gemini" not in script.lower()
