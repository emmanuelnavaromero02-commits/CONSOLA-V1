"""
Sprint v1.36 — audit B4 (P0).

Before v1.36, ``infra/init/25_service_roles.sql`` granted SELECT on
identity / auth / decisions / RBAC tables (``users``, ``tenants``,
``decisions``, ``roles``, ``workspaces``) to ``omega_mcp_infra``.
Combined with ``postgres_execute_query`` (which lets the caller send
an arbitrary SELECT — by design, for admin users), the GRANT was a
path to ``users.password_hash`` exfiltration whenever a future bug
gave a non-admin reach to /api/mcp/invoke.

The MCP-infra service does NOT query any of those tables today
(``grep "FROM (users|tenants|decisions|roles|workspaces)" mcp-infra``
returns zero hits), so the GRANT was over-broad with no operational
justification.

This suite enforces, at the SQL source level, that:

1. ``25_service_roles.sql`` no longer names ``users / tenants /
   decisions / roles / workspaces`` in the ``GRANT SELECT … TO
   omega_mcp_infra`` block.
2. ``35_omega_mcp_infra_lockdown.sql`` exists and explicitly REVOKEs
   the identity / auth / audit tables, and is structured to be
   idempotent (DO-block + IF EXISTS guards) so it's safe to replay.
3. ``25_service_roles.sql`` still grants SELECT on the operational
   tables that Studio relies on (``cartridge_dags``,
   ``entity_config``, ``pipeline_runs``, …) — the lockdown is not a
   carpet-bomb.
4. ``scripts/smoke_test.sh`` exercises ``has_table_privilege`` on the
   live DB so a future GRANT regression fails the smoke run.

These are AST-style guards against accidental regression. They run in
seconds without Postgres because we read the .sql / .sh files
directly. A separate live test (test_postgres_roles.py) covers the
"it actually works against psql" path.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROLES_SQL = REPO_ROOT / "infra" / "init" / "25_service_roles.sql"
LOCKDOWN_SQL = REPO_ROOT / "infra" / "init" / "35_omega_mcp_infra_lockdown.sql"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_test.sh"

# Tables that must NEVER appear in an omega_mcp_infra GRANT, and that
# the lockdown migration must explicitly REVOKE.
SENSITIVE_TABLES = (
    "users",
    "user_sessions",
    "user_tokens",
    "refresh_tokens",
    "tenants",
    "workspaces",
    "roles",
    "user_workspace_roles",
    "decisions",
    "decision_actions",
    "audit_events",
    "login_attempts",
    "vault_access_log",
)

# Operational tables that must REMAIN granted to omega_mcp_infra.
OPERATIONAL_TABLES = (
    "cartridge_dags",
    "entity_config",
    "pipeline_runs",
    "datasets",
    "silver_lineage",
    "mcp_servers",
)


def _omega_mcp_grant_block() -> str:
    """Return the text of the ``GRANT SELECT … TO omega_mcp_infra``
    statement in 25_service_roles.sql so we can scan it for table
    names."""
    src = SERVICE_ROLES_SQL.read_text(encoding="utf-8")
    # Find the SELECT grant: "GRANT SELECT ON <tables> TO omega_mcp_infra;"
    match = re.search(
        r"GRANT\s+SELECT\s+ON\s+([^;]+?)\s+TO\s+omega_mcp_infra\s*;",
        src,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "could not locate the SELECT grant to omega_mcp_infra"
    return match.group(1)


def _names_in_grant_block(block: str) -> set[str]:
    """Return the set of bare identifiers named in the GRANT list."""
    return {
        token.strip()
        for token in re.split(r"[,\s]+", block)
        if token.strip() and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", token.strip())
    }


# ── 25_service_roles.sql ────────────────────────────────────────────────────

def test_service_roles_no_sensitive_grants_to_omega_mcp_infra():
    block = _omega_mcp_grant_block()
    names = _names_in_grant_block(block)
    offenders = sorted(names & set(SENSITIVE_TABLES))
    assert not offenders, (
        f"omega_mcp_infra must not have SELECT on identity / auth / "
        f"decisions tables; still granted: {offenders}"
    )


def test_service_roles_operational_grants_preserved():
    block = _omega_mcp_grant_block()
    names = _names_in_grant_block(block)
    missing = sorted(set(OPERATIONAL_TABLES) - names)
    assert not missing, (
        f"omega_mcp_infra must keep SELECT on the operational tables; "
        f"missing: {missing}. Did the lockdown over-revoke?"
    )


# ── 35_omega_mcp_infra_lockdown.sql ─────────────────────────────────────────

def test_lockdown_migration_exists():
    assert LOCKDOWN_SQL.is_file(), (
        f"missing migration {LOCKDOWN_SQL.relative_to(REPO_ROOT)}"
    )


def test_lockdown_migration_is_idempotent_shape():
    """The migration must guard every REVOKE with IF EXISTS-style
    checks so it can replay on a brand-new DB (role not created yet),
    on a v1.19+ DB (role exists, GRANT applied), or on an older DB
    that's missing some of the tables."""
    src = LOCKDOWN_SQL.read_text(encoding="utf-8")
    assert "DO $$" in src, "migration must be wrapped in a DO block"
    assert "IF NOT EXISTS" in src and "pg_roles" in src, (
        "migration must short-circuit when omega_mcp_infra role doesn't "
        "exist yet"
    )
    assert "IF EXISTS" in src and "pg_tables" in src, (
        "migration must skip tables that aren't present yet"
    )


def test_lockdown_migration_revokes_every_sensitive_table():
    src = LOCKDOWN_SQL.read_text(encoding="utf-8")
    missing = []
    for tbl in SENSITIVE_TABLES:
        # The DO block iterates over an ARRAY[...] of names; verify
        # each sensitive name is in that list (not a free-form
        # REVOKE), so reviewers can read one place to know what's
        # locked down.
        if not re.search(rf"'{re.escape(tbl)}'", src):
            missing.append(tbl)
    assert not missing, (
        f"lockdown migration is missing entries for: {missing}"
    )


def test_lockdown_migration_uses_revoke_all_privileges():
    src = LOCKDOWN_SQL.read_text(encoding="utf-8")
    # REVOKE ALL (not just SELECT) — the GRANT history might also have
    # INSERT / UPDATE for some tables in older deployments.
    assert "REVOKE ALL PRIVILEGES" in src, (
        "use REVOKE ALL PRIVILEGES to cover any historical extra grants"
    )


def test_lockdown_migration_does_not_touch_vault_entries_grants():
    """vault_entries lockdown is owned by v1.19; this migration must
    not REVOKE / GRANT on it (a duplicate REVOKE wouldn't hurt but
    would blur the audit trail). The name may still appear in a
    docstring/comment line."""
    src = LOCKDOWN_SQL.read_text(encoding="utf-8")
    # Drop comment-only lines, then check for statements mentioning
    # vault_entries.
    code_lines = [
        line for line in src.splitlines() if not line.lstrip().startswith("--")
    ]
    code = "\n".join(code_lines)
    assert "vault_entries" not in code, (
        "lockdown migration must not REVOKE/GRANT vault_entries — that "
        "remains v1.19 territory"
    )


# ── scripts/smoke_test.sh ───────────────────────────────────────────────────

def test_smoke_test_includes_lockdown_checks():
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    # The smoke test loops over the must-check tables using a shell
    # variable (e.g. `for sensitive in users tenants ...`), so the
    # literal name appears in the for-loop list rather than baked into
    # the SQL string. Verify both: that has_table_privilege is invoked
    # against omega_mcp_infra at least once, and that every must-check
    # table appears in the source (anywhere).
    assert "has_table_privilege('omega_mcp_infra'" in src, (
        "smoke test must call has_table_privilege on omega_mcp_infra"
    )
    must_check = ("users", "tenants", "decisions", "roles", "workspaces")
    for tbl in must_check:
        assert tbl in src, f"smoke test missing reference to {tbl!r}"


def test_smoke_test_keeps_operational_regression_check():
    """The smoke run must also verify a known operational read still
    works, so a future over-revoke fails the smoke instead of fooling
    nobody until a Studio user hits the bug."""
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "has_table_privilege('omega_mcp_infra', 'cartridge_dags'" in src
