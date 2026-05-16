"""Sprint v1.43.2 (Claude B5) — audit_events dedup + UNIQUE constraint.

Static + structural verification of:
  * migration 44 enforces uniqueness with the documented escape hatch
    for older Postgres,
  * the audit_service INSERT now carries ON CONFLICT DO NOTHING,
  * migration 19 no longer duplicates the audit_events CREATE TABLE.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INIT_DIR = REPO / "infra" / "init"
MIGRATION_44 = INIT_DIR / "44_audit_events_dedup.sql"
MIGRATION_16 = INIT_DIR / "16_audit_events.sql"
MIGRATION_19 = INIT_DIR / "19_operational_stability_hotfix.sql"
AUDIT_SVC    = REPO / "console" / "app" / "services" / "audit_service.py"


def test_migration_44_exists():
    assert MIGRATION_44.exists(), f"missing {MIGRATION_44}"


def test_migration_44_collapses_pre_existing_duplicates():
    """The DELETE step at the top removes redundant rows so the UNIQUE
    constraint can be added without a backfill failure."""
    src = MIGRATION_44.read_text(encoding="utf-8")
    assert "ROW_NUMBER() OVER" in src
    assert "PARTITION BY user_id, action, resource_id, created_at" in src
    assert "DELETE FROM audit_events" in src


def test_migration_44_adds_unique_constraint_with_fallback():
    """PG 15 path adds UNIQUE NULLS NOT DISTINCT; older fallback uses
    a partial unique index. Both must appear so a downgrade still
    enforces the contract."""
    src = MIGRATION_44.read_text(encoding="utf-8")
    assert "audit_events_dedup_uniq" in src
    assert "UNIQUE NULLS NOT DISTINCT" in src
    assert "(user_id, action, resource_id, created_at)" in src
    # Fallback path
    assert "audit_events_dedup_uniq_idx" in src
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in src


def test_migration_44_idempotent():
    """Re-running must be a no-op: the DO block guards against both
    the constraint and the fallback index already existing."""
    src = MIGRATION_44.read_text(encoding="utf-8")
    assert "IF EXISTS" in src
    # Three guards: pre-existing constraint, pre-existing fallback
    # index, and the partial-index CREATE itself uses IF NOT EXISTS.
    assert src.count("IF NOT EXISTS") + src.count("IF EXISTS") >= 3


def test_no_duplicate_create_table_audit_events():
    """The CREATE TABLE for audit_events lives only in 16_audit_events.sql
    going forward — the v1.43.2 cleanup removed the duplicate from 19."""
    files_with_create = []
    for p in INIT_DIR.glob("*.sql"):
        text = p.read_text(encoding="utf-8")
        # Match a real DDL: CREATE TABLE ... audit_events (
        if re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?audit_events\s*\(",
                     text, re.IGNORECASE):
            files_with_create.append(p.name)
    assert files_with_create == ["16_audit_events.sql"], (
        f"audit_events CREATE TABLE should live ONLY in 16_audit_events.sql, "
        f"found in: {files_with_create}"
    )


def test_migration_19_keeps_other_tables_intact():
    """The cleanup of migration 19 only removed the audit_events block;
    login_attempts, data_catalog, data_relationships and the entity_config
    UPDATEs must still be there."""
    src = MIGRATION_19.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS login_attempts" in src
    assert "CREATE TABLE IF NOT EXISTS data_catalog" in src
    assert "CREATE TABLE IF NOT EXISTS data_relationships" in src
    assert "UPDATE entity_config" in src


def test_audit_service_uses_on_conflict_do_nothing():
    """v1.43.2 Claude B5: the INSERT must carry ON CONFLICT DO NOTHING
    so a retry of the same admin action doesn't error out at the new
    UNIQUE constraint and propagate up as a 500 — instead it silently
    no-ops, which is the desired forensic posture."""
    src = AUDIT_SVC.read_text(encoding="utf-8")
    assert "INSERT INTO audit_events" in src
    assert "ON CONFLICT (user_id, action, resource_id, created_at)" in src
    assert "DO NOTHING" in src


def test_migration_44_lex_orders_after_16():
    """Lexicographic order: 44 must come after 16 so the table exists
    when the constraint is added."""
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("16_audit_events.sql") < names.index("44_audit_events_dedup.sql")
