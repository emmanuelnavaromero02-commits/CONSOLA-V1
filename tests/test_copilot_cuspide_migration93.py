"""Sprint v1.45 cúspide — migration 93 static shape tests.

Read the migration file off disk and assert the contract the rest of
the cúspide modules rely on: three tables, the right columns, the
self-register pattern. No database connection required.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "infra" / "init" / "93_copilot_cuspide.sql"
)


@pytest.fixture(scope="module")
def sql_text() -> str:
    assert MIGRATION_PATH.exists(), f"migration missing: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_self_registers(sql_text: str) -> None:
    assert "INSERT INTO schema_migrations" in sql_text
    assert "'93_copilot_cuspide.sql'" in sql_text
    assert "ON CONFLICT (filename) DO NOTHING" in sql_text


def test_creates_copilot_goals_table(sql_text: str) -> None:
    block = _table_block(sql_text, "copilot_goals")
    for col in (
        "id", "user_id", "workspace_id", "conversation_id",
        "goal_text", "plan_summary", "status",
        "impact_estimate", "outcome_summary", "workflow_ids",
        "metadata", "created_at", "finished_at",
    ):
        assert re.search(rf"\b{col}\b", block), f"missing column in copilot_goals: {col}"
    assert "DEFAULT 'planning'" in block
    assert "REFERENCES users(id)" in block


def test_creates_copilot_lessons_table(sql_text: str) -> None:
    block = _table_block(sql_text, "copilot_lessons")
    for col in (
        "id", "user_id", "workspace_id", "scope",
        "trigger_pattern", "lesson_text",
        "source_kind", "source_ref",
        "confidence", "applies_to", "enabled",
        "hits", "last_used_at", "created_at",
    ):
        assert re.search(rf"\b{col}\b", block), f"missing column in copilot_lessons: {col}"
    # confidence must be bounded
    assert "CHECK (confidence" in block


def test_creates_copilot_watchdogs_table(sql_text: str) -> None:
    block = _table_block(sql_text, "copilot_watchdogs")
    for col in (
        "id", "cartridge_id", "slug", "name", "description",
        "intent_keywords", "agent_slug", "tools",
        "risk_level", "enabled", "metadata", "created_at",
    ):
        assert re.search(rf"\b{col}\b", block), f"missing column in copilot_watchdogs: {col}"
    assert "UNIQUE (cartridge_id, slug)" in block
    assert "CHECK (risk_level" in block


def test_indexes_present(sql_text: str) -> None:
    expected = [
        "idx_copilot_goals_user_created",
        "idx_copilot_goals_user_status",
        "idx_copilot_goals_active",
        "idx_copilot_lessons_user_enabled",
        "idx_copilot_watchdogs_enabled",
    ]
    for idx in expected:
        assert idx in sql_text, f"missing index: {idx}"


def test_workspace_id_is_uuid_not_bigint(sql_text: str) -> None:
    """workspaces.id is UUID in infra/init/13_rbac_models.sql, so the
    workspace_id columns in this migration must be UUID too — otherwise
    any FK lookup against workspaces would silently fail to match.
    Regression guard for the v1.45 bring-up audit."""
    for table in ("copilot_goals", "copilot_lessons"):
        block = _table_block(sql_text, table)
        # match: `workspace_id    UUID,`  (with whitespace tolerance)
        assert re.search(r"workspace_id\s+UUID\b", block, re.IGNORECASE), (
            f"{table}.workspace_id must be UUID, not BIGINT"
        )


def test_no_drop_table_statements(sql_text: str) -> None:
    """Migrations in this project are additive — no DROP TABLE should
    sneak in or earlier deploys lose data on re-apply."""
    assert not re.search(
        r"\bDROP\s+TABLE\b", sql_text, re.IGNORECASE,
    ), "migration must not DROP tables"


def _table_block(sql: str, table: str) -> str:
    """Slice from the CREATE TABLE for `table` to the next blank line
    after the closing );  so we can grep columns without hitting other
    tables' columns by accident."""
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\((.*?)\);",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"CREATE TABLE for {table} not found"
    return match.group(1)
