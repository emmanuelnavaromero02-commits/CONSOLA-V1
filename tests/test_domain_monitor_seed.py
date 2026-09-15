"""Mission 4 — the monitor seed migration must not drift from the Python contracts.

Talent has five SQL variants of its contract (99t, 99zf, 99zi, 99zk, 99zm) and they
drifted from ``successfactors_talent_monitor_contract()`` in three separate ways:
the Python side dropped ``"model_version": "bayesian_calibration.v1"``, it moved to
a v2 dataset and model_version the original seed never learned, and the
``recommended_action`` wording diverged. Each divergence means a runtime repair
silently rewrites what a seed wrote.

This file makes that class of bug impossible for the three new monitors: it parses
the migration and asserts the stored JSON is byte-equal to what the Python contract
builds. Mould: tests/test_sap_successfactors_agentops_monitor_seed.py, which reads
the SQL as text rather than running it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.domains.agentops.domain_monitors import DOMAIN_MONITOR_SPECS
from app.domains.agentops.domain_monitor_support import build_contract

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/init/99zzzzh_domain_agentops_monitors.sql"
# The migration that owns the shared-memory table the monitors' tools write to.
MEMORY_MIGRATION = ROOT / "infra/init/99zzzzg_agent_shared_findings.sql"
TALENT_MIGRATION = ROOT / "infra/init/99t_sap_successfactors_talent_agentops_monitor.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _slug_blocks(sql: str) -> dict[str, str]:
    """Split the payload CTE into one text block per agent slug."""
    blocks: dict[str, str] = {}
    for spec in DOMAIN_MONITOR_SPECS:
        marker = f"'{spec.slug}'::text AS slug"
        anchor = sql.index(marker)
        # Walk back to this SELECT so the block also covers the columns declared
        # before slug, and forward to the next UNION ALL (or the next CTE).
        start = sql.rindex("    SELECT", 0, anchor)
        nxt = sql.find("    UNION ALL", anchor)
        end = nxt if nxt != -1 else sql.index("updated AS (", anchor)
        blocks[spec.slug] = sql[start:end]
    return blocks


def _sql_without_comments(sql: str) -> str:
    """Drop whole-line ``--`` comments.

    The migration's header explains the Talent precedent by name, which is
    documentation rather than a change to Talent, so assertions about what the
    migration DOES must not read its prose.
    """
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def _jsonb_literal(block: str, column: str) -> object:
    """Extract the JSON behind ``'...'::jsonb AS <column>`` from a payload block."""
    match = re.search(
        r"'((?:[^']|'')*)'::jsonb AS " + re.escape(column),
        block,
        re.DOTALL,
    )
    assert match, f"no jsonb literal for {column}"
    return json.loads(match.group(1).replace("''", "'"))


def test_migration_exists_and_sorts_after_the_memory_table() -> None:
    # Docker runs /docker-entrypoint-initdb.d in lexicographic order, so the
    # monitors (which reference the memory tools) must apply after the table.
    assert MIGRATION.is_file()
    assert MEMORY_MIGRATION.is_file()
    assert MEMORY_MIGRATION.name < MIGRATION.name


def test_stored_contract_is_byte_equal_to_the_python_contract() -> None:
    blocks = _slug_blocks(_sql())
    for spec in DOMAIN_MONITOR_SPECS:
        allowed_tools, rag_filter, extra = build_contract(spec)
        block = blocks[spec.slug]
        assert _jsonb_literal(block, "allowed_tools") == allowed_tools, spec.slug
        assert _jsonb_literal(block, "rag_filter") == rag_filter, spec.slug
        assert _jsonb_literal(block, "extra") == extra, spec.slug


def test_scalar_columns_match_the_spec() -> None:
    blocks = _slug_blocks(_sql())
    for spec in DOMAIN_MONITOR_SPECS:
        block = blocks[spec.slug]
        assert f"'{spec.cartridge_id}'::text AS cartridge_id" in block
        assert f"'{spec.name}'::text AS name" in block
        assert f"'{spec.model}'::text AS model" in block
        assert f"{spec.max_tokens}::int AS max_tokens" in block
        assert f"{spec.temperature}::real AS temperature" in block
        # The prompt is dollar-quoted, so it can contain whatever punctuation the
        # Spanish needs without escaping.
        assert spec.instructions in block
        assert spec.personality in block


def test_rows_are_workspace_scoped_not_global_templates() -> None:
    sql = _sql()
    # omega_rls_workspace_matches() returns false for a NULL workspace_id and
    # sync_agentops_monitor_candidates skips an agent missing either id, so a
    # monitor MUST carry tenant_id and workspace_id.
    assert "tenant_id, workspace_id, cartridge_id, slug" in sql
    assert "scope.tenant_id" in sql and "scope.workspace_id" in sql
    assert "FROM cartridge_installations ci" in sql
    assert "ci.status = 'ready'" in sql
    assert "COALESCE(te.status, 'active') = 'active'" in sql
    # Fallback for a fresh install with no ready installation yet.
    assert "NOT EXISTS (SELECT 1 FROM active_scope)" in sql
    assert "'Default Tenant'" in sql and "'Main Workspace'" in sql


def test_upsert_is_idempotent_and_correlated_per_agent() -> None:
    sql = _sql()
    assert "updated AS (" in sql
    assert "RETURNING a.workspace_id, a.cartridge_id, a.slug" in sql
    # With three agents in one statement an uncorrelated NOT EXISTS would suppress
    # the INSERT of the other two the moment any one of them was updated.
    assert "FROM updated u" in sql
    assert "u.workspace_id = p.workspace_id" in sql
    assert "u.cartridge_id = p.cartridge_id" in sql
    assert "u.slug = p.slug" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_conversational_agents_gain_their_domain_read_and_lose_nothing() -> None:
    sql = _sql()
    tail = sql.split("WITH kpi_tool", 1)[1]
    for spec in DOMAIN_MONITOR_SPECS:
        base_slug = spec.slug.removesuffix("_monitor")
        assert f"'{base_slug}'" in tail
        assert f"'mcp-infra__{spec.kpi_tool}'" in tail
    # Append-only, and a no-op on re-run.
    assert "allowed_tools = a.allowed_tools || to_jsonb(k.tool)" in tail
    assert "NOT (a.allowed_tools @> to_jsonb(k.tool))" in tail
    # Nothing about the conversational rows' identity or model is touched.
    for forbidden in ("SET slug", "personality =", "instructions =", "model ="):
        assert forbidden not in tail, forbidden


def test_model_is_documented_as_per_row_adjustable() -> None:
    sql = _sql()
    # The comment lives in the SQL, so this one reads the file WITH its prose.
    # Mission 4 part C: the comment has to say a future upgrade needs no code.
    assert "Model is per row on purpose" in sql
    assert "no code change" in sql
    assert "system default" in sql
    for spec in DOMAIN_MONITOR_SPECS:
        assert f"'{spec.model}'::text AS model" in sql
    assert "opus" not in sql.lower()


def test_talent_seed_is_untouched() -> None:
    # Mission 4 must not rewrite Talent. Its own seed still owns WB-TALENTO and
    # its own slug, and this migration must not mention either.
    talent = TALENT_MIGRATION.read_text(encoding="utf-8")
    assert "sap_successfactors_talent_monitor" in talent
    assert '"wisdom_bit_id": "WB-TALENTO"' in talent
    statements = _sql_without_comments(_sql())
    assert "WB-TALENTO" not in statements
    assert "sap_successfactors_talent_monitor" not in statements
    assert "sap_successfactors" not in statements


def test_migration_is_not_destructive() -> None:
    sql = _sql_without_comments(_sql()).lower()
    assert not re.search(r"\bdelete\s+from\b", sql)
    assert "truncate" not in sql
    assert not re.search(r"\bdrop\s+(table|column|policy|index)\b", sql)
    # It never rewrites another migration's rows wholesale.
    assert "update agents a\n   set allowed_tools" in sql or "update agents" in sql


def test_every_wisdom_bit_and_slug_is_unique() -> None:
    slugs = [spec.slug for spec in DOMAIN_MONITOR_SPECS]
    wisdom_bits = [spec.wisdom_bit_id for spec in DOMAIN_MONITOR_SPECS]
    crons = [spec.cron for spec in DOMAIN_MONITOR_SPECS]
    assert len(set(slugs)) == len(slugs)
    assert len(set(wisdom_bits)) == len(wisdom_bits)
    assert len(set(crons)) == len(crons)
    assert "WB-TALENTO" not in wisdom_bits


def test_memory_table_migration_is_append_only_and_scoped() -> None:
    sql = MEMORY_MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS agent_shared_findings" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    # An agent must not be able to erase what another agent recorded.
    assert "REVOKE DELETE, TRUNCATE ON agent_shared_findings" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON agent_shared_findings" in sql
    # mcp-infra writes this table directly, the way it writes control_room_items.
    assert "omega_mcp_infra" in sql
    # The finding_type vocabulary is a database invariant, not a convention.
    assert "'data_gap', 'error', 'insight', 'warning'" in sql
