"""Sprint v1.42 — pins the v1.42 copilot indexes migration."""
from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1] / "infra" / "init"
    / "41_copilot_query_indexes.sql"
)


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_exists():
    assert MIGRATION.exists()


def test_partial_index_for_pending_approvals():
    src = _src()
    assert "idx_messages_pending_approval" in src
    assert "ON conversation_messages" in src
    # Partial index: only indexes the rows that matter (pending).
    assert "WHERE tool_calls IS NOT NULL" in src
    assert "tool_results IS NULL" in src


def test_migration_is_idempotent():
    src = _src()
    create_lines = [ln for ln in src.splitlines() if ln.strip().startswith("CREATE INDEX")]
    assert len(create_lines) == 1
    assert "IF NOT EXISTS" in create_lines[0]


def test_migration_ordering():
    """41 must run after 38 (conversations + conversation_messages) and
    after 40 (the v1.41.1 indexes) so we don't shuffle existing files."""
    init = Path(__file__).resolve().parents[1] / "infra" / "init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("38_copilot_conversations.sql") < names.index("41_copilot_query_indexes.sql")
    assert names.index("40_v141_observability_indexes.sql") < names.index("41_copilot_query_indexes.sql")
