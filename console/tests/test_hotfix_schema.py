from pathlib import Path


def test_token_usage_schema_includes_cache_columns():
    schema = Path("infra/init/00_schema.sql").read_text(encoding="utf-8")
    migration = Path("infra/init/14_token_usage_cache_columns.sql").read_text(encoding="utf-8")

    assert "cache_creation_tokens INTEGER NOT NULL DEFAULT 0" in schema
    assert "cache_read_tokens INTEGER NOT NULL DEFAULT 0" in schema
    assert "ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cache_creation_tokens" in migration
    assert "ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cache_read_tokens" in migration
