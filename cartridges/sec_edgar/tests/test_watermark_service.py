from app.services.watermark_service import PostgresWatermarkStore, _psycopg_dsn


def test_psycopg_dsn_accepts_sqlalchemy_postgres_scheme() -> None:
    assert (
        _psycopg_dsn("postgresql+psycopg2://user:pass@postgres:5432/modecissions")
        == "postgresql://user:pass@postgres:5432/modecissions"
    )


def test_postgres_watermark_store_normalizes_database_url() -> None:
    store = PostgresWatermarkStore("postgresql+psycopg2://user:pass@postgres:5432/modecissions")
    assert store.database_url == "postgresql://user:pass@postgres:5432/modecissions"
