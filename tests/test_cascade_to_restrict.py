"""Sprint v1.43.2 (Claude B6) — CASCADE → RESTRICT + soft-delete trigger.

Static structural verification of migration 45. Live-DB assertions
(actual DELETE blocked, audit_deletes row captured) live in the E2E
suite which has access to a real Postgres.
"""
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
    # Must store the entire row + a pointer back to whoever / when.
    for col in ("table_name", "deleted_pk", "deleted_row", "deleted_at"):
        assert col in src, f"audit_deletes missing column {col}"
    # JSONB for the snapshot so we can search later.
    assert "deleted_row     JSONB NOT NULL" in src


def test_migration_45_indexes_audit_deletes_for_lookup():
    """The two access patterns: 'show me everything deleted from
    workspaces' and 'show me everything deleted in the last 24h'."""
    src = _src()
    assert "idx_audit_deletes_table" in src
    assert "idx_audit_deletes_at" in src
    assert "deleted_at DESC" in src


def test_migration_45_walks_information_schema_for_cascade_fks():
    """Hard-coding constraint names breaks across environments. The
    migration must look them up at runtime."""
    src = _src()
    assert "information_schema.referential_constraints" in src
    assert "rc.delete_rule     = 'CASCADE'" in src
    assert "ON DELETE RESTRICT" in src


def test_migration_45_targets_critical_tables_only():
    """The conversion is opt-in to the chain that motivated the audit
    finding — accidental tenant/workspace/user mass-delete. We must
    NOT touch unrelated CASCADE FKs (e.g. rag_chunks → rag_sources)."""
    src = _src()
    target_tables = (
        "user_workspace_roles", "conversations", "conversation_messages",
        "workspaces", "decisions", "refresh_tokens", "user_sessions",
    )
    for tbl in target_tables:
        assert f"'{tbl}'" in src, f"missing target table {tbl}"
    # And explicitly NOT in the tables that should keep CASCADE
    # (rag_chunks parent_id → cleaning up RAG chunks is intentional).
    assert "'rag_chunks'" not in src


def test_migration_45_soft_delete_trigger_function_present():
    src = _src()
    assert "CREATE OR REPLACE FUNCTION soft_delete_audit_trigger" in src
    assert "to_jsonb(OLD)" in src
    # Returning OLD lets the actual DELETE proceed; returning NULL
    # would cancel the operation.
    assert "RETURN OLD" in src


def test_migration_45_attaches_trigger_to_protected_tables():
    """The trigger fires on tenants / workspaces / users /
    conversations / decisions — the tables an operator can DELETE
    by accident from a console."""
    src = _src()
    for tbl in ("tenants", "workspaces", "users",
                "conversations", "decisions"):
        assert f"'{tbl}'" in src, f"trigger not attached to {tbl}"
    assert "BEFORE DELETE" in src
    assert "FOR EACH ROW EXECUTE FUNCTION soft_delete_audit_trigger" in src


def test_migration_45_idempotent():
    """Re-runs must be safe: every CREATE uses IF NOT EXISTS, every
    DROP TRIGGER uses IF EXISTS, the FK swap walks the catalog so it
    naturally no-ops once the swap is done."""
    src = _src()
    assert "CREATE TABLE IF NOT EXISTS audit_deletes" in src
    assert "CREATE INDEX IF NOT EXISTS" in src
    assert "DROP TRIGGER IF EXISTS" in src
    # CREATE OR REPLACE for the function so re-runs replace cleanly.
    assert "CREATE OR REPLACE FUNCTION" in src


def test_migration_45_table_existence_guard():
    """A partial-install environment may lack one of the dependant
    tables. The trigger attach loop must skip with NOTICE, not error."""
    src = _src()
    assert "FROM information_schema.tables" in src
    assert "table_schema = 'public'" in src


def test_migration_45_lex_orders_after_44():
    """Lexicographic order: 45 must come after 44 (which created the
    audit_events UNIQUE constraint) and after 38 (conversations)."""
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("44_audit_events_dedup.sql") < names.index("45_cascade_to_restrict.sql")
    assert names.index("38_copilot_conversations.sql") < names.index("45_cascade_to_restrict.sql")


def test_migration_45_no_secret_columns_snapshotted_directly():
    """We snapshot to_jsonb(OLD) which captures every column. For the
    five protected tables (tenants, workspaces, users, conversations,
    decisions) this is intentional — but the migration must NOT
    attach the trigger to vault_entries / refresh_tokens / sessions
    where it would persist secret/token values into a JSONB tombstone.
    Confirm those tables are NOT in the FOREACH list."""
    src = _src()
    # The FOREACH array
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
