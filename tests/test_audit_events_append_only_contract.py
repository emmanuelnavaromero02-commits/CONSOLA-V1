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
    assert "BEFORE UPDATE OR DELETE ON audit_events" in sql
    assert "BEFORE TRUNCATE ON audit_events" in sql
    assert "FOR EACH STATEMENT" in sql
    always = re.findall(r"ENABLE ALWAYS TRIGGER\s+(\w+)", sql)
    assert set(always) == {"audit_events_no_update_delete", "audit_events_no_truncate"}


def test_migration_is_idempotent():
    sql = _sql()
    assert sql.count("DROP TRIGGER IF EXISTS") == 2
    assert "CREATE OR REPLACE FUNCTION audit_events_append_only()" in sql


def test_owner_is_exempt_so_maintenance_stays_possible():
    sql = _sql()
    assert "session_user = owner_name" in sql
    assert "pg_get_userbyid" in sql
    assert "TG_LEVEL = 'STATEMENT'" in sql


def test_application_code_never_updates_or_deletes_audit_events():
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


HARDENING = "infra/init/99zzzzk_audit_events_append_only_hardening.sql"


def _hardening() -> str:
    return _read(HARDENING)


def test_hardening_sorts_after_the_migration_it_fixes():
    names = sorted(p.name for p in (ROOT / "infra/init").glob("[0-9][0-9]*_*.sql"))
    assert names.index(Path(HARDENING).name) > names.index(Path(MIGRATION).name)


def test_trigger_function_pins_search_path_with_pg_temp_last():
    sql = _hardening()
    assert "SET search_path = pg_catalog, pg_temp" in sql
    assert "SET search_path = pg_catalog, public" not in sql


def test_owner_is_read_from_the_trigger_relation_and_compared_by_oid():
    sql = _hardening()
    assert "WHERE c.oid = TG_RELID" in sql
    assert "FROM pg_catalog.pg_class c" in sql
    assert "FROM pg_catalog.pg_roles r" in sql
    assert "session_role = table_owner" in sql
    assert "relname = 'audit_events'" not in sql


def test_trigger_function_ownership_is_pinned():
    assert (
        "ALTER FUNCTION public.audit_events_append_only() OWNER TO postgres"
        in _hardening()
    )


def test_conversation_foreign_key_is_dropped():
    sql = _hardening()
    assert "a.attname = 'conversation_id'" in sql
    assert "DROP CONSTRAINT %I" in sql


def test_hardening_refuses_to_commit_unless_every_guarantee_holds():
    sql = _hardening()
    for message in (
        "still carries a foreign key",
        "is not owned by postgres",
        "does not pin search_path",
        "not ENABLE ALWAYS",
    ):
        assert message in sql
