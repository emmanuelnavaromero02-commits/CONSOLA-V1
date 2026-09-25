"""Sprint v1.23 — `make smoke` is real now (audit finding B3).

These tests don't BOOT the stack (the test environment has no Docker
daemon). They verify that `scripts/smoke_test.sh` exists, is shaped
correctly, and that `make smoke` actually runs it instead of being the
v1.7 exit-1 stub. The script itself is shellcheck-clean and gets a
syntax check from bash via `bash -n` in this same test file.

The "v1.19 vault_entries partitioning" check inside the script is
called out by name (`test_smoke_script_verifies_vault_isolation`) so a
future refactor that drops that line fails the test loudly — the
keystone defense-in-depth assertion must not regress silently.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT    = REPO_ROOT / "scripts" / "smoke_test.sh"
MAKEFILE  = REPO_ROOT / "Makefile"
RUN_E2E   = REPO_ROOT / "scripts" / "run-e2e.sh"
WAIT_HEALTH = REPO_ROOT / "scripts" / "wait_for_health.sh"


# ── File-level shape ────────────────────────────────────────────────


def test_smoke_script_exists():
    assert SCRIPT.is_file(), f"missing: {SCRIPT}"


def test_smoke_script_is_executable():
    """If the file isn't +x, `make smoke` works via `bash scripts/...`
    but a developer running it directly hits a permission error. Keep
    the executable bit so both paths work."""
    mode = SCRIPT.stat().st_mode
    assert mode & 0o111, (
        f"{SCRIPT} is not executable (mode={oct(mode)}). "
        f"Run `chmod +x {SCRIPT.relative_to(REPO_ROOT)}`."
    )


def test_smoke_script_has_bash_shebang():
    """The script uses bashisms (parameter expansion ${var//re/repl},
    arithmetic, [[ ]]) — POSIX sh would explode. Pin the shebang."""
    first_line = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!"), f"missing shebang: {first_line!r}"
    assert "bash" in first_line, (
        f"shebang must invoke bash (uses bash-only features); got: {first_line!r}"
    )


def test_smoke_script_uses_set_euo_pipefail():
    """Without `set -euo pipefail` a failing curl quietly continues and
    the operator sees `ALL CHECKS PASSED` when nothing was checked."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^set\s+-euo\s+pipefail\b", body, re.MULTILINE), (
        "script must `set -euo pipefail` near the top to fail-fast on "
        "unexpected errors"
    )


def test_smoke_script_passes_bash_syntax_check():
    """Catches typos, unmatched quotes, broken heredocs, etc. — without
    booting any containers."""
    if not shutil.which("bash"):
        # CI environment without bash — skip rather than fail.
        import pytest
        pytest.skip("bash not available in this environment")
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    # Python subprocess can SIGSEGV on macOS arm64 with Homebrew Python 3.12
    # when spawning bash. This is a Python runtime quirk unrelated to the
    # smoke script. CI on Linux always exercises this path correctly.
    if result.returncode < 0:
        import pytest
        pytest.skip(
            f"Python subprocess crashed with signal {-result.returncode} "
            f"spawning bash (host runtime quirk, not a script defect). "
            f"This path is covered by Linux CI."
        )

    assert result.returncode == 0, (
        f"bash -n rejected the smoke script:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "script",
    (
        "infra/bootstrap-keys.sh",
        "scripts/aws-entrypoint.sh",
        "scripts/wait_for_health.sh",
        "scripts/smoke_test.sh",
    ),
)
def test_release_gate_scripts_pass_bash_syntax_check(script: str):
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / script)],
        capture_output=True,
        text=True,
    )
    if result.returncode < 0:
        pytest.skip(
            f"Python subprocess crashed with signal {-result.returncode} "
            f"spawning bash (host runtime quirk, not a script defect). "
            f"This path is covered by Linux CI."
        )
    assert result.returncode == 0, f"bash -n rejected {script}:\n{result.stderr}"


# ── Coverage of the 5 app services ──────────────────────────────────


SERVICES = ("console", "workspace", "refinement", "vault", "mcp-infra")


def test_smoke_script_checks_5_services():
    body = SCRIPT.read_text(encoding="utf-8")
    missing = [s for s in SERVICES if s not in body]
    assert not missing, (
        f"smoke script doesn't mention service(s): {missing}. "
        f"The v1.21 healthcheck contract covers all 5 — the smoke "
        f"check must cover all 5 too."
    )


def test_smoke_script_targets_each_services_canonical_port():
    """Catches a port-swap bug (e.g. probing vault on refinement's 8500
    would silently pass against a healthy refinement and miss vault
    being down)."""
    body = SCRIPT.read_text(encoding="utf-8")
    expected_ports = {
        "console":    8000,
        "workspace":  8001,
        "mcp-infra":  8010,
        "vault":      8300,
        "refinement": 8500,
    }
    for svc, port in expected_ports.items():
        # Match the `service:port` pair that the loop iterates over,
        # OR the `:port/healthz` form for any inline checks.
        pat1 = f"{svc}:{port}"
        pat2 = f":{port}/healthz"
        assert pat1 in body or pat2 in body, (
            f"smoke script doesn't probe {svc} on canonical port {port}"
        )


# ── v1.19 vault_entries partitioning regression guard ──────────────


def test_smoke_script_verifies_vault_entries_partitioning():
    """Sprint v1.19 split Postgres into per-service roles; the keystone
    invariant is that ONLY omega_vault touches vault_entries. The smoke
    script must verify both halves of the partition:
      (a) omega_vault HAS SELECT on vault_entries
      (b) omega_console is DENIED SELECT on vault_entries
    A future refactor that drops either probe fails this test."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert re.search(
        r"has_table_privilege\(\s*'omega_vault',\s*'vault_entries'",
        body,
    ), (
        "smoke script must probe `has_table_privilege('omega_vault', "
        "'vault_entries', ...)` — the positive half of the v1.19 partition"
    )
    assert re.search(
        r"has_table_privilege\(\s*'omega_console',\s*'vault_entries'",
        body,
    ), (
        "smoke script must probe `has_table_privilege('omega_console', "
        "'vault_entries', ...)` — the negative half of the v1.19 partition "
        "(regression guard if a future GRANT leaks the table)"
    )


def test_smoke_script_checks_auth_gate():
    """An anonymous POST to a protected endpoint must be rejected.
    After v1.22, CSRF runs before auth on cookie paths, so the code is
    403 instead of 401. The smoke script accepts BOTH because both are
    legitimate rejections of an unauthenticated request."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "/monitoring/invoke" in body, (
        "smoke script must probe a protected POST endpoint to verify "
        "the auth gate; expected /monitoring/invoke"
    )
    # Accept either 401 or 403 — the spec used 401 but post-v1.22 the
    # actual code is 403 (CSRF fires first). The script's case statement
    # handles both.
    assert re.search(r"\b401\b", body) and re.search(r"\b403\b", body), (
        "auth-gate check must accept BOTH 401 and 403 as 'rejected' "
        "outcomes — CSRF-first vs auth-first depends on which dep wins "
        "the race"
    )


def test_smoke_script_uses_curl_timeouts():
    """A hung service must not block the whole smoke run forever. Curl
    needs an explicit --max-time."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "--max-time" in body, (
        "smoke script must pass --max-time to curl so a hung service "
        "can't stall the run"
    )


# ── Makefile wiring ─────────────────────────────────────────────────


def test_makefile_smoke_target_runs_the_script():
    body = MAKEFILE.read_text(encoding="utf-8")
    # Find the body of the `smoke:` target — everything between `smoke:`
    # and the next blank line or next target header.
    m = re.search(r"^smoke:\s*$([\s\S]+?)(?=^\S|\Z)", body, re.MULTILINE)
    assert m, "smoke target not found in Makefile"
    smoke_body = m.group(1)
    assert "scripts/smoke_test.sh" in smoke_body, (
        f"smoke target doesn't invoke scripts/smoke_test.sh:\n{smoke_body}"
    )
    # And explicitly NOT the v1.7 honest stub form
    assert "not implemented" not in smoke_body.lower(), (
        "smoke target still contains the 'not implemented' stub — "
        "v1.23 should have replaced it with the script call"
    )


def test_makefile_help_describes_smoke_as_real():
    """The `make help` output should advertise smoke as a real target."""
    body = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(r'@echo\s+"\s*make smoke[^"]*"', body)
    assert m, "make help has no `make smoke` line"
    desc = m.group(0).lower()
    assert "not implemented" not in desc, (
        f"help text still claims smoke is unimplemented: {m.group(0)}"
    )


def test_makefile_up_starts_full_sap_profile():
    body = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(r"^up:\s*$([\s\S]+?)(?=^\S|\Z)", body, re.MULTILINE)
    assert m, "up target not found in Makefile"
    up_body = m.group(1)
    assert "--profile sap" in body and "$(COMPOSE_FULL)" in up_body, (
        "v1.0 `make up` must start the SAP profile because smoke/E2E require "
        "SAP HCM, SAP S/4HANA and SuccessFactors on ports 8202-8204"
    )
    assert "up-core:" in body, "Makefile must keep an explicit up-core target for non-SAP local work"


def test_makefile_lifecycle_targets_manage_full_sap_stack():
    body = MAKEFILE.read_text(encoding="utf-8")
    assert "COMPOSE_BASE ?= docker compose -f infra/docker-compose.yml" in body
    assert "COMPOSE_DEV ?= $(COMPOSE_BASE) -f infra/docker-compose.dev.yml" in body
    assert "COMPOSE_FULL ?= $(COMPOSE_DEV) --profile sap" in body
    for target in ("up", "down", "nuke", "logs", "ps"):
        m = re.search(rf"^{target}:\s*$([\s\S]+?)(?=^\S|\Z)", body, re.MULTILINE)
        assert m, f"{target} target not found in Makefile"
        assert "$(COMPOSE_FULL)" in m.group(1), (
            f"{target} must use COMPOSE_FULL so SAP profile services are not "
            "left running or omitted from the release lifecycle"
        )


def test_wait_for_health_does_not_accept_exited_containers_as_ready():
    body = WAIT_HEALTH.read_text(encoding="utf-8")
    assert "OMEGA_WAIT_FULL_STACK" in body
    assert "healthy|running|exited" not in body, (
        "wait_for_health must not count exited containers as ready; that would "
        "turn a dead service into a false-positive release gate"
    )
    assert "mode_superset" in body
    assert "omega_salesforce" in body
    assert "salesforce_status" in body
    assert "http://127.0.0.1:8205/health" in body
    assert "salesforce=${salesforce_status}" in body
    assert "mode_hubspot" in body
    assert "mode_sap_hcm" in body
    assert "mode_sap_s4hana" in body
    assert "mode_sap_successfactors" in body
    assert "mode_sap_b1" in body
    assert "http://127.0.0.1:8206/health" in body


def test_smoke_script_checks_salesforce_cartridge_contract():
    body = SCRIPT.read_text(encoding="utf-8")
    assert "http://localhost:8205/health" in body
    assert 'auth_gate_check "http://localhost:8205/mcp/tools"' in body
    assert 'auth_gate_check "http://localhost:8205/skills/entities"' in body
    assert '"salesforce:8205"' in body
    assert '"sap-b1:8206"' in body
    assert '"sap_b1:8206"' in body
    assert "mcp_servers registers salesforce cartridge" in body
    assert "mcp_servers WHERE id='salesforce'" in body
    assert "omega_cartridge_salesforce" in body
    assert "skipping 7 MCP tool probes" in body
    assert "skipping 6 MCP tool probes" not in body


def test_bootstrap_keys_backfills_all_runtime_db_role_passwords():
    body = (REPO_ROOT / "infra" / "bootstrap-keys.sh").read_text(encoding="utf-8")
    for key in (
        "OMEGA_REFINEMENT_GOLD_PASSWORD",
        "OMEGA_AIRFLOW_DAG_PASSWORD",
        "OMEGA_AIRFLOW_META_PASSWORD",
        "OMEGA_SUPERSET_META_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_S4_PASSWORD",
        "OMEGA_CARTRIDGE_SAP_SF_PASSWORD",
        "OMEGA_CARTRIDGE_REPLICON_PASSWORD",
        "OMEGA_CARTRIDGE_SALESFORCE_PASSWORD",
        "OMEGA_CARTRIDGE_HUBSPOT_PASSWORD",
        "OMEGA_CARTRIDGE_BANXICO_PASSWORD",
        "OMEGA_CARTRIDGE_INEGI_PASSWORD",
        "OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD",
    ):
        assert f'"{key}"' in body


def test_verify_release_bootstraps_env_and_recreates_full_stack():
    body = MAKEFILE.read_text(encoding="utf-8")
    bootstrap = re.search(r"^bootstrap-env:\s*$([\s\S]+?)(?=^\S|\Z)", body, re.MULTILINE)
    assert bootstrap, "bootstrap-env target not found in Makefile"
    bootstrap_body = bootstrap.group(1)
    assert "infra/bootstrap.sh" in bootstrap_body
    assert "infra/bootstrap-keys.sh infra/.env" in bootstrap_body

    m = re.search(r"^verify-release:\s*$([\s\S]+?)(?=^\S|\Z)", body, re.MULTILINE)
    assert m, "verify-release target not found in Makefile"
    target_body = m.group(1)
    assert "$(MAKE) bootstrap-env" in target_body
    assert "--profile sap config -q" in target_body
    assert "up -d --build --force-recreate" in target_body
    assert "docker compose -f infra/docker-compose.yml --profile sap build" not in target_body
    assert "scripts/wait_for_health.sh" in target_body
    assert target_body.index("bootstrap-env") < target_body.index("config -q")
    assert target_body.index("up -d --build --force-recreate") < target_body.index("scripts/wait_for_health.sh")


# ── Idempotency contract (documented by inspection) ─────────────────


def test_console_healthz_is_in_public_paths():
    """Sprint v1.23.1 hotfix regression guard.

    console/app/main.py has an `auth_middleware` that redirects every
    non-public path to /login. If /healthz isn't in _AUTH_PUBLIC_EXACT,
    the compose healthcheck probe gets a 307 to /login (never 200), and
    the container hangs in `(unhealthy)` forever — even though the
    service is fine. Workspace and vault already handle this via their
    own public-path mechanisms; console is the one that needs the
    explicit entry.

    This test verifies the entry survives. A future refactor of the
    public-path set that drops /healthz silently fails the test.
    """
    src = (REPO_ROOT / "console" / "app" / "main.py").read_text(encoding="utf-8")
    # Find the _AUTH_PUBLIC_EXACT block as text and check the literal
    # appears inside the braces. AST is overkill here; the set literal
    # is one-line-per-cluster of strings and grep-friendly.
    m = re.search(r"_AUTH_PUBLIC_EXACT\s*=\s*\{([^}]*)\}", src, re.DOTALL)
    assert m, "_AUTH_PUBLIC_EXACT set not found in console/app/main.py"
    block = m.group(1)
    assert '"/healthz"' in block or "'/healthz'" in block, (
        '"/healthz" must be in _AUTH_PUBLIC_EXACT — otherwise '
        "auth_middleware redirects the compose healthcheck probe to "
        "/login and the service stays (unhealthy) forever. See v1.23.1 "
        "hotfix commit message for the failure mode."
    )


def test_smoke_script_has_no_side_effects():
    """Every check in the script is read-only (GET / SELECT / has_table_privilege).
    A POST or INSERT in the smoke script would mean state mutation, which
    breaks idempotency: re-running yields different results.

    We accept the ONE POST (the auth-gate check) because it's expected
    to be REJECTED — no state change. The script must not contain
    INSERT/UPDATE/DELETE SQL or curl -X DELETE etc.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    forbidden_sql = (
        r"\bINSERT\s+INTO\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\bDELETE\s+FROM\b",
        r"\bDROP\s+",
        r"\bTRUNCATE\b",
        r"\bALTER\s+",
        r"\bCREATE\s+",
    )
    for pat in forbidden_sql:
        m = re.search(pat, body, re.IGNORECASE)
        assert not m, (
            f"smoke script contains side-effectful SQL ({pat}): {m.group(0)!r}. "
            f"Smoke must stay read-only for idempotency."
        )
    forbidden_curl = (r"-X\s+PUT\b", r"-X\s+DELETE\b", r"-X\s+PATCH\b")
    for pat in forbidden_curl:
        m = re.search(pat, body)
        assert not m, (
            f"smoke script contains state-changing curl call: {m.group(0)!r}. "
            f"Only POST /monitoring/invoke is allowed (and it's expected to 401/403)."
        )


# ── Beta-8: honest auth-gate classification (down != security regression) ──

def test_smoke_distinguishes_down_service_from_security_regression():
    """A connection-refused (000) must NOT be reported as a security
    regression. The honest classifier labels it as 'DOWN'."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "auth_gate_check" in body, "smoke must use the honest auth_gate_check helper"
    # 000 path must say DOWN, not 'security regression'.
    assert re.search(r"000\)\s*fail .*DOWN", body), (
        "auth_gate_check must classify 000 as a DOWN service, not a gate result"
    )
    assert "P0 security regression" not in body, (
        "smoke must no longer label an unreachable service as a 'P0 security "
        "regression' — that confuses audits and demos (a real 2xx-on-anon "
        "gap is still flagged via the UNEXPECTED branch)"
    )


def test_smoke_auth_gate_still_accepts_401_and_403():
    body = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"401\|403\)\s*pass", body), (
        "auth_gate_check must still treat 401/403 as a working gate"
    )


def test_smoke_uses_console_to_cartridge_pair_key_for_authenticated_tools():
    body = SCRIPT.read_text(encoding="utf-8")
    assert "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE" in body, (
        "authenticated cartridge probes identify as X-Internal-Service: console, "
        "so they must use the console→cartridge pair key instead of the legacy "
        "shared INTERNAL_API_KEY"
    )
    assert 'X-Internal-Service: console' in body


def test_e2e_runner_exports_console_to_cartridge_pair_key_for_specs():
    body = RUN_E2E.read_text(encoding="utf-8")
    assert "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE" in body, (
        "Playwright cartridge specs send X-Internal-Service: console; the runner "
        "must source the console→cartridge pair key from infra/.env"
    )
