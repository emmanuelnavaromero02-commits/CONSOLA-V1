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
import logging
import re
from pathlib import Path

import pytest

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


# ── runtime provisioning, and the two allowlists a new domain must join ──────


class _RecordingConn:
    """Records the SQL ensure_domain_monitor issues, in order."""

    def __init__(self, *, update_rows: int) -> None:
        self.update_rows = update_rows
        self.statements: list[str] = []

    async def execute(self, sql: str, *args):
        self.statements.append(sql)
        if "UPDATE agents" in sql:
            return f"UPDATE {self.update_rows}"
        return "INSERT 0 1"

    def transaction(self, **kwargs):
        return _NullCtx()


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class _Acquire:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _ctx(tenant: str | None = "11111111-1111-4111-8111-111111111111",
         workspace: str | None = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"):
    def build(_user):
        return {"tenant_id": tenant, "workspace_id": workspace}

    return build


@pytest.mark.asyncio
async def test_ensure_updates_an_existing_row_without_inserting() -> None:
    from app.domains.agentops.domain_monitor_support import ensure_domain_monitor

    conn = _RecordingConn(update_rows=1)

    async def pool():
        return _Pool(conn)

    await ensure_domain_monitor(
        {}, DOMAIN_MONITOR_SPECS[0],
        get_db_pool=pool, build_security_context=_ctx(), logger=logging.getLogger(),
    )
    # The RLS GUCs are set inside the transaction, before touching agents.
    assert "set_config('app.tenant_id'" in conn.statements[0]
    assert any("UPDATE agents" in sql for sql in conn.statements)
    assert not any("INSERT INTO agents" in sql for sql in conn.statements)


@pytest.mark.asyncio
async def test_ensure_inserts_when_the_row_is_missing() -> None:
    from app.domains.agentops.domain_monitor_support import ensure_domain_monitor

    conn = _RecordingConn(update_rows=0)

    async def pool():
        return _Pool(conn)

    await ensure_domain_monitor(
        {}, DOMAIN_MONITOR_SPECS[0],
        get_db_pool=pool, build_security_context=_ctx(), logger=logging.getLogger(),
    )
    assert any("INSERT INTO agents" in sql for sql in conn.statements)
    # The INSERT guards itself, so two concurrent callers cannot both insert.
    insert = next(sql for sql in conn.statements if "INSERT INTO agents" in sql)
    assert "WHERE NOT EXISTS" in insert


@pytest.mark.asyncio
async def test_ensure_is_a_no_op_without_workspace_scope() -> None:
    from app.domains.agentops.domain_monitor_support import ensure_domain_monitor

    conn = _RecordingConn(update_rows=1)

    async def pool():  # pragma: no cover - must never be reached
        raise AssertionError("the pool must not be opened without scope")

    await ensure_domain_monitor(
        {}, DOMAIN_MONITOR_SPECS[0],
        get_db_pool=pool, build_security_context=_ctx(workspace=None),
        logger=logging.getLogger(),
    )
    assert conn.statements == []


@pytest.mark.asyncio
async def test_ensure_fails_open_and_logs() -> None:
    from app.domains.agentops.domain_monitor_support import ensure_domain_monitor

    class _Logger:
        def __init__(self) -> None:
            self.warnings: list[tuple] = []

        def warning(self, *args, **kwargs) -> None:
            self.warnings.append(args)

    async def pool():
        raise RuntimeError("database is gone")

    logger = _Logger()
    # A broken database must leave the row unrepaired and log, never break the
    # caller: this runs inside a sync that is doing other useful work.
    await ensure_domain_monitor(
        {}, DOMAIN_MONITOR_SPECS[0],
        get_db_pool=pool, build_security_context=_ctx(), logger=logger,
    )
    assert logger.warnings


def test_sync_now_repairs_the_domain_monitors_for_its_cartridge() -> None:
    source = (ROOT / "console/app/domains/pipeline/agentops_refresh.py").read_text(
        encoding="utf-8"
    )
    # The rows come from a one-shot migration, so a workspace created later needs
    # this call or its monitors never fire.
    assert "ensure_domain_monitor(" in source
    assert "spec.cartridge_id == cartridge" in source
    # And the AgentOps step must no longer be gated on the SuccessFactors literal.
    assert "AGENTOPS_MONITOR_CARTRIDGES" in source
    assert 'if cartridge == "sap_successfactors":\n        can_run_agentops' not in source


def test_every_monitor_domain_is_in_the_control_room_label_allowlist() -> None:
    from app.schemas.control_room_summary_responses import _DOMAIN_LABELS

    # _count_breakdown drops any key that is not in this map, so a domain missing
    # here means the monitor's alerts are counted and then vanish from the summary.
    for spec in DOMAIN_MONITOR_SPECS:
        assert spec.domain.lower() in _DOMAIN_LABELS, spec.domain
        assert _DOMAIN_LABELS[spec.domain.lower()] == spec.domain


def test_the_write_tool_returns_a_name_not_a_slug() -> None:
    source = (ROOT / "mcp-infra/app/tools/control_room.py").read_text(encoding="utf-8")
    body = source.split("async def control_room__agent_memory_write(", 1)[1].split(
        "\n@tool(", 1
    )[0]
    # Attribution travels as a display name on both sides of the boundary.
    assert '"agent_name": scope["agent_name"] or None' in body
    assert 'scope["agent_slug"]' not in body
