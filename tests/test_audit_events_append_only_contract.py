"""audit_events must stay append-only for the application role.

The migration under test closes a gap that was real: because
25_service_roles.sql grants four verbs ON ALL TABLES and only revokes
vault_entries, the console's role could UPDATE and DELETE the very table that
records what the console did.

These checks are source-level on purpose — they run in every CI job, not only
where a database is wired. The runtime behaviour was verified separately
against PostgreSQL 15 (see the pull request), where all five attacks fail:
DELETE, UPDATE and TRUNCATE as omega_console, and DELETE and TRUNCATE as a
superuser with session_replication_role = replica.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "infra/init/99zzzzj_audit_events_append_only.sql"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _sql() -> str:
    return _read(MIGRATION)


def test_migration_sorts_after_the_broad_grant_it_narrows():
    # apply_db_migrations.sh applies infra/init/[0-9][0-9]*_*.sql in filename
    # order. A revoke that sorted before 25_service_roles.sql would be undone
    # by it on every fresh initialisation.
    names = sorted(p.name for p in (ROOT / "infra/init").glob("[0-9][0-9]*_*.sql"))
    grant = next(n for n in names if n.startswith("25_service_roles"))
    revoke = Path(MIGRATION).name
    assert revoke in names, "the migration must be picked up by apply_db_migrations.sh"
    assert names.index(revoke) > names.index(grant)


def test_application_role_loses_update_delete_and_truncate():
    sql = _sql()
    revoke = re.search(
        r"REVOKE\s+([A-Z,\s]+?)\s+ON\s+audit_events\s+FROM\s+omega_console", sql
    )
    assert revoke, "the migration must revoke from omega_console by name"
    verbs = {v.strip() for v in revoke.group(1).split(",")}
    assert verbs == {"UPDATE", "DELETE", "TRUNCATE"}


def test_application_role_keeps_exactly_what_an_audit_trail_needs():
    sql = _sql()
    grant = re.search(
        r"GRANT\s+([A-Z,\s]+?)\s+ON\s+audit_events\s+TO\s+omega_console", sql
    )
    assert grant, "the migration must restate the grants it leaves in place"
    verbs = {v.strip() for v in grant.group(1).split(",")}
    assert verbs == {"SELECT", "INSERT"}


def test_triggers_close_the_replica_and_truncate_gaps():
    sql = _sql()
    # A plain trigger does not fire under session_replication_role = replica,
    # and a row-level trigger never fires on TRUNCATE. Both gaps are why the
    # external_action_events guard is weaker than it looks.
    assert "BEFORE UPDATE OR DELETE ON audit_events" in sql
    assert "BEFORE TRUNCATE ON audit_events" in sql
    assert "FOR EACH STATEMENT" in sql
    always = re.findall(r"ENABLE ALWAYS TRIGGER\s+(\w+)", sql)
    assert set(always) == {"audit_events_no_update_delete", "audit_events_no_truncate"}


def test_migration_is_idempotent():
    sql = _sql()
    # apply_db_migrations.sh records applied files, but a fresh initialisation
    # replays everything, and operators re-run files by hand.
    assert sql.count("DROP TRIGGER IF EXISTS") == 2
    assert "CREATE OR REPLACE FUNCTION audit_events_append_only()" in sql


def test_break_glass_path_is_documented_in_the_migration():
    # Erasing an audit record should be possible for legitimate maintenance,
    # but only as an explicit, privileged act. If that path is undocumented,
    # the next operator reaches for something worse.
    sql = _sql()
    assert "DISABLE TRIGGER audit_events_no_update_delete" in sql


def test_application_code_never_updates_or_deletes_audit_events():
    # The revoke is only safe while this holds. If a future change adds an
    # UPDATE or DELETE, this test fails before production does.
    offenders: list[str] = []
    pattern = re.compile(
        r"(UPDATE\s+audit_events|DELETE\s+FROM\s+audit_events)", re.IGNORECASE
    )
    for service in ("console", "workspace", "refinement", "vault", "mcp-infra"):
        root = ROOT / service
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "test" in path.parts or path.name.startswith("test_"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if pattern.search(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "audit_events is append-only for the application role; these files "
        f"would now fail at runtime: {offenders}"
    )
