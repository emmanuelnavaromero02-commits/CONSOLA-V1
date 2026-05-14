"""Sprint v1.19 — per-service Postgres roles (defense-in-depth).

Each backend service now connects with its own login role instead of
the shared ``postgres`` superuser. This file holds the static contract
tests that the audit relies on:

  * The migration ``25_service_roles.sql`` exists, defines all 5 roles,
    and locks ``vault_entries`` to ``omega_vault``.
  * ``bootstrap.sh`` generates the 5 ``OMEGA_*_PASSWORD`` env vars.
  * ``docker-compose.yml`` wires every in-scope service to its omega
    role via DATABASE_URL (or PG_USER/PG_PASSWORD for mcp-infra), and
    passes the 5 passwords to Postgres via ``PGOPTIONS`` so the
    migration can read them with ``current_setting('app.omega_xxx_password')``.

Tests are pure string parsing — no Postgres needed in the test env.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT  = Path(__file__).resolve().parents[1]
MIGRATION  = REPO_ROOT / "infra" / "init" / "25_service_roles.sql"
COMPOSE    = REPO_ROOT / "infra" / "docker-compose.yml"
BOOTSTRAP  = REPO_ROOT / "infra" / "bootstrap.sh"
ENV_EXAMPLE = REPO_ROOT / "infra" / ".env.example"


OMEGA_ROLES = (
    "omega_console",
    "omega_refinement",
    "omega_vault",
    "omega_workspace",
    "omega_mcp_infra",
)
NON_VAULT_ROLES = tuple(r for r in OMEGA_ROLES if r != "omega_vault")
PASSWORD_VARS = tuple(f"{r.upper()}_PASSWORD" for r in OMEGA_ROLES)


# ── Migration SQL ───────────────────────────────────────────────────


def test_migration_file_exists():
    assert MIGRATION.is_file(), f"{MIGRATION} missing"


@pytest.mark.parametrize("role", OMEGA_ROLES)
def test_migration_creates_each_role(role):
    """Each role gets a CREATE ROLE inside an idempotent DO $$ block."""
    sql = MIGRATION.read_text(encoding="utf-8")
    pattern = rf"CREATE ROLE {role}\b"
    assert re.search(pattern, sql), (
        f"migration is missing CREATE ROLE for {role!r}"
    )


def test_migration_defines_exactly_five_roles():
    """A 6th omega_xxx role would mean someone added one without
    documenting it in the spec — fail loudly so the audit catches it."""
    sql = MIGRATION.read_text(encoding="utf-8")
    found = sorted(set(re.findall(r"CREATE ROLE (omega_[a-z_]+)\b", sql)))
    assert found == sorted(OMEGA_ROLES), (
        f"role set drift: found {found}, expected {sorted(OMEGA_ROLES)}"
    )


@pytest.mark.parametrize("role", NON_VAULT_ROLES)
def test_vault_entries_revoked_for_non_vault_roles(role):
    """The whole point of this sprint: only omega_vault touches
    vault_entries. Every other role must have an explicit
    REVOKE ALL ON vault_entries."""
    sql = MIGRATION.read_text(encoding="utf-8")
    pattern = rf"REVOKE ALL ON vault_entries FROM {role}\b"
    assert re.search(pattern, sql), (
        f"{role} must have REVOKE ALL ON vault_entries"
    )


def test_vault_role_has_full_crud_on_vault_entries():
    sql = MIGRATION.read_text(encoding="utf-8")
    pattern = r"GRANT\s+SELECT,\s*INSERT,\s*UPDATE,\s*DELETE\s+ON\s+vault_entries\s+TO\s+omega_vault"
    assert re.search(pattern, sql, flags=re.IGNORECASE), (
        "omega_vault must have SELECT/INSERT/UPDATE/DELETE on vault_entries"
    )


def test_no_other_role_has_grants_on_vault_entries():
    """Belt-and-suspenders: no non-vault role appears in any
    GRANT ... ON vault_entries TO ... line."""
    sql = MIGRATION.read_text(encoding="utf-8")
    grants = re.findall(
        r"GRANT\s+[^;]*?\s+ON\s+vault_entries\s+TO\s+(\w+)",
        sql, flags=re.IGNORECASE,
    )
    leaked = sorted(set(g for g in grants if g != "omega_vault"))
    assert not leaked, (
        f"vault_entries GRANT escaped to non-vault role(s): {leaked}"
    )


def test_migration_aborts_on_missing_password_setting():
    """The migration must refuse to create a role with a NULL/empty
    password — otherwise an operator who forgets to set PGOPTIONS gets
    5 passwordless login roles, which is exactly what we're trying to
    prevent."""
    sql = MIGRATION.read_text(encoding="utf-8")
    # Each DO block has a defensive RAISE EXCEPTION for the empty
    # password case. Five blocks → five RAISE EXCEPTIONs.
    raises = re.findall(
        r"RAISE\s+EXCEPTION\s+'app\.(omega_[a-z_]+)_password not set",
        sql,
    )
    assert sorted(set(raises)) == sorted(OMEGA_ROLES), (
        f"missing empty-password guard for some role(s): "
        f"got {sorted(set(raises))}, expected {sorted(OMEGA_ROLES)}"
    )


# ── bootstrap.sh ────────────────────────────────────────────────────


@pytest.mark.parametrize("var", PASSWORD_VARS)
def test_bootstrap_generates_each_omega_password_assignment(var):
    """bootstrap.sh must both generate the value AND write it into the
    .env file (two separate appearances per var)."""
    src = BOOTSTRAP.read_text(encoding="utf-8")
    # The variable assignment uses openssl rand -hex 16
    assert re.search(rf"^{var}=\"\$\(openssl rand -hex 16\)\"$",
                     src, flags=re.MULTILINE), (
        f"{var} not generated by bootstrap.sh"
    )
    # And it shows up in the heredoc that writes to .env
    assert f"{var}=${{{var}}}" in src, (
        f"{var} not written into infra/.env by bootstrap.sh heredoc"
    )


def test_env_example_documents_all_five_omega_passwords():
    """A new operator copies .env.example → .env to know what to fill in
    (or just runs bootstrap.sh). Either way, the template must list
    the new vars so they're discoverable."""
    src = ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in PASSWORD_VARS:
        assert var in src, f".env.example missing {var}"


# ── docker-compose.yml ──────────────────────────────────────────────


def _compose_doc():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_postgres_service_passes_pgoptions_with_five_app_settings():
    """Without these custom GUCs being set on the server, the migration
    can't read the passwords and fails. Verifies the bridge between the
    .env vars and the SQL migration."""
    doc = _compose_doc()
    pg_env = doc["services"]["postgres"]["environment"]
    assert "PGOPTIONS" in pg_env, "postgres service missing PGOPTIONS"
    pgo = pg_env["PGOPTIONS"]
    for role in OMEGA_ROLES:
        setting = f"app.{role}_password"
        assert setting in pgo, (
            f"PGOPTIONS missing -c {setting}=…  ({pgo!r})"
        )


SERVICE_TO_ROLE = {
    "console":    "omega_console",
    "refinement": "omega_refinement",
    "vault":      "omega_vault",
    "workspace":  "omega_workspace",
    # mcp-infra uses PG_USER/PG_PASSWORD, not DATABASE_URL — checked separately.
}


@pytest.mark.parametrize("service,role", list(SERVICE_TO_ROLE.items()))
def test_each_service_database_url_uses_its_omega_role(service, role):
    """The DATABASE_URL of each in-scope service must use the per-pair
    role and the matching password env var. A leftover `postgres:` user
    in any of these would silently bypass the whole sprint."""
    doc = _compose_doc()
    env = doc["services"][service]["environment"]
    db_url = env["DATABASE_URL"]
    assert f"://{role}:" in db_url, (
        f"{service} DATABASE_URL doesn't use {role}: {db_url!r}"
    )
    pwd_var = f"{role.upper()}_PASSWORD"
    assert f"${{{pwd_var}" in db_url, (
        f"{service} DATABASE_URL doesn't reference {pwd_var}: {db_url!r}"
    )
    assert "postgres:${POSTGRES_PASSWORD" not in db_url, (
        f"{service} DATABASE_URL still uses superuser postgres: {db_url!r}"
    )


def test_mcp_infra_uses_omega_mcp_infra_via_pg_user():
    """mcp-infra reads its connection params via PG_HOST / PG_USER /
    PG_PASSWORD instead of a single DSN, so it gets a separate test."""
    doc = _compose_doc()
    env = doc["services"]["mcp-infra"]["environment"]
    assert env["PG_USER"] == "omega_mcp_infra", (
        f"mcp-infra PG_USER must be omega_mcp_infra, got {env['PG_USER']!r}"
    )
    assert "OMEGA_MCP_INFRA_PASSWORD" in env["PG_PASSWORD"], (
        f"mcp-infra PG_PASSWORD must reference OMEGA_MCP_INFRA_PASSWORD; "
        f"got {env['PG_PASSWORD']!r}"
    )


def test_refinement_gold_database_url_stays_on_postgres_superuser():
    """The analytical GOLD DB doesn't have auth-state tables to lock
    down. Per the spec, refinement keeps using the postgres superuser
    there. This test makes the deliberate-out-of-scope decision visible
    so a future cleanup PR has to actively change it."""
    doc = _compose_doc()
    gold = doc["services"]["refinement"]["environment"]["GOLD_DATABASE_URL"]
    assert "://postgres:" in gold, (
        f"GOLD_DATABASE_URL changed unexpectedly: {gold!r}. "
        f"If this was intentional, update the test and the spec."
    )


def test_no_in_scope_service_still_uses_postgres_superuser_for_main_db():
    """Regression guard: catch a future PR that re-introduces
    `postgres:${POSTGRES_PASSWORD}` for the main DATABASE_URL on any of
    the 5 in-scope services."""
    doc = _compose_doc()
    in_scope = ("console", "refinement", "vault", "workspace")
    bad = []
    for svc in in_scope:
        url = doc["services"][svc]["environment"].get("DATABASE_URL", "")
        if "postgres:${POSTGRES_PASSWORD" in url:
            bad.append(f"{svc}: {url}")
    assert not bad, (
        f"in-scope service(s) still use the postgres superuser:\n  "
        + "\n  ".join(bad)
    )
