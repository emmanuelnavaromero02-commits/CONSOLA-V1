from __future__ import annotations

import re
from pathlib import Path


REPO       = Path(__file__).resolve().parents[1]
SRC        = REPO / "console/app"
MIG_DIR    = REPO / "infra/init"
MAIN       = SRC / "main.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    assert "UNIQUE (user_id, fact)" in src


def test_memory_summary_fk_guarded_by_existence_check():
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    assert "information_schema.tables" in src
    assert "table_name = 'conversations'" in src
    assert "ADD CONSTRAINT conversation_memory_summary_conversation_id_fkey" in src


def test_migration_51_self_registers():
    src = _read(MIG_DIR / "51_copilot_memory.sql")
    assert "'51_copilot_memory.sql'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_migration_52_exists():
    assert (MIG_DIR / "52_copilot_drafts.sql").exists()


def test_migration_52_drafts_has_status_lifecycle_constraints():
    src = _read(MIG_DIR / "52_copilot_drafts.sql")
    assert "status       TEXT         NOT NULL DEFAULT 'draft'" in src
    assert "idx_copilot_drafts_user_updated" in src
    assert "idx_copilot_drafts_status" in src
    assert "WHERE status != 'discarded'" in src


def test_migration_52_drafts_cascade_on_user_delete():
    src = _read(MIG_DIR / "52_copilot_drafts.sql")
    assert "REFERENCES users(id) ON DELETE CASCADE" in src


def test_migration_52_self_registers():
    src = _read(MIG_DIR / "52_copilot_drafts.sql")
    assert "'52_copilot_drafts.sql'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_migration_53_exists():
    assert (MIG_DIR / "53_copilot_workflows.sql").exists()


def test_migration_53_creates_runs_and_steps():
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert "CREATE TABLE IF NOT EXISTS workflow_runs" in src
    assert "CREATE TABLE IF NOT EXISTS workflow_steps" in src


def test_migration_53_step_unique_per_workflow():
    src = _read(MIG_DIR / "53_copilot_workflows.sql")
    assert "UNIQUE (workflow_id, step_idx)" in src


def test_migration_53_partial_status_index():
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


def test_memory_router_exists_and_includes_three_resources():
    path = SRC / "routers/copilot_memory.py"
    assert path.exists()
    src = _read(path)
    assert '@router.post("/fact"' in src
    assert "@router.get(\"\")" in src or "@router.get('')" in src
    assert '@router.delete("/fact/{fact_id}"' in src
    assert '@router.put("/preference/{key}"' in src


def test_memory_router_csrf_gates_mutations():
    src = _read(SRC / "routers/copilot_memory.py")
    for handler in ("add_fact", "delete_fact", "set_preference"):
        body = re.search(
            rf"async def {handler}.*?(?=^async def|\Z)",
            src, re.DOTALL | re.MULTILINE,
        )
        assert body, f"handler {handler} not found"
    assert src.count("require_csrf") >= 3


def test_memory_router_enforces_fact_length():
    src = _read(SRC / "routers/copilot_memory.py")
    assert "_MAX_FACT_LEN" in src
    assert "500" in src


def test_memory_router_preference_key_allowlist():
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


def test_drafts_router_exists_with_crud_surface():
    path = SRC / "routers/copilot_drafts.py"
    assert path.exists()
    src = _read(path)
    assert "@router.post(\"\"" in src or "@router.post('')" in src
    assert "@router.get(\"\"" in src or "@router.get('')" in src
    assert '"/{draft_id}/send"' in src
    assert "async def send_draft" in src


def test_drafts_router_validates_kind_and_tone():
    src = _read(SRC / "routers/copilot_drafts.py")
    assert "_ALLOWED_KINDS" in src
    for kind in ("email", "memo", "note", "report"):
        assert f'"{kind}"' in src
    assert "_ALLOWED_TONES" in src
    for tone in ("formal", "neutral", "friendly", "urgent"):
        assert f'"{tone}"' in src


def test_drafts_router_body_length_capped():
    src = _read(SRC / "routers/copilot_drafts.py")
    assert "_MAX_BODY_LEN" in src
    assert "50_000" in src or "50000" in src


def test_drafts_send_returns_404_for_other_users():
    src = _read(SRC / "services/draft_sender.py")
    assert "AND user_id = $2" in src
    assert "AND status = 'draft'" in src
    assert "UPDATE copilot_drafts" in src


def test_drafts_router_audits_create_and_send():
    router_src = _read(SRC / "routers/copilot_drafts.py")
    sender_src = _read(SRC / "services/draft_sender.py")
    assert "copilot.draft.create" in router_src
    assert "copilot.draft.send" in sender_src


def test_workflows_router_exists():
    path = SRC / "routers/copilot_workflows.py"
    assert path.exists()
    src = _read(path)
    assert "@router.post(\"\"" in src
    assert '@router.get("/{workflow_id}")' in src
    assert "@router.get(\"\"" in src
    assert '"/{workflow_id}/cancel"' in src
    assert "async def cancel_workflow" in src


def test_workflows_create_validates_intent():
    src = _read(SRC / "routers/copilot_workflows.py")
    assert "_MAX_INTENT_LEN" in src
    assert "intent is required" in src or 'intent required' in src
    body = re.search(
        r"async def create_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body and "'planning'" in body.group(0)


def test_workflows_get_scopes_by_user():
    src = _read(SRC / "routers/copilot_workflows.py")
    body = re.search(
        r"async def get_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body
    assert "WHERE id = $1" in body.group(0)
    assert "AND user_id = $2" in body.group(0)
    assert "AND scope_status = 'scoped'" in body.group(0)
    assert "AND workspace_id = $3::uuid" in body.group(0)
    assert "404" in body.group(0)


def test_workflows_cancel_only_active():
    src = _read(SRC / "services/workflow_executor.py")
    assert "status IN ('planning', 'running', 'waiting_approval')" in src
    assert "UPDATE workflow_runs" in src
    assert "SET status = 'cancelled'" in src


def test_workflows_router_audits():
    router_src = _read(SRC / "routers/copilot_workflows.py")
    executor_src = _read(SRC / "services/workflow_executor.py")
    assert "copilot.workflow.create" in router_src
    assert "copilot.workflow.cancel" in executor_src


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


def test_migrations_51_52_53_lexicographic_order():
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


def test_migration_54_adds_failed_and_success_partial_indexes():
    src = _read(MIG_DIR / "54_proactive_indexes.sql")
    assert "idx_extraction_runs_failed_finished" in src
    assert "WHERE status = 'failed'" in src
    assert "idx_extraction_runs_success_finished" in src
    assert "WHERE status = 'success'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_workflows_router_validates_uuid_path_params():
    src = _read(SRC / "routers/copilot_workflows.py")
    assert "import uuid" in src
    assert "def _validate_uuid" in src
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
    decorator_body = src[src.rfind("@router.post", 0, send_body.start()):send_body.start()]
    assert "copilot.write" in decorator_body


def test_workflows_404_strings_identical_across_branches():
    src = _read(SRC / "routers/copilot_workflows.py")
    not_found_strings = re.findall(
        r'HTTPException\(\s*404,\s*"([^"]+)"', src,
    )
    assert not_found_strings, "no 404 raises in copilot_workflows.py"
    distinct = set(not_found_strings)
    assert distinct == {"Workflow not found"}, (
        f"All 404 strings in copilot_workflows.py must read "
        f"'Workflow not found' identically. Found: {distinct}"
    )
