from pathlib import Path

from app.services.watermark_service import PostgresWatermarkStore, _psycopg_dsn


def test_inegi_watermark_rls_migration_scopes_runtime_role() -> None:
    sql = (
        Path(__file__).resolve()
        .parents[3]
        .joinpath("infra/init/99zu_inegi_entity_watermarks_rls.sql")
        .read_text(encoding="utf-8")
    )
    assert "omega_cartridge_inegi" in sql
    assert "entity_watermarks_inegi_workspace_rls" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql


def test_inegi_default_connection_migration_sets_default_conn_id() -> None:
    sql = (
        Path(__file__).resolve()
        .parents[3]
        .joinpath("infra/init/99zv_inegi_default_connection_id.sql")
        .read_text(encoding="utf-8")
    )
    assert "connection_id = 'default'" in sql
    assert "cartridge_id = 'inegi'" in sql
    assert "entity = 'series_observations'" in sql


def test_psycopg_dsn_accepts_sqlalchemy_postgres_scheme() -> None:
    assert (
        _psycopg_dsn("postgresql+psycopg2://user:pass@postgres:5432/modecissions")
        == "postgresql://user:pass@postgres:5432/modecissions"
    )


def test_postgres_watermark_store_normalizes_database_url() -> None:
    store = PostgresWatermarkStore("postgresql+psycopg2://user:pass@postgres:5432/modecissions")
    assert store.database_url == "postgresql://user:pass@postgres:5432/modecissions"
