from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
import pytest

from omega_cartridge_kit import kb_sink
from omega_cartridge_kit.kb_sink import KbSinkError, write_scoped_kb_table

REPO = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"


class _Result:
    def __init__(self, value=None, row=None):
        self._value = value
        self._row = row

    def scalar(self):
        return self._value

    def first(self):
        return self._row


class _Connection:
    def __init__(self, *, schema_oid=1, existing=None):
        self.schema_oid = schema_oid
        self.existing = existing
        self.statements: list[tuple[str, dict | None]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params))
        if "to_regnamespace" in sql:
            return _Result(self.schema_oid)
        if "FROM pg_class rel" in sql:
            return _Result(row=self.existing)
        return _Result()


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_exc):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection
        self.begins = 0

    def begin(self):
        self.begins += 1
        return _Transaction(self.connection)


@pytest.fixture
def written(monkeypatch):
    calls: list[dict] = []

    def fake_to_sql(frame, name, con, schema=None, if_exists="fail", index=True, **_kwargs):
        calls.append(
            {
                "frame": frame.copy(),
                "name": name,
                "con": con,
                "schema": schema,
                "if_exists": if_exists,
                "index": index,
            }
        )
        return len(frame)

    monkeypatch.setattr(pd.DataFrame, "to_sql", fake_to_sql)
    return calls


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"project": ["alpha", "beta"], "hours": [2.5, 7.0]})


def _sql(connection: _Connection) -> list[str]:
    return [sql for sql, _ in connection.statements]


def test_creates_scoped_table_with_forced_rls_in_one_transaction(written):
    connection = _Connection(existing=None)
    engine = _Engine(connection)
    frame = _frame()

    rows = write_scoped_kb_table(
        engine, table="replicon_project_status", df=frame, tenant_id=TENANT, workspace_id=WORKSPACE
    )

    assert rows == 2
    assert engine.begins == 1
    statements = _sql(connection)
    assert "set_config('app.tenant_id', :tenant, true)" in statements[0]
    assert "set_config('app.workspace_id', :workspace, true)" in statements[0]
    assert connection.statements[0][1] == {"tenant": TENANT, "workspace": WORKSPACE}
    assert "to_regnamespace" in statements[1]
    assert "pg_get_userbyid(rel.relowner) = current_user" in statements[2]
    qualified = 'knowledge_bits."replicon_project_status"'
    tail = statements[3:]
    assert tail == [
        f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS tenant_id TEXT",
        f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS workspace_id TEXT",
        f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
        f"REVOKE ALL ON TABLE {qualified} FROM PUBLIC",
        f"DROP POLICY IF EXISTS kb_workspace_scope ON {qualified}",
        f"CREATE POLICY kb_workspace_scope ON {qualified} "
        "USING (omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text)) "
        "WITH CHECK (omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text))",
        f"DELETE FROM {qualified} WHERE tenant_id::text = :tenant AND workspace_id::text = :workspace",
    ]
    assert connection.statements[-1][1] == {"tenant": TENANT, "workspace": WORKSPACE}

    create, insert = written
    assert create["frame"].empty
    assert list(create["frame"].columns) == ["project", "hours", "tenant_id", "workspace_id"]
    for call in written:
        assert call["con"] is connection
        assert call["schema"] == "knowledge_bits"
        assert call["name"] == "replicon_project_status"
        assert call["if_exists"] == "append"
        assert call["index"] is False
    assert insert["frame"]["tenant_id"].tolist() == [TENANT, TENANT]
    assert insert["frame"]["workspace_id"].tolist() == [WORKSPACE, WORKSPACE]
    assert list(frame.columns) == ["project", "hours"]


def test_existing_owned_table_is_rescoped_without_recreating(written):
    connection = _Connection(existing=("r", True))
    write_scoped_kb_table(
        _Engine(connection), table="kb_table", df=_frame(), tenant_id=TENANT, workspace_id=WORKSPACE
    )
    assert len(written) == 1
    assert not written[0]["frame"].empty
    assert any("FORCE ROW LEVEL SECURITY" in sql for sql in _sql(connection))


def test_caller_supplied_scope_columns_are_overwritten(written):
    frame = _frame().assign(tenant_id="other-tenant", workspace_id="other-workspace")
    write_scoped_kb_table(
        _Engine(_Connection(existing=("r", True))),
        table="kb_table",
        df=frame,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    )
    inserted = written[-1]["frame"]
    assert set(inserted["tenant_id"]) == {TENANT}
    assert set(inserted["workspace_id"]) == {WORKSPACE}
    assert set(frame["tenant_id"]) == {"other-tenant"}


@pytest.mark.parametrize(
    "existing,message",
    [
        (("r", False), "owned by another role"),
        (("v", True), "not a plain table"),
        (("m", True), "not a plain table"),
        (("p", True), "not a plain table"),
    ],
)
def test_foreign_or_non_table_targets_are_refused_before_any_write(written, existing, message):
    connection = _Connection(existing=existing)
    with pytest.raises(KbSinkError, match=message):
        write_scoped_kb_table(
            _Engine(connection), table="kb_table", df=_frame(), tenant_id=TENANT, workspace_id=WORKSPACE
        )
    assert written == []
    assert len(connection.statements) == 3


def test_missing_schema_is_not_created_by_the_writer(written):
    connection = _Connection(schema_oid=None)
    with pytest.raises(KbSinkError, match="not provisioned"):
        write_scoped_kb_table(
            _Engine(connection), table="kb_table", df=_frame(), tenant_id=TENANT, workspace_id=WORKSPACE
        )
    assert written == []
    assert not any("CREATE SCHEMA" in sql for sql in _sql(connection))


@pytest.mark.parametrize(
    "table",
    [
        "",
        "Kb_Table",
        "1kb",
        'kb"; DROP TABLE users; --',
        "kb table",
        "knowledge_bits.kb",
        "kb-table",
        "k" * 64,
        "tábla",
        None,
        123,
    ],
)
def test_table_name_must_be_a_strict_identifier(written, table):
    connection = _Connection()
    with pytest.raises(ValueError):
        write_scoped_kb_table(
            _Engine(connection), table=table, df=_frame(), tenant_id=TENANT, workspace_id=WORKSPACE
        )
    assert connection.statements == []


@pytest.mark.parametrize(
    "tenant,workspace",
    [
        ("", WORKSPACE),
        (TENANT, ""),
        (None, WORKSPACE),
        ("a/b", WORKSPACE),
        (TENANT, "w'; --"),
        ("t" * 129, WORKSPACE),
    ],
)
def test_scope_is_required_before_touching_the_database(written, tenant, workspace):
    connection = _Connection()
    with pytest.raises(PermissionError):
        write_scoped_kb_table(
            _Engine(connection), table="kb_table", df=_frame(), tenant_id=tenant, workspace_id=workspace
        )
    assert connection.statements == []


def test_the_policy_uses_the_migration_function():
    migration = (REPO / "infra/init/99zzzzs_knowledge_bits_workspace_rls.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION omega_rls_workspace_text_matches(row_tenant text, row_workspace text)" in migration
    assert kb_sink._SCOPE_PREDICATE in migration
    assert f"CREATE POLICY {kb_sink.KB_POLICY} ON knowledge_bits.%I TO PUBLIC" in migration


def test_only_replicon_keeps_a_postgres_kb_sink():
    replicon = (REPO / "cartridges/replicon/app/services/duckdb_service.py").read_text(encoding="utf-8")
    assert "from omega_cartridge_kit.kb_sink import write_scoped_kb_table" in replicon
    assert 'if_exists="replace"' not in replicon
    for cartridge in ("hubspot", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors"):
        for name in ("duckdb_service.py", "kb_service.py"):
            source = (REPO / f"cartridges/{cartridge}/app/services/{name}").read_text(encoding="utf-8")
            assert "write_kb_to_postgres" not in source, (cartridge, name)
            assert "knowledge_bits." not in source, (cartridge, name)
            assert ".to_sql(" not in source, (cartridge, name)
    mcp = (REPO / "mcp-infra/app/tools/cartridges.py").read_text(encoding="utf-8")
    run_kb = mcp.split("def cartridge_run_kb(", 1)[1].split("@tool(", 1)[0]
    assert "to_sql" not in run_kb
    assert "knowledge_bits" not in run_kb
    assert "create_engine" not in run_kb



def test_replicon_non_managed_kb_writes_go_through_the_kit(monkeypatch):
    from tests.conftest import load_cartridge_app

    load_cartridge_app("replicon")
    import importlib

    request_context = importlib.import_module("app.core.request_context")
    service = importlib.import_module("app.services.duckdb_service")
    calls: list[dict] = []
    disposed: list[bool] = []

    class _FakeEngine:
        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(service, "create_engine", lambda _url: _FakeEngine())
    monkeypatch.setattr(service, "write_scoped_kb_table", lambda engine, **kwargs: calls.append(kwargs) or 2)
    ctx = request_context._sign_security_context(
        {"trusted": True, "source": "console", "tenant_id": TENANT, "workspace_id": WORKSPACE}
    )

    service.write_kb_to_postgres(_frame(), "replicon_project_status", ctx)

    assert len(calls) == 1
    assert calls[0]["table"] == "replicon_project_status"
    assert calls[0]["tenant_id"] == TENANT
    assert calls[0]["workspace_id"] == WORKSPACE
    assert disposed == [True]
    with pytest.raises(request_context.SecurityContextError):
        service.write_kb_to_postgres(_frame(), "replicon_project_status", {"trusted": True})
    assert len(calls) == 1


# Live PostgreSQL: the real init schema, the real Replicon role, FORCE RLS.

sqlalchemy = pytest.importorskip("sqlalchemy")
from tests.test_operational_rls_console_refinement import (  # noqa: E402
    postgres_with_real_init_schema,
)

REPLICON_PASSWORD = "test_omega_cartridge_replicon_password"


def _engine(dsn: str, *, user: str | None = None, password: str | None = None):
    url = sqlalchemy.engine.make_url(dsn.replace("postgresql://", "postgresql+psycopg2://", 1))
    if user:
        url = url.set(username=user, password=password)
    return sqlalchemy.create_engine(url)


def _count(engine, table: str, tenant: str | None, workspace: str | None) -> int:
    with engine.begin() as conn:
        if tenant is not None:
            conn.execute(
                sqlalchemy.text(
                    "SELECT set_config('app.tenant_id', :t, true), set_config('app.workspace_id', :w, true)"
                ),
                {"t": tenant, "w": workspace},
            )
        return conn.execute(
            sqlalchemy.text(f'SELECT count(*) FROM knowledge_bits."{table}"')  # nosec B608 - test-owned name
        ).scalar()


def test_live_replicon_role_writes_forced_rls_tables(postgres_with_real_init_schema):
    admin = _engine(postgres_with_real_init_schema)
    replicon = _engine(postgres_with_real_init_schema, user="omega_cartridge_replicon", password=REPLICON_PASSWORD)
    table = f"kb_live_{uuid.uuid4().hex[:10]}"
    other_tenant, other_workspace = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        assert write_scoped_kb_table(
            replicon, table=table, df=_frame(), tenant_id=TENANT, workspace_id=WORKSPACE
        ) == 2
        assert write_scoped_kb_table(
            replicon,
            table=table,
            df=_frame().head(1),
            tenant_id=other_tenant,
            workspace_id=other_workspace,
        ) == 1

        assert _count(replicon, table, TENANT, WORKSPACE) == 2
        assert _count(replicon, table, other_tenant, other_workspace) == 1
        assert _count(replicon, table, None, None) == 0
        assert _count(replicon, table, TENANT, other_workspace) == 0
        assert _count(admin, table, None, None) == 3

        write_scoped_kb_table(replicon, table=table, df=_frame().head(1), tenant_id=TENANT, workspace_id=WORKSPACE)
        assert _count(replicon, table, TENANT, WORKSPACE) == 1
        assert _count(replicon, table, other_tenant, other_workspace) == 1

        with admin.begin() as conn:
            flags = conn.execute(
                sqlalchemy.text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:name)"
                ),
                {"name": f"knowledge_bits.{table}"},
            ).one()
            policies = conn.execute(
                sqlalchemy.text(
                    "SELECT policyname, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'knowledge_bits' AND tablename = :table"
                ),
                {"table": table},
            ).all()
        assert tuple(flags) == (True, True)
        assert [row.policyname for row in policies] == ["kb_workspace_scope"]
        assert "omega_rls_workspace_text_matches" in policies[0].qual
        assert "omega_rls_workspace_text_matches" in policies[0].with_check

        with pytest.raises(sqlalchemy.exc.DBAPIError):
            with replicon.begin() as conn:
                conn.execute(
                    sqlalchemy.text(
                        "SELECT set_config('app.tenant_id', :t, true), set_config('app.workspace_id', :w, true)"
                    ),
                    {"t": TENANT, "w": WORKSPACE},
                )
                conn.execute(
                    sqlalchemy.text(
                        f'INSERT INTO knowledge_bits."{table}" (project, hours, tenant_id, workspace_id) '  # nosec B608
                        "VALUES ('x', 1, :t, :w)"
                    ),
                    {"t": other_tenant, "w": other_workspace},
                )
    finally:
        with admin.begin() as conn:
            conn.execute(sqlalchemy.text(f'DROP TABLE IF EXISTS knowledge_bits."{table}"'))
        admin.dispose()
        replicon.dispose()


def test_live_writer_refuses_foreign_owned_tables_and_views(postgres_with_real_init_schema):
    admin = _engine(postgres_with_real_init_schema)
    replicon = _engine(postgres_with_real_init_schema, user="omega_cartridge_replicon", password=REPLICON_PASSWORD)
    suffix = uuid.uuid4().hex[:10]
    foreign, view = f"kb_foreign_{suffix}", f"kb_view_{suffix}"
    try:
        with admin.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    f'CREATE TABLE knowledge_bits."{foreign}" (project TEXT, hours FLOAT, tenant_id TEXT, workspace_id TEXT)'
                )
            )
            conn.execute(sqlalchemy.text(f'GRANT ALL ON knowledge_bits."{foreign}" TO omega_cartridge_replicon'))
            conn.execute(sqlalchemy.text(f'CREATE VIEW knowledge_bits."{view}" AS SELECT 1 AS project'))
        for table in (foreign, view):
            with pytest.raises(KbSinkError):
                write_scoped_kb_table(replicon, table=table, df=_frame(), tenant_id=TENANT, workspace_id=WORKSPACE)
        assert _count(admin, foreign, None, None) == 0
    finally:
        with admin.begin() as conn:
            conn.execute(sqlalchemy.text(f'DROP VIEW IF EXISTS knowledge_bits."{view}"'))
            conn.execute(sqlalchemy.text(f'DROP TABLE IF EXISTS knowledge_bits."{foreign}"'))
        admin.dispose()
        replicon.dispose()


def test_live_migration_scopes_or_quarantines_every_knowledge_bits_table(postgres_with_real_init_schema):
    migration = (REPO / "infra/init/99zzzzs_knowledge_bits_workspace_rls.sql").read_text(encoding="utf-8")
    admin = _engine(postgres_with_real_init_schema)
    suffix = uuid.uuid4().hex[:8]
    scoped, unscoped, clash = f"kb_scoped_{suffix}", f"kb_unscoped_{suffix}", f"kb_clash_{suffix}"
    clash_oid = None
    try:
        with admin.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    f'CREATE TABLE knowledge_bits."{scoped}" (v INT, tenant_id UUID, workspace_id TEXT);'
                    f'CREATE POLICY allow_everything ON knowledge_bits."{scoped}" USING (true);'
                    f'GRANT SELECT ON knowledge_bits."{scoped}" TO PUBLIC;'
                    f'CREATE TABLE knowledge_bits."{unscoped}" (v INT);'
                    f'CREATE TABLE knowledge_bits."{clash}" (v INT);'
                    f'CREATE TABLE knowledge_bits_quarantine."{clash}" (v INT);'
                )
            )
            clash_oid = conn.execute(
                sqlalchemy.text("SELECT to_regclass(:name)::oid"), {"name": f"knowledge_bits.{clash}"}
            ).scalar()
        for _ in range(2):
            with admin.begin() as conn:
                conn.connection.driver_connection.cursor().execute(migration)
        with admin.begin() as conn:
            flags = conn.execute(
                sqlalchemy.text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:name)"
                ),
                {"name": f"knowledge_bits.{scoped}"},
            ).one()
            policies = conn.execute(
                sqlalchemy.text(
                    "SELECT policyname FROM pg_policies WHERE schemaname='knowledge_bits' AND tablename=:t"
                ),
                {"t": scoped},
            ).scalars().all()
            public_select = conn.execute(
                sqlalchemy.text("SELECT has_table_privilege('public', :name, 'SELECT')"),
                {"name": f"knowledge_bits.{scoped}"},
            ).scalar()
            moved = conn.execute(
                sqlalchemy.text("SELECT to_regclass(:a), to_regclass(:b), to_regclass(:c)"),
                {
                    "a": f"knowledge_bits.{unscoped}",
                    "b": f"knowledge_bits_quarantine.{unscoped}",
                    "c": f"knowledge_bits_quarantine.{clash[:40]}_legacy_{clash_oid}",
                },
            ).one()
            hubspot_usage = conn.execute(
                sqlalchemy.text(
                    "SELECT bool_or(has_schema_privilege(rolname, 'knowledge_bits', 'USAGE')) "
                    "FROM pg_roles WHERE rolname IN ('omega_cartridge_hubspot', 'omega_cartridge_sap_hcm', "
                    "'omega_cartridge_sap_s4', 'omega_cartridge_sap_sf')"
                )
            ).scalar()
            replicon_usage = conn.execute(
                sqlalchemy.text("SELECT has_schema_privilege('omega_cartridge_replicon', 'knowledge_bits', 'CREATE')")
            ).scalar()
        assert tuple(flags) == (True, True)
        assert policies == ["kb_workspace_scope"]
        assert public_select is False
        assert moved[0] is None
        assert moved[1] == f"knowledge_bits_quarantine.{unscoped}"
        assert moved[2] == f"knowledge_bits_quarantine.{clash[:40]}_legacy_{clash_oid}"
        assert hubspot_usage in (False, None)
        assert replicon_usage is True
    finally:
        with admin.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    f'DROP TABLE IF EXISTS knowledge_bits."{scoped}";'
                    f'DROP TABLE IF EXISTS knowledge_bits_quarantine."{unscoped}";'
                    f'DROP TABLE IF EXISTS knowledge_bits_quarantine."{clash}";'
                    f'DROP TABLE IF EXISTS knowledge_bits_quarantine."{clash[:40]}_legacy_{clash_oid}";'
                )
            )
        admin.dispose()
