from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INIT = REPO / "infra" / "init"
MIGRATION = INIT / "99zzzzu_catalog_copilot.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_migration_runs_after_every_existing_migration():
    names = sorted(path.name for path in INIT.glob("[0-9][0-9]*_*.sql"))
    assert names[-1] == MIGRATION.name or names.index(MIGRATION.name) > names.index(
        "99zzzzq_analytic_app_manifest_registry_sap_b1_poc.sql"
    )


def test_migration_is_idempotent_and_transaction_safe():
    code = _code(_sql())
    assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", code, re.I | re.M)
    assert "CONCURRENTLY" not in code.upper()
    for statement in re.findall(r"ADD COLUMN [^,;]+", code):
        assert "IF NOT EXISTS" in statement, statement
    assert "CREATE TABLE IF NOT EXISTS catalog_copilot_state" in code
    for index in re.findall(r"CREATE (?:UNIQUE )?INDEX[^;]+;", code):
        assert "IF NOT EXISTS" in index
    assert "DROP TABLE" not in code.upper()
    assert "DELETE FROM" not in code.upper()


def test_every_new_check_is_added_not_valid_then_validated():
    code = _code(_sql())
    added = re.findall(r"ADD CONSTRAINT (\w+)", code)
    assert len(added) == 12
    for name in added:
        assert f"conname = '{name}'" in code, f"{name} must be guarded"
        assert f"VALIDATE CONSTRAINT {name};" in code, f"{name} must be validated"
    assert code.count("NOT VALID") == len(added)


def test_counts_only_is_a_database_invariant():
    code = _code(_sql())
    forbidden = "ARRAY['values', 'examples', 'sample', 'samples', 'min', 'max']"
    assert code.count(forbidden) == 3
    assert "copilot_evidence\n                         ?|" in code or (
        "copilot_evidence" in code and "?|" in code
    )
    assert "CHECK (classifications <@ ARRAY['pii', 'financial', 'confidential']::text[])" in code
    assert "CHECK (origin IN ('manual', 'packaged', 'copilot'))" in code
    assert "CHECK (status IN ('active', 'rejected', 'retired'))" in code
    assert "cardinality IN ('1:1', '1:N', 'N:1', 'N:N')" in code
    assert "join_hint IN ('INNER', 'LEFT', 'RIGHT', 'FULL')" in code


def test_state_table_is_tenant_scoped_with_forced_rls_and_no_service_deletes():
    code = _code(_sql())
    assert "REFERENCES workspaces(tenant_id, id) ON DELETE CASCADE" in code
    assert "UNIQUE (workspace_id, subject_kind, subject)" in code
    assert "ALTER TABLE catalog_copilot_state ENABLE ROW LEVEL SECURITY" in code
    assert "ALTER TABLE catalog_copilot_state FORCE ROW LEVEL SECURITY" in code
    assert code.count("omega_rls_workspace_matches(tenant_id, workspace_id)") == 3
    assert "USING (true)" not in code and "WITH CHECK (true)" not in code
    assert "FOR SELECT TO omega_mcp_infra" in code
    assert "REVOKE DELETE, TRUNCATE ON catalog_copilot_state" in code
    assert "GRANT SELECT, INSERT, UPDATE ON catalog_copilot_state TO omega_refinement" in code
    assert "GRANT SELECT ON catalog_copilot_state TO omega_console" in code
    assert "GRANT DELETE" not in code


def test_backfills_are_bounded_to_legacy_values():
    code = _code(_sql())
    assert "WHEN 'many_to_one' THEN 'N:1'" in code
    assert "WHEN 'one_to_many' THEN '1:N'" in code
    assert "WHEN 'one_to_one' THEN '1:1'" in code
    assert "WHEN 'many_to_many' THEN 'N:N'" in code
    assert "&& ARRAY['auto_described', 'semantic_enrichment']::text[]" in code
    assert "WHERE description_origin IS NULL" in code


def test_migration_records_itself_in_the_ledger():
    assert (
        "INSERT INTO schema_migrations (filename, applied_at)\n"
        "VALUES ('99zzzzu_catalog_copilot.sql', NOW())\n"
        "ON CONFLICT (filename) DO NOTHING;"
    ) in _sql()
