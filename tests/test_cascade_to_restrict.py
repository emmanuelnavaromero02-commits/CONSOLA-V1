from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INIT_DIR = REPO / "infra" / "init"
MIGRATION = INIT_DIR / "45_cascade_to_restrict.sql"


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_45_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"


def test_migration_45_creates_audit_deletes_table():
    src = _src()
    assert "CREATE TABLE IF NOT EXISTS audit_deletes" in src
    for col in ("table_name", "deleted_pk", "deleted_row", "deleted_at"):
        assert col in src, f"audit_deletes missing column {col}"
    assert "deleted_row     JSONB NOT NULL" in src


def test_migration_45_indexes_audit_deletes_for_lookup():
    src = _src()
    assert "idx_audit_deletes_table" in src
    assert "idx_audit_deletes_at" in src
    assert "deleted_at DESC" in src


def test_migration_45_walks_information_schema_for_cascade_fks():
    src = _src()
    assert "information_schema.referential_constraints" in src
    assert "rc.delete_rule     = 'CASCADE'" in src
    assert "ON DELETE RESTRICT" in src


def test_migration_45_targets_critical_tables_only():
    src = _src()
    target_tables = (
        "user_workspace_roles", "conversations", "conversation_messages",
        "workspaces", "decisions",
    )
    fk_in_clause = re.search(
        r"AND tc\.table_name\s+IN\s*\(\s*\n(.*?)\n\s*\)",
        src, re.DOTALL,
    )
    assert fk_in_clause, "FK IN-clause not found"
    fk_body = fk_in_clause.group(1)

    for tbl in target_tables:
        assert f"'{tbl}'" in fk_body, f"missing target table {tbl}"

    for forbidden in ("refresh_tokens", "user_sessions"):
        assert f"'{forbidden}'" not in fk_body, (
            f"{forbidden} must not be in the FK-conversion IN-list — "
            "see SQL comment and tests/test_cascade_to_restrict.py:test_migration_45_targets_critical_tables_only"
        )
    assert "'rag_chunks'" not in src


def test_migration_45_soft_delete_trigger_function_present():
    src = _src()
    assert "CREATE OR REPLACE FUNCTION soft_delete_audit_trigger" in src
    assert "to_jsonb(OLD)" in src
    assert "RETURN OLD" in src


def test_migration_45_attaches_trigger_to_protected_tables():
    src = _src()
    for tbl in ("tenants", "workspaces", "users",
                "conversations", "decisions"):
        assert f"'{tbl}'" in src, f"trigger not attached to {tbl}"
    assert "BEFORE DELETE" in src
    assert "FOR EACH ROW EXECUTE FUNCTION soft_delete_audit_trigger" in src


def test_migration_45_idempotent():
    src = _src()
    assert "CREATE TABLE IF NOT EXISTS audit_deletes" in src
    assert "CREATE INDEX IF NOT EXISTS" in src
    assert "DROP TRIGGER IF EXISTS" in src
    assert "CREATE OR REPLACE FUNCTION" in src


def test_migration_45_table_existence_guard():
    src = _src()
    assert "FROM information_schema.tables" in src
    assert "table_schema = 'public'" in src


def test_migration_45_lex_orders_after_44():
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("44_audit_events_dedup.sql") < names.index("45_cascade_to_restrict.sql")
    assert names.index("38_copilot_conversations.sql") < names.index("45_cascade_to_restrict.sql")


def test_migration_45_no_secret_columns_snapshotted_directly():
    src = _src()
    m = re.search(r"FOREACH\s+tbl\s+IN\s+ARRAY\s+ARRAY\[([^\]]+)\]",
                  src, re.IGNORECASE)
    assert m, "FOREACH array not found"
    body = m.group(1)
    for forbidden in ("vault_entries", "refresh_tokens", "user_sessions",
                      "user_tokens"):
        assert f"'{forbidden}'" not in body, (
            f"trigger must NOT attach to {forbidden} — "
            f"would snapshot secret material into audit_deletes JSONB"
        )


def test_migration_45_trigger_strips_password_hash_from_snapshot():
    src = _src()
    assert "to_jsonb(OLD)" in src
    assert "- 'password_hash'" in src, (
        "soft_delete_audit_trigger must strip password_hash from the "
        "JSONB snapshot — otherwise every users-row DELETE persists a "
        "crackable bcrypt hash into audit_deletes."
    )
    for keyword in ("password", "token", "api_key", "secret"):
        assert f"- '{keyword}'" in src, (
            f"trigger should strip '{keyword}' — defense-in-depth for "
            "future schema additions that match a sensitive name."
        )


def test_migration_45_audit_deletes_revoked_from_public():
    src = _src()
    assert "REVOKE ALL ON audit_deletes FROM PUBLIC" in src


def test_migration_45_audit_deletes_append_only_from_console():
    src = _src()
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON audit_deletes FROM omega_console" in src, (
        "audit_deletes must be append-only for omega_console: REVOKE "
        "UPDATE/DELETE/TRUNCATE explicitly to neutralise the default "
        "ALTER DEFAULT PRIVILEGES from migration 25."
    )
    assert "GRANT INSERT, SELECT ON audit_deletes TO omega_console" in src


def test_migration_45_self_registers_in_schema_migrations():
    src = _src()
    assert "INSERT INTO schema_migrations" in src
    assert "'45_cascade_to_restrict.sql'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src
