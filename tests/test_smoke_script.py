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


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT    = REPO_ROOT / "scripts" / "smoke_test.sh"
MAKEFILE  = REPO_ROOT / "Makefile"


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
    assert result.returncode == 0, (
        f"bash -n rejected the smoke script:\n{result.stderr}"
    )


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
