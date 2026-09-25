from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROLES_SQL = REPO_ROOT / "infra" / "init" / "25_service_roles.sql"
LOCKDOWN_SQL = REPO_ROOT / "infra" / "init" / "35_omega_mcp_infra_lockdown.sql"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_test.sh"

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

OPERATIONAL_TABLES = (
    "cartridge_dags",
    "entity_config",
    "pipeline_runs",
    "datasets",
    "silver_lineage",
    "mcp_servers",
)


def _omega_mcp_grant_block() -> str:
    src = SERVICE_ROLES_SQL.read_text(encoding="utf-8")
    match = re.search(
        r"GRANT\s+SELECT\s+ON\s+([^;]+?)\s+TO\s+omega_mcp_infra\s*;",
        src,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "could not locate the SELECT grant to omega_mcp_infra"
    return match.group(1)


def _names_in_grant_block(block: str) -> set[str]:
    return {
        token.strip()
        for token in re.split(r"[,\s]+", block)
        if token.strip() and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", token.strip())
    }


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


def test_lockdown_migration_exists():
    assert LOCKDOWN_SQL.is_file(), (
        f"missing migration {LOCKDOWN_SQL.relative_to(REPO_ROOT)}"
    )


def test_lockdown_migration_is_idempotent_shape():
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
        if not re.search(rf"'{re.escape(tbl)}'", src):
            missing.append(tbl)
    assert not missing, (
        f"lockdown migration is missing entries for: {missing}"
    )


def test_lockdown_migration_uses_revoke_all_privileges():
    src = LOCKDOWN_SQL.read_text(encoding="utf-8")
    assert "REVOKE ALL PRIVILEGES" in src, (
        "use REVOKE ALL PRIVILEGES to cover any historical extra grants"
    )


def test_lockdown_migration_does_not_touch_vault_entries_grants():
    src = LOCKDOWN_SQL.read_text(encoding="utf-8")
    code_lines = [
        line for line in src.splitlines() if not line.lstrip().startswith("--")
    ]
    code = "\n".join(code_lines)
    assert "vault_entries" not in code, (
        "lockdown migration must not REVOKE/GRANT vault_entries — that "
        "remains v1.19 territory"
    )


def test_smoke_test_includes_lockdown_checks():
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "has_table_privilege('omega_mcp_infra'" in src, (
        "smoke test must call has_table_privilege on omega_mcp_infra"
    )
    must_check = ("users", "tenants", "decisions", "roles", "workspaces")
    for tbl in must_check:
        assert tbl in src, f"smoke test missing reference to {tbl!r}"


def test_smoke_test_keeps_operational_regression_check():
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "has_table_privilege('omega_mcp_infra', 'cartridge_dags'" in src
