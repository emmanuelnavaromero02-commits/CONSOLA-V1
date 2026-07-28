from __future__ import annotations

import pytest

from app.services.public_sql_runtime_catalog import STATEMENT_HEADS
from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql


CATALOG_PRODUCTIONS = {
    "abort": "ABORT",
    "alter": "ALTER TABLE payroll ADD COLUMN bonus INTEGER",
    "analyse": "ANALYSE payroll",
    "analyze": "ANALYZE payroll",
    "attach": "ATTACH 'payroll.db' AS payroll",
    "begin": "BEGIN",
    "call": "CALL refresh_payroll()",
    "checkpoint": "CHECKPOINT",
    "close": "CLOSE payroll_cursor",
    "cluster": "CLUSTER payroll",
    "comment": "COMMENT ON TABLE payroll IS 'copy'",
    "commit": "COMMIT",
    "copy": "COPY payroll TO STDOUT",
    "create": "CREATE TABLE payroll(id INTEGER)",
    "deallocate": "DEALLOCATE payroll_plan",
    "declare": "DECLARE payroll_cursor CURSOR FOR SELECT 1",
    "delete": "DELETE FROM payroll",
    "desc": "DESC payroll",
    "describe": "DESCRIBE payroll",
    "detach": "DETACH payroll",
    "discard": "DISCARD ALL",
    "do": "DO $$BEGIN NULL; END$$",
    "drop": "DROP TABLE payroll",
    "end": "END",
    "exec": "EXEC payroll_plan",
    "execute": "EXECUTE payroll_plan",
    "explain": "EXPLAIN SELECT 1",
    "export": "EXPORT DATABASE 'backup'",
    "fetch": "FETCH NEXT FROM payroll_cursor",
    "force": "FORCE CHECKPOINT payroll",
    "from": "FROM payroll",
    "grant": "GRANT SELECT ON payroll TO analyst",
    "import": "IMPORT FOREIGN SCHEMA remote FROM SERVER remote INTO public",
    "insert": "INSERT INTO payroll VALUES (1)",
    "install": "INSTALL httpfs",
    "listen": "LISTEN payroll_channel",
    "load": "LOAD httpfs",
    "lock": "LOCK TABLE payroll",
    "merge": "MERGE INTO payroll USING staging ON payroll.id = staging.id",
    "move": "MOVE NEXT FROM payroll_cursor",
    "notify": "NOTIFY payroll_channel",
    "pivot": "PIVOT payroll ON department USING sum(salary)",
    "pivot_wider": "PIVOT_WIDER payroll USING sum(salary)",
    "pragma": "PRAGMA version",
    "prepare": "PREPARE payroll_plan AS SELECT 1",
    "reassign": "REASSIGN OWNED BY old_role TO new_role",
    "refresh": "REFRESH MATERIALIZED VIEW payroll_view",
    "reindex": "REINDEX TABLE payroll",
    "release": "RELEASE SAVEPOINT payroll_savepoint",
    "reset": "RESET search_path",
    "revoke": "REVOKE SELECT ON payroll FROM analyst",
    "rollback": "ROLLBACK",
    "savepoint": "SAVEPOINT payroll_savepoint",
    "security": "SECURITY LABEL ON TABLE payroll IS 'label'",
    "select": "SELECT",
    "set": "SET search_path TO private",
    "show": "SHOW TRANSACTION ISOLATION LEVEL",
    "start": "START TRANSACTION",
    "summarize": "SUMMARIZE payroll",
    "table": "TABLE payroll",
    "truncate": "TRUNCATE payroll",
    "unlisten": "UNLISTEN payroll_channel",
    "unpivot": "UNPIVOT payroll ON salary INTO NAME metric VALUE amount",
    "update": "UPDATE payroll SET salary = 1",
    "use": "USE memory.main",
    "vacuum": "VACUUM",
    "values": "VALUES (1)",
    "with": "WITH p AS (VALUES (1)) SELECT * FROM p",
}


def test_versioned_catalog_has_one_complete_production_per_head() -> None:
    assert set(CATALOG_PRODUCTIONS) == STATEMENT_HEADS


@pytest.mark.parametrize(
    ("head", "production"),
    CATALOG_PRODUCTIONS.items(),
    ids=CATALOG_PRODUCTIONS,
)
def test_every_catalog_production_is_found_with_copy_on_both_sides(
    head: str,
    production: str,
) -> None:
    assert production.casefold().startswith(head)
    for raw in (
        production,
        f"Business note. {production}",
        f"note {production} please",
    ):
        assert contains_runtime_sql(raw)
        assert contains_public_sql(raw)


@pytest.mark.parametrize(
    "production",
    (
        "PIVOT payroll USING sum(salary)",
        "PIVOT payroll GROUP BY department",
        "PIVOT_WIDER payroll ON department USING sum(salary)",
        "LOCK payroll",
        "LOCK ONLY payroll IN ACCESS SHARE MODE",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "SET XML OPTION DOCUMENT",
        "DO LANGUAGE plpgsql $$BEGIN NULL; END$$",
        "SECURITY LABEL FOR selinux ON ROLE analyst IS 'label'",
        "SET LOCAL ROLE analyst",
        "SET SESSION ROLE analyst",
        "SET SESSION TIME ZONE 'UTC'",
        "SET LOCAL XML OPTION DOCUMENT",
        "COPY BINARY payroll TO STDOUT",
    ),
)
def test_real_dialect_variants_are_complete_catalog_productions(
    production: str,
) -> None:
    for raw in (production, f"Business note. {production} please"):
        assert contains_runtime_sql(raw)
        assert contains_public_sql(raw)
