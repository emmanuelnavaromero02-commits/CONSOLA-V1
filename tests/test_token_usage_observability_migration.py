from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INIT = REPO / "infra" / "init"
MIGRATION = INIT / "99zzzzza_token_usage_observability.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_migration_runs_after_every_existing_migration():
    names = sorted(path.name for path in INIT.glob("[0-9][0-9]*_*.sql"))
    assert names.index(MIGRATION.name) > names.index("99zzzzy_entity_config_blank_cron.sql")


def test_migration_adds_nullable_observability_columns():
    code = _code(_sql())
    assert "ADD COLUMN IF NOT EXISTS duration_ms INTEGER" in code
    assert "ADD COLUMN IF NOT EXISTS surface TEXT" in code
    assert "NOT NULL" not in code
    assert "DEFAULT" not in code.replace("ON CONFLICT (filename) DO NOTHING", "")


def test_migration_constrains_surface_to_known_values():
    code = _code(_sql())
    assert "conname = 'chk_token_usage_surface'" in code
    assert "surface IS NULL" in code
    assert "surface IN ('copilot', 'studio', 'rag', 'catalog', 'workspace', 'other')" in code
    assert "NOT VALID" in code
    assert "VALIDATE CONSTRAINT chk_token_usage_surface" in code


def test_migration_is_idempotent_and_transaction_safe():
    code = _code(_sql())
    assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", code, re.I | re.M)
    assert "CONCURRENTLY" not in code.upper()
    for statement in re.findall(r"ADD COLUMN [^,;]+", code):
        assert "IF NOT EXISTS" in statement, statement
    assert "DROP TABLE" not in code.upper()
    assert "DELETE FROM" not in code.upper()
    assert "UPDATE token_usage" not in code


def test_migration_registers_itself():
    code = _code(_sql())
    assert "INSERT INTO schema_migrations" in code
    assert "'99zzzzza_token_usage_observability.sql'" in code
    assert "ON CONFLICT (filename) DO NOTHING" in code
