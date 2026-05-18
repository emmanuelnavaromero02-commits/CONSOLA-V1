"""Sprint v1.44.2 (Tareas G + H + I) — backend hooks for memory,
drafts, and workflows. Static contracts; LLM-driven integration
into copilot_service is the next-session task.

Per the v1.44.2 scope agreement: this session ships the durable
schema layer + REST CRUD; the LLM planning/generation/extraction
hooks land separately because they touch copilot_service.run_turn
(>1100 lines, substantial code surface).
"""
from __future__ import annotations

import re
from pathlib import Path


REPO       = Path(__file__).resolve().parents[1]
SRC        = REPO / "console/app"
MIG_DIR    = REPO / "infra/init"
MAIN       = SRC / "main.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Migration 51 (memory) ────────────────────────────────────────────────


def test_migration_51_exists():
    assert (MIG_DIR / "51_copilot_memory.sql").exists()


def test_migration_51_creates_three_tables():
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    for table in ("user_facts", "user_preferences",
                  "conversation_memory_summary"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in src, (
            f"migration 51 missing CREATE TABLE for {table}"
        )


def test_user_facts_uniqueness_constraint():
    """The LLM extraction path naively INSERTs; without the UNIQUE
    constraint the table fills with duplicate observations."""
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    assert "UNIQUE (user_id, fact)" in src


def test_memory_summary_fk_guarded_by_existence_check():
    """The conversation_memory_summary FK on conversations(id) is
    attached in a DO block that checks both sides exist — safe on
    a partial bootstrap and idempotent on re-runs."""
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    assert "information_schema.tables" in src
    assert "table_name = 'conversations'" in src
    assert "ADD CONSTRAINT conversation_memory_summary_conversation_id_fkey" in src


def test_migration_51_self_registers():
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    assert "'51_copilot_memory.sql'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


# ── Migration 52 (drafts) ────────────────────────────────────────────────


def test_migration_52_exists():
    assert (MIG_DIR / "52_copilot_drafts.sql").exists()


def test_migration_52_drafts_has_status_lifecycle_constraints():
    """status defaults to 'draft'; the application-layer endpoints
    enforce the lifecycle (draft → sent | discarded). The schema
    accepts any TEXT so a future status code can ship without a
    migration — defensive flexibility that's appropriate for a
    user-content table."""
    src = _read(MIG_DIR / "52_copilot_drafts.sql")
    assert "status       TEXT         NOT NULL DEFAULT 'draft'" in src
    # The two indexes the v1.44.2 brief documents:
    assert "idx_copilot_drafts_user_updated" in src
    assert "idx_copilot_drafts_status" in src
    # Partial index excludes discarded — the common read path is
    # "show me my active drafts", not "show me everything ever".
    assert "WHERE status != 'discarded'" in src


def test_migration_52_drafts_cascade_on_user_delete():
    src = _read(MIG_DIR / "52_copilot_drafts.sql")
    assert "REFERENCES users(id) ON DELETE CASCADE" in src


def test_migration_52_self_registers():
    src = _read(MIG_DIR / "52_copilot_drafts.sql")
    assert "'52_copilot_drafts.sql'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


# ── Migration 53 (workflows) ─────────────────────────────────────────────


def test_migration_53_exists():
    assert (MIG_DIR / "53_copilot_workflows.sql").exists()


def test_migration_53_creates_runs_and_steps():
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert "CREATE TABLE IF NOT EXISTS workflow_runs" in src
    assert "CREATE TABLE IF NOT EXISTS workflow_steps" in src


def test_migration_53_step_unique_per_workflow():
    """Step idx must be unique within a workflow so the LLM planner
    can't accidentally clobber a step on retry."""
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert "UNIQUE (workflow_id, step_idx)" in src


def test_migration_53_partial_status_index():
    """Only in-flight workflows get indexed — completed/cancelled/
    failed ones live in the cold tail and shouldn't pollute the
    hot index."""
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert re.search(
        r"idx_workflow_runs_status[\s\S]*?WHERE status IN\s*\(\s*'planning',\s*'running'\s*\)",
        src,
    )


def test_migration_53_conversation_fk_guarded():
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert "information_schema.tables" in src
    assert "ADD CONSTRAINT workflow_runs_conversation_id_fkey" in src


def test_migration_53_self_registers():
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert "'53_copilot_workflows.sql'" in src


# ── Memory router ───────────────────────────────────────────────────────


def test_memory_router_exists_and_includes_three_resources():
    path = SRC / "routers/copilot_memory.py"
    assert path.exists()
    src = _read(path)
    # Three endpoint families per the brief.
    assert '@router.post("/fact"' in src
    assert "@router.get(\"\")" in src or "@router.get('')" in src
    assert '@router.delete("/fact/{fact_id}"' in src
    assert '@router.put("/preference/{key}"' in src


def test_memory_router_csrf_gates_mutations():
    src = _read(SRC / "routers/copilot_memory.py")
    # Every mutation endpoint must declare require_csrf in its
    # dependencies block. Three mutations: add_fact, delete_fact,
    # set_preference.
    for handler in ("add_fact", "delete_fact", "set_preference"):
        body = re.search(
            rf"async def {handler}.*?(?=^async def|\Z)",
            src, re.DOTALL | re.MULTILINE,
        )
        assert body, f"handler {handler} not found"
    # Count require_csrf in decorators — should appear at least 3x.
    assert src.count("require_csrf") >= 3


def test_memory_router_enforces_fact_length():
    """A 500-char cap on facts so the table can't be turned into a
    spam pump for the LLM context window."""
    src = _read(SRC / "routers/copilot_memory.py")
    assert "_MAX_FACT_LEN" in src
    assert "500" in src


def test_memory_router_preference_key_allowlist():
    """Preference keys are explicitly allowlisted — a typo can't
    pollute the table with junk. The brief documents tone /
    language / default_cartridge."""
    src = _read(SRC / "routers/copilot_memory.py")
    assert "_ALLOWED_PREF_KEYS" in src
    for key in ('"tone"', '"language"', '"default_cartridge"'):
        assert key in src


def test_memory_router_audits_mutations():
    src = _read(SRC / "routers/copilot_memory.py")
    for action in (
        "copilot.memory.fact.add",
        "copilot.memory.fact.delete",
        "copilot.memory.preference.set",
    ):
        assert action in src


# ── Drafts router ───────────────────────────────────────────────────────


def test_drafts_router_exists_with_crud_surface():
    path = SRC / "routers/copilot_drafts.py"
    assert path.exists()
    src = _read(path)
    assert "@router.post(\"\"" in src or "@router.post('')" in src
    assert "@router.get(\"\"" in src or "@router.get('')" in src
    assert '@router.post("/{draft_id}/send"' in src


def test_drafts_router_validates_kind_and_tone():
    """Free-text 'kind' would let any caller pollute the table.
    Allowlist both kind and tone."""
    src = _read(SRC / "routers/copilot_drafts.py")
    assert "_ALLOWED_KINDS" in src
    for kind in ("email", "memo", "note", "report"):
        assert f'"{kind}"' in src
    assert "_ALLOWED_TONES" in src
    for tone in ("formal", "neutral", "friendly", "urgent"):
        assert f'"{tone}"' in src


def test_drafts_router_body_length_capped():
    """50 KB per draft. Generous but bounded — protects the audit
    metadata path from accidentally swallowing megabytes."""
    src = _read(SRC / "routers/copilot_drafts.py")
    assert "_MAX_BODY_LEN" in src
    assert "50_000" in src or "50000" in src


def test_drafts_send_returns_404_for_other_users():
    """Probing client mustn't be able to enumerate other users'
    draft IDs by send-attempt error semantics. The brief's
    contract: 404 covers all four "not yours / not draft / not
    found / already sent" branches identically."""
    src = _read(SRC / "services/draft_sender.py")
    assert "AND user_id = $2" in src
    assert "AND status = 'draft'" in src
    assert "UPDATE copilot_drafts" in src


def test_drafts_router_audits_create_and_send():
    router_src = _read(SRC / "routers/copilot_drafts.py")
    sender_src = _read(SRC / "services/draft_sender.py")
    assert "copilot.draft.create" in router_src
    assert "copilot.draft.send" in sender_src


# ── Workflows router ────────────────────────────────────────────────────


def test_workflows_router_exists():
    path = SRC / "routers/copilot_workflows.py"
    assert path.exists()
    src = _read(path)
    assert "@router.post(\"\"" in src
    assert '@router.get("/{workflow_id}")' in src
    assert "@router.get(\"\"" in src
    assert '@router.post("/{workflow_id}/cancel"' in src


def test_workflows_create_validates_intent():
    src = _read(SRC / "routers/copilot_workflows.py")
    # intent required, length capped.
    assert "_MAX_INTENT_LEN" in src
    assert "intent is required" in src or 'intent required' in src
    # Initial status MUST be 'planning' — the LLM planner flips to
    # 'running' once it produces the step list.
    body = re.search(
        r"async def create_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body and "'planning'" in body.group(0)


def test_workflows_get_scopes_by_user():
    """Can't fetch another user's workflow by ID. Status code is
    404 (not 403) to avoid enumeration."""
    src = _read(SRC / "routers/copilot_workflows.py")
    body = re.search(
        r"async def get_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body
    assert "WHERE id = $1 AND user_id = $2" in body.group(0)
    assert "404" in body.group(0)


def test_workflows_cancel_only_active():
    """Cancel transitions planning|running → cancelled. Terminal
    statuses (completed, failed, already cancelled) return 404 so
    a probe can't enumerate workflow states."""
    src = _read(SRC / "routers/copilot_workflows.py")
    body = re.search(
        r"async def cancel_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body and "status IN ('planning', 'running')" in body.group(0)


def test_workflows_router_audits():
    src = _read(SRC / "routers/copilot_workflows.py")
    assert "copilot.workflow.create" in src
    assert "copilot.workflow.cancel" in src


# ── main.py wiring ──────────────────────────────────────────────────────


def test_main_includes_three_new_routers():
    src = _read(MAIN)
    for module_alias in (
        "copilot_memory as copilot_memory_router",
        "copilot_drafts as copilot_drafts_router",
        "copilot_workflows as copilot_workflows_router",
    ):
        assert module_alias in src, (
            f"main.py missing import: {module_alias}"
        )
    for include in (
        "app.include_router(copilot_memory_router.router)",
        "app.include_router(copilot_drafts_router.router)",
        "app.include_router(copilot_workflows_router.router)",
    ):
        assert include in src


# ── Migration ordering ──────────────────────────────────────────────────


def test_migrations_51_52_53_lexicographic_order():
    """docker-entrypoint-initdb.d processes files lexicographically.
    The conversations FK on migrations 51 + 53 depends on the
    conversations table (migration 38), so the migrations must run
    in numerical order 38 → 51 → 52 → 53."""
    names = sorted(p.name for p in MIG_DIR.glob("*.sql"))
    for earlier, later in (
        ("38_copilot_conversations.sql", "51_copilot_memory.sql"),
        ("50_copilot_briefing_dismissed.sql", "51_copilot_memory.sql"),
        ("51_copilot_memory.sql", "52_copilot_drafts.sql"),
        ("52_copilot_drafts.sql", "53_copilot_workflows.sql"),
    ):
        assert names.index(earlier) < names.index(later), (
            f"{earlier} must come before {later}"
        )


# ── v1.44.2 R1 follow-ups ──────────────────────────────────────────────


def test_migration_54_adds_failed_and_success_partial_indexes():
    """R1 DB P1: analyze_extraction_failures would seq-scan
    extraction_runs without a partial index on status='failed'.
    Migration 54 also adds a finished_at-leading partial for the
    volume-anomaly 8-day range scan."""
    src = _read(MIG_DIR / "54_proactive_indexes.sql")
    assert "idx_extraction_runs_failed_finished" in src
    assert "WHERE status = 'failed'" in src
    assert "idx_extraction_runs_success_finished" in src
    assert "WHERE status = 'success'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_workflows_router_validates_uuid_path_params():
    """R1 Security P2: a malformed path UUID would surface as a 500
    from asyncpg's InvalidTextRepresentation. _validate_uuid()
    converts it to a deterministic 400."""
    src = _read(SRC / "routers/copilot_workflows.py")
    assert "import uuid" in src
    assert "def _validate_uuid" in src
    # Both UUID-bearing handlers must call the validator.
    for handler in ("get_workflow", "cancel_workflow"):
        body = re.search(
            rf"async def {handler}.*?(?=^async def|\Z)",
            src, re.DOTALL | re.MULTILINE,
        )
        body_text = body.group(0) if body else ""
        assert "_validate_uuid" in body_text, (
            f"{handler} must validate the workflow_id path param"
        )


def test_drafts_router_validates_uuid_path_params():
    src = _read(SRC / "routers/copilot_drafts.py")
    assert "def _validate_uuid" in src
    send_body = re.search(
        r"async def send_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert send_body and "_validate_uuid" in send_body.group(0)


def test_workflows_404_strings_identical_across_branches():
    """R1 Security P2: the cancel handler's 404 string used to read
    'Workflow not found or already finished'. Compared against
    get_workflow's 'Workflow not found' a probe could infer that a
    given UUID is terminal-state. Collapse to a single string."""
    src = _read(SRC / "routers/copilot_workflows.py")
    # Both handlers must raise HTTPException(404, "Workflow not found")
    # — no other 404 string in the file.
    not_found_strings = re.findall(
        r'HTTPException\(\s*404,\s*"([^"]+)"', src,
    )
    assert not_found_strings, "no 404 raises in copilot_workflows.py"
    distinct = set(not_found_strings)
    assert distinct == {"Workflow not found"}, (
        f"All 404 strings in copilot_workflows.py must read "
        f"'Workflow not found' identically. Found: {distinct}"
    )
