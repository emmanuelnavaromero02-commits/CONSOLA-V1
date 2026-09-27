from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import postgres_with_real_init_schema  # noqa: F401


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99zzzzw_entity_cron_timezone.sql"
BLANK_CRON_MIGRATION = REPO / "infra" / "init" / "99zzzzy_entity_config_blank_cron.sql"
MUTATIONS = REPO / "console" / "app" / "domains" / "studio" / "entity_mutations.py"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_adds_utc_default_column_and_validated_format_check():
    sql = _sql()
    assert "ADD COLUMN IF NOT EXISTS cron_timezone TEXT NOT NULL DEFAULT 'UTC'" in sql
    assert "entity_config_cron_timezone_format_check" in sql
    assert ") NOT VALID;" in sql
    assert "VALIDATE CONSTRAINT entity_config_cron_timezone_format_check" in sql
    assert "to_regclass('public.entity_config') IS NULL" in sql
    assert "VALUES ('99zzzzw_entity_cron_timezone.sql', NOW())" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|UPDATE)\b", sql)


def test_database_and_console_accept_the_same_timezone_shape():
    sql_pattern = re.search(r"cron_timezone ~ '([^']+)'", _sql()).group(1)
    py_pattern = re.search(r'_TIMEZONE_RE = re\.compile\(r"([^"]+)"\)', MUTATIONS.read_text(encoding="utf-8")).group(1)
    assert sql_pattern == f"^{py_pattern}$"


async def _probe(dsn: str) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    cartridge_id = f"tzprobe_{uuid.uuid4().hex[:10]}"
    try:
        column = await conn.fetchrow(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='entity_config' AND column_name='cron_timezone'"
        )
        validated = await conn.fetchval(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conname='entity_config_cron_timezone_format_check' "
            "AND conrelid='public.entity_config'::regclass"
        )
        recorded = await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename='99zzzzw_entity_cron_timezone.sql'"
        )
        await conn.execute(_sql())
        await conn.execute(_sql())
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO entity_config (cartridge_id, entity) VALUES ($1, 'Default')",
                cartridge_id,
            )
            default_tz = await conn.fetchval(
                "SELECT cron_timezone FROM entity_config WHERE cartridge_id=$1 AND entity='Default'",
                cartridge_id,
            )
            await conn.execute(
                "INSERT INTO entity_config (cartridge_id, entity, cron_timezone) "
                "VALUES ($1, 'Buenos', 'America/Argentina/Buenos_Aires')",
                cartridge_id,
            )
            rejected = []
            for bad in ("../etc/passwd", "America/Mexico_City; x", "", "UTC\n", "a" * 65):
                try:
                    async with conn.transaction():
                        await conn.execute(
                            "INSERT INTO entity_config (cartridge_id, entity, cron_timezone) "
                            "VALUES ($1, 'Bad', $2)",
                            cartridge_id,
                            bad,
                        )
                except asyncpg.CheckViolationError:
                    rejected.append(bad)
            raise _Rollback({
                "column": dict(column) if column else None,
                "validated": validated,
                "recorded": recorded,
                "default_tz": default_tz,
                "rejected": rejected,
            })
    except _Rollback as done:
        return done.result
    finally:
        await conn.close()


class _Rollback(Exception):
    def __init__(self, result: dict[str, object]):
        super().__init__("rollback")
        self.result = result


def test_live_migration_defaults_to_utc_and_rejects_malformed_zones(
    postgres_with_real_init_schema: str,  # noqa: F811
) -> None:
    result = asyncio.run(_probe(postgres_with_real_init_schema))
    assert result["column"] == {"is_nullable": "NO", "column_default": "'UTC'::text"}
    assert result["validated"] is True
    assert result["recorded"] == 1
    assert result["default_tz"] == "UTC"
    assert result["rejected"] == ["../etc/passwd", "America/Mexico_City; x", "", "UTC\n", "a" * 65]


def test_blank_cron_migration_is_idempotent_and_recorded():
    sql = BLANK_CRON_MIGRATION.read_text(encoding="utf-8")
    assert "UPDATE entity_config SET cron_expression = NULL WHERE cron_expression = ''" in sql
    assert "to_regclass('public.entity_config') IS NULL" in sql
    assert "VALUES ('99zzzzy_entity_config_blank_cron.sql', NOW())" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|ALTER)\b", sql)


async def _blank_cron_probe(dsn: str) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    cartridge_id = f"blankcron_{uuid.uuid4().hex[:10]}"
    sql = BLANK_CRON_MIGRATION.read_text(encoding="utf-8")
    try:
        await conn.execute(
            "INSERT INTO entity_config (cartridge_id, entity, trigger_type, cron_expression) "
            "VALUES ($1, 'Legacy', 'scheduled', ''), ($1, 'Kept', 'scheduled', '0 8 * * *')",
            cartridge_id,
        )
        await conn.execute(sql)
        await conn.execute(sql)
        rows = await conn.fetch(
            "SELECT entity, cron_expression FROM entity_config WHERE cartridge_id=$1 ORDER BY entity",
            cartridge_id,
        )
        recorded = await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename='99zzzzy_entity_config_blank_cron.sql'"
        )
        return {
            "rows": {row["entity"]: row["cron_expression"] for row in rows},
            "recorded": recorded,
        }
    finally:
        await conn.execute("DELETE FROM entity_config WHERE cartridge_id=$1", cartridge_id)
        await conn.close()


def test_live_blank_cron_rows_become_null_without_touching_real_schedules(
    postgres_with_real_init_schema: str,  # noqa: F811
) -> None:
    result = asyncio.run(_blank_cron_probe(postgres_with_real_init_schema))
    assert result["rows"] == {"Legacy": None, "Kept": "0 8 * * *"}
    assert result["recorded"] == 1
