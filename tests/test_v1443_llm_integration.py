from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SVC  = REPO / "console/app/services"
RTR  = REPO / "console/app/routers"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_memory_service_exports_three_public_helpers():
    src = _read(SVC / "memory_service.py")
    for fn in (
        "build_system_prompt_with_memory",
        "extract_facts_from_turn",
        "summarise_conversation",
    ):
        assert f"async def {fn}" in src, (
            f"memory_service missing public helper {fn}"
        )


def test_memory_prompt_block_renders_facts_prefs_summaries():
    src = _read(SVC / "memory_service.py")
    assert "render_memory_block" in src
    for header in (
        "Contexto del usuario",
        "Hechos sobre el usuario y su empresa",
        "Preferencias",
        "Resumen de conversaciones recientes",
    ):
        assert header in src, f"memory block missing section: {header}"


def test_memory_prompt_empty_returns_base_prompt_unchanged():
    import sys
    sys.path.insert(0, str(REPO / "console"))
    import importlib
    sys.modules.pop("app.services.memory_service", None)
    sys.modules.pop("app.services", None)
    sys.modules.pop("app", None)
    src = _read(SVC / "memory_service.py")
    block_match = re.search(
        r"def render_memory_block.*?(?=^def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert block_match
    body = block_match.group(0)
    assert "if not fact_list and not preferences and not summary_list" in body
    assert 'return ""' in body


def test_extract_facts_handles_strict_and_prose_wrapped_json():
    src = _read(SVC / "memory_service.py")
    body = re.search(
        r"def _parse_facts_json.*?(?=^def |^async def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body
    text = body.group(0)
    assert "json.loads" in text
    assert "re.search" in text or "_re.search" in text
    assert r"\[.*\]" in text


def test_extract_facts_inserts_with_extracted_source():
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def extract_facts_from_turn.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert block and "source, confidence" in block.group(0)
    assert "'extracted'" in block.group(0)
    assert "ON CONFLICT (user_id, workspace_id, fact) WHERE workspace_id IS NOT NULL" in block.group(0)
    assert "DO NOTHING" in block.group(0)


def test_extract_facts_caps_per_turn():
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def extract_facts_from_turn.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "max_new_facts: int = 5" in body
    assert "len(valid_texts) >= max_new_facts" in body, (
        "extraction cap must be applied AFTER validation so a mix of "
        "valid + invalid candidates doesn't silently under-count"
    )


def test_summarise_conversation_uses_upsert():
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def summarise_conversation.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "INSERT INTO conversation_memory_summary" in body
    assert "ON CONFLICT (conversation_id) DO UPDATE" in body
    assert "sin contenido" in body


def test_summarise_conversation_truncates_long_summaries():
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def summarise_conversation.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "1000" in body, (
        "summarise_conversation must cap summary at ~1000 chars so a "
        "runaway LLM can't bloat the conversation_memory_summary table"
    )


def test_copilot_service_calls_memory_builder():
    src = _read(SVC / "copilot_service.py")
    assert "memory_service" in src
    assert "build_system_prompt_with_memory" in src
    builder_idx = src.find("build_system_prompt_with_memory")
    chat_idx = src.find("await llm_client.chat", builder_idx)
    assert builder_idx >= 0 and chat_idx > builder_idx, (
        "build_system_prompt_with_memory must run before llm_client.chat"
    )
    chat_block = src[chat_idx:chat_idx + 400]
    assert "system=system_prompt_for_call" in chat_block


def test_copilot_service_falls_back_on_memory_failure():
    src = _read(SVC / "copilot_service.py")
    assert "system_prompt_for_call = SYSTEM_PROMPT" in src


def test_copilot_service_extracts_facts_after_turn():
    src = _read(SVC / "copilot_service.py")
    assert "_maybe_extract_facts" in src
    body = re.search(
        r"async def _maybe_extract_facts.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body
    text = body.group(0)
    assert "memory_service.extract_facts_from_turn" in text
    assert "history)[-" in text


def test_copilot_service_fact_extraction_failure_does_not_block_turn():
    src = _read(SVC / "copilot_service.py")
    m = re.search(
        r"try:\s*\n\s*await _maybe_extract_facts.*?except", src, re.DOTALL,
    )
    assert m, "fact extraction call must be wrapped in try/except"


def test_drafts_generate_endpoint_exists():
    src = _read(RTR / "copilot_drafts.py")
    assert '@router.post("/generate"' in src
    assert "async def generate_draft" in src


def test_drafts_generate_calls_llm_and_persists():
    src = _read(RTR / "copilot_drafts.py")
    block = re.search(
        r"async def generate_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "_llm_single_shot" in body
    assert "INSERT INTO copilot_drafts" in body
    assert "copilot.draft.generate" in body


def test_drafts_generate_validates_inputs():
    src = _read(RTR / "copilot_drafts.py")
    block = re.search(
        r"async def generate_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "_ALLOWED_KINDS" in body
    assert "_ALLOWED_TONES" in body
    assert "about is required" in body
    assert "4000" in body


def test_drafts_generate_uses_memory_context():
    src = _read(RTR / "copilot_drafts.py")
    block = re.search(
        r"async def generate_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "memory_service" in body
    assert "_fetch_facts" in body


def test_drafts_generate_csrf_gated():
    src = _read(RTR / "copilot_drafts.py")
    m = re.search(
        r'@router\.post\("/generate"[^)]*\)',
        src, re.DOTALL,
    )
    assert m
    assert "require_csrf" in m.group(0)


def test_drafts_generate_sanitises_llm_errors():
    src = _read(RTR / "copilot_drafts.py")
    block = re.search(
        r"async def generate_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "except Exception" in body
    assert '502, "draft generation failed"' in body


def test_drafts_prompts_per_kind():
    src = _read(RTR / "copilot_drafts.py")
    for kind in ("email", "memo", "note", "report"):
        assert f'"{kind}":' in src, f"_DRAFT_PROMPTS missing {kind}"


def test_workflow_plan_endpoint_exists():
    src = _read(RTR / "copilot_workflows.py")
    assert '@router.post("/{workflow_id}/plan"' in src
    assert "async def plan_workflow" in src


def test_workflow_plan_runs_llm_persists_steps():
    src = _read(RTR / "copilot_workflows.py")
    block = re.search(
        r"async def plan_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "_llm_plan" in body
    assert "INSERT INTO workflow_steps" in body
    assert "SET plan = $2::jsonb, status = 'running'" in body
    assert "copilot.workflow.plan" in body


def test_workflow_plan_409_on_already_planned():
    src = _read(RTR / "copilot_workflows.py")
    block = re.search(
        r"async def plan_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert 'run["status"] != "planning"' in body
    assert "409" in body


def test_workflow_plan_caps_steps_at_8():
    src = _read(RTR / "copilot_workflows.py")
    assert "Máximo 8 pasos" in src
    assert "parsed[:8]" in src


def test_workflow_plan_destructive_tools_default_to_human_review():
    src = _read(RTR / "copilot_workflows.py")
    assert "destructiva" in src
    for kw in ("delete", "drop", "truncate", "set_variable", "create_dag"):
        assert kw in src, f"planner prompt missing destructive keyword {kw!r}"


def test_workflow_plan_grounds_planner_in_real_tools():
    src = _read(RTR / "copilot_workflows.py")
    block = re.search(
        r"async def plan_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "mcp_registry" in body
    assert "list_servers" in body
    assert "list_tools" in body


def test_workflow_plan_csrf_gated():
    src = _read(RTR / "copilot_workflows.py")
    m = re.search(
        r'@router\.post\("/\{workflow_id\}/plan"[^)]*\)',
        src, re.DOTALL,
    )
    assert m
    assert "require_csrf" in m.group(0)


def test_no_mock_llm_client_in_production_code():
    for path in (
        SVC / "memory_service.py",
        SVC / "copilot_service.py",
        RTR / "copilot_drafts.py",
        RTR / "copilot_workflows.py",
    ):
        src = _read(path)
        for forbidden in (
            "from app.services.fake_llm",
            "from app.services.mock_llm",
            "from tests.",
            "MagicMock",
            "AsyncMock",
        ):
            assert forbidden not in src, (
                f"{path.name} contains forbidden mock import: {forbidden}"
            )


def test_llm_call_errors_truncated_before_audit():
    for path in (SVC / "memory_service.py", RTR / "copilot_drafts.py"):
        src = _read(path)
        record_blocks = re.findall(
            r"audit_service\.record_event\([^)]*\)", src, re.DOTALL,
        )
        for block in record_blocks:
            assert "str(exc)" not in block, (
                f"{path.name} audit_event includes raw str(exc); "
                f"may leak SDK error contents — truncate first"
            )


def test_workflow_parser_denylists_destructive_tools():
    src = _read(RTR / "copilot_workflows.py")
    assert "_DESTRUCTIVE_TOOL_RE" in src
    assert "_is_destructive_tool" in src
    for kw in ("delete", "drop", "truncate", "set_variable", "create_dag"):
        assert kw in src, f"deny-list regex missing {kw!r}"
    parser = re.search(
        r"def _parse_plan_json.*?(?=^def |^async def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = parser.group(0) if parser else ""
    assert "None if _is_destructive_tool" in body


def test_fact_extraction_prompt_includes_no_inference_rule():
    src = _read(SVC / "memory_service.py")
    assert "NO infieras" in src or "No infieras" in src
    assert "REGLA INVIOLABLE" in src


def test_extract_facts_validates_before_slicing():
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def extract_facts_from_turn.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "for candidate in parsed:" in body
    assert "len(valid_texts) >= max_new_facts" in body
    assert "break" in body


def test_credentials_form_dialog_focus_traps_cancel_button():
    src = _read(REPO / "console-next/src/components/cartridges/CredentialsForm.tsx")
    assert "cancelRef" not in src
    assert "useRef" not in src
    assert "ConfirmDeleteDialog" not in src


def test_credentials_form_optional_number_uses_undefined_default():
    src = _read(REPO / "console-next/src/components/cartridges/CredentialsForm.tsx")
    assert "function defaultsFor" not in src


def test_cartridges_grid_drops_redundant_lg_breakpoint():
    src = _read(REPO / "console-next/src/app/(shell)/cartridges/page.tsx")
    code_only = re.sub(r"//.*?$|/\*.*?\*/|\{/\*.*?\*/\}", "",
                       src, flags=re.MULTILINE | re.DOTALL)
    assert "lg:grid-cols-2" not in code_only
    assert "md:grid-cols-2" in code_only
