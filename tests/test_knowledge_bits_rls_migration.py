from __future__ import annotations

import re
from pathlib import Path

import sqlglot
from sqlglot import exp

REPO = Path(__file__).resolve().parents[1]
INIT = REPO / "infra" / "init"
MIGRATION = INIT / "99zzzzs_knowledge_bits_workspace_rls.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function_body(sql: str, name: str) -> tuple[str, str]:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\((?P<args>[^)]*)\)\s*RETURNS boolean\s*"
        r"LANGUAGE sql\s*STABLE\s*AS \$\$(?P<body>.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group("args"), match.group("body")


def test_migration_runs_after_the_schema_it_hardens_and_registers_itself():
    names = sorted(path.name for path in INIT.glob("*.sql"))
    assert names.index(MIGRATION.name) > names.index("99zp_replicon_wip_materialization_v3.sql")
    sql = _sql()
    assert "VALUES ('99zzzzs_knowledge_bits_workspace_rls.sql', NOW())" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_text_scope_function_compares_text_and_requires_both_gucs():
    args, body = _function_body(_sql(), "omega_rls_workspace_text_matches")
    assert args == "row_tenant text, row_workspace text"
    assert "::uuid" not in body
    assert "SECURITY DEFINER" not in _sql()
    select = sqlglot.parse_one(body.strip(), read="postgres")
    assert isinstance(select, exp.Select)
    predicate = select.expressions[0].sql(dialect="postgres")
    for fragment in (
        "NOT row_tenant IS NULL",
        "NOT row_workspace IS NULL",
        "row_tenant = NULLIF(CURRENT_SETTING('app.tenant_id', TRUE), '')",
        "row_workspace = NULLIF(CURRENT_SETTING('app.workspace_id', TRUE), '')",
    ):
        assert fragment in predicate
    assert " OR " not in predicate


def test_every_plain_table_is_forced_scoped_or_quarantined():
    sql = _sql()
    loop = sql.split("DO $kb_rls$", 1)[1].split("$kb_rls$;", 1)[0]
    assert "namespace.nspname = 'knowledge_bits'" in loop
    assert "rel.relkind = 'r'" in loop
    assert "attribute.attname = 'tenant_id'" in loop
    assert "attribute.attname = 'workspace_id'" in loop
    assert "NOT attribute.attisdropped" in loop
    scoped, quarantine = loop.split("ELSE", 1)
    assert "IF relation.has_tenant AND relation.has_workspace THEN" in scoped
    for statement in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS kb_workspace_scope",
        "CREATE POLICY kb_workspace_scope ON knowledge_bits.%I TO PUBLIC",
        "USING (omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text))",
        "WITH CHECK (omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text))",
        "REVOKE ALL ON TABLE knowledge_bits.%I FROM PUBLIC",
    ):
        assert statement in scoped
    assert "policy.polname <> 'kb_workspace_scope'" in scoped
    assert "'DROP POLICY %I ON knowledge_bits.%I'" in scoped
    assert "USING (true)" not in sql.replace("USING(true)", "USING (true)")
    assert "to_regclass(format('knowledge_bits_quarantine.%I', target_name)) IS NOT NULL" in quarantine
    assert "LEFT(relation.relation_name, 40) || '_legacy_' || relation.relation_oid::TEXT" in quarantine
    assert "'ALTER TABLE knowledge_bits.%I RENAME TO %I'" in quarantine
    assert "'ALTER TABLE knowledge_bits.%I SET SCHEMA knowledge_bits_quarantine'" in quarantine
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql


def test_non_replicon_cartridge_roles_lose_knowledge_bits_privileges_by_either_spelling():
    sql = _sql()
    roles = sql.split("DO $kb_roles$", 1)[1].split("$kb_roles$;", 1)[0]
    assert "SELECT rolname FROM pg_roles" in roles
    for role in (
        "omega_cartridge_hubspot",
        "omega_cartridge_salesforce",
        "omega_cartridge_sap_b1",
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_s4hana",
        "omega_cartridge_sap_sf",
        "omega_cartridge_sap_successfactors",
        "omega_cartridge_successfactors",
    ):
        assert f"'{role}'" in roles
    assert "'omega_cartridge_replicon'," not in roles.split("IF EXISTS", 1)[0]
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA knowledge_bits FROM %I" in roles
    assert "REVOKE ALL ON SCHEMA knowledge_bits FROM %I" in roles
    assert "REVOKE ALL ON SCHEMA knowledge_bits_quarantine FROM omega_cartridge_replicon" in roles


def test_statements_outside_plpgsql_blocks_parse_as_postgres():
    sql = _sql()
    outside = re.sub(r"DO \$(\w+)\$.*?\$\1\$;", "", sql, flags=re.DOTALL)
    outside = re.sub(r"CREATE OR REPLACE FUNCTION.*?\$\$;", "", outside, flags=re.DOTALL)
    outside = "\n".join(line for line in outside.splitlines() if not line.lstrip().startswith("--"))
    statements = [part.strip() for part in outside.split(";") if part.strip()]
    parsed = [sqlglot.parse_one(statement, read="postgres") for statement in statements]
    assert len(parsed) == len(statements) >= 4
