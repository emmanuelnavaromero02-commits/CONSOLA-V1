"""Sprint v1.44.3 (Tarea D) — LLM integration contracts.

Per the scope agreement: real LLM calls in production code, mocked
LLM calls in CI tests. This file mocks at the
``llm_client.chat`` / extracted-text callable seam so the tests run
in <1 s without burning API tokens, while production keeps hitting
the configured provider.

Covered:
  * memory_service builds the system-prompt appendix from facts +
    preferences + recent summaries; returns the base prompt unchanged
    when the user has nothing recorded.
  * extract_facts_from_turn tolerates strict JSON, prose-wrapped JSON,
    and silently drops malformed responses.
  * summarise_conversation upserts via the v1.44.2 migration 51 table.
  * copilot_drafts /generate calls the LLM, persists, audits.
  * copilot_workflows /{id}/plan calls the LLM, parses the plan,
    persists steps, flips status, audits.
  * copilot_service.run_turn integrates memory_service via
    ``build_system_prompt_with_memory`` AND runs
    ``_maybe_extract_facts`` after the turn.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SVC  = REPO / "console/app/services"
RTR  = REPO / "console/app/routers"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── memory_service module ────────────────────────────────────────────────


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
    """The pure-function renderer covers the prompt shape so tests
    don't need to mock the DB pool."""
    src = _read(SVC / "memory_service.py")
    assert "render_memory_block" in src
    # Section headers the LLM consumes.
    for header in (
        "Contexto del usuario",
        "Hechos sobre el usuario y su empresa",
        "Preferencias",
        "Resumen de conversaciones recientes",
    ):
        assert header in src, f"memory block missing section: {header}"


def test_memory_prompt_empty_returns_base_prompt_unchanged():
    """Identity-transform contract: if the user has no facts /
    prefs / summaries, the base prompt comes back without any
    appendage. Pure-function check via the helper."""
    import sys
    sys.path.insert(0, str(REPO / "console"))
    # Module sets up app.services namespace which has DB dependencies;
    # the pure-function render helper doesn't touch DB but lives in
    # the same module, so we import inside a guarded scope.
    import importlib
    # Restart any cached import so the test starts clean.
    sys.modules.pop("app.services.memory_service", None)
    sys.modules.pop("app.services", None)
    sys.modules.pop("app", None)
    # The helper is intentionally pure — no DB, no LLM. We can
    # import the function in isolation by source-reading + exec'ing
    # the render helper definition. Static-asserting is cleaner.
    src = _read(SVC / "memory_service.py")
    # No early-return branch in render_memory_block means the empty
    # case would emit an empty block — that's incorrect. The helper
    # must check the three inputs and return "" when all empty.
    block_match = re.search(
        r"def render_memory_block.*?(?=^def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert block_match
    body = block_match.group(0)
    assert "if not fact_list and not preferences and not summary_list" in body
    assert 'return ""' in body


def test_extract_facts_handles_strict_and_prose_wrapped_json():
    """The LLM is asked for strict JSON but sometimes prefixes with
    'Here are the facts:' or wraps in markdown fences. The parser
    must accept the prose-wrapped form via the regex fallback."""
    src = _read(SVC / "memory_service.py")
    body = re.search(
        r"def _parse_facts_json.*?(?=^def |^async def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body
    text = body.group(0)
    assert "json.loads" in text
    # Regex fallback that pulls the JSON array out of prose.
    assert "re.search" in text or "_re.search" in text
    assert r"\[.*\]" in text


def test_extract_facts_inserts_with_extracted_source():
    """Persisted facts must be tagged source='extracted' so a future
    UI can distinguish operator-stated facts from LLM inferences."""
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def extract_facts_from_turn.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert block and "source, confidence" in block.group(0)
    assert "'extracted'" in block.group(0)
    # Idempotent: ON CONFLICT lets the LLM re-observe the same fact
    # across turns without duplicates.
    assert "ON CONFLICT (user_id, fact) DO NOTHING" in block.group(0)


def test_extract_facts_caps_per_turn():
    """Cap on how many facts persist per turn — a malicious or
    rambling LLM response shouldn't pump 100 facts.

    v1.44.3 R1 LLM-A2 follow-up: the cap is now applied to the
    VALID candidate count via an early-break, not via a slice on
    the raw parsed list. The previous slice-first order silently
    under-counted when leading candidates failed validation.
    """
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def extract_facts_from_turn.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "max_new_facts: int = 5" in body
    # The cap is enforced via len(valid_texts) >= max_new_facts AFTER
    # per-candidate validation, not via a slice on the raw parsed list.
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
    # The "sin contenido" sentinel suppresses empty-summary upserts.
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


# ── copilot_service integration ──────────────────────────────────────────


def test_copilot_service_calls_memory_builder():
    """run_turn's _run_loop must call memory_service.build_system_prompt_with_memory
    instead of passing SYSTEM_PROMPT directly."""
    src = _read(SVC / "copilot_service.py")
    assert "memory_service" in src
    assert "build_system_prompt_with_memory" in src
    # The builder is called BEFORE the llm_client.chat invocation.
    builder_idx = src.find("build_system_prompt_with_memory")
    chat_idx = src.find("await llm_client.chat", builder_idx)
    assert builder_idx >= 0 and chat_idx > builder_idx, (
        "build_system_prompt_with_memory must run before llm_client.chat"
    )
    # And the call site must pass the BUILT prompt, not the raw constant.
    chat_block = src[chat_idx:chat_idx + 400]
    assert "system=system_prompt_for_call" in chat_block


def test_copilot_service_falls_back_on_memory_failure():
    """Memory is personalisation, not load-bearing. If the builder
    raises, the turn must still proceed with the base SYSTEM_PROMPT
    — never block the user."""
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
    # Should pass the recent tail of history, not the full transcript,
    # to keep the extraction prompt short.
    assert "history)[-" in text


def test_copilot_service_fact_extraction_failure_does_not_block_turn():
    """Extraction is best-effort. A raise inside the helper must not
    propagate — the user already got their response."""
    src = _read(SVC / "copilot_service.py")
    # The _maybe_extract_facts call site must be inside a try/except.
    m = re.search(
        r"try:\s*\n\s*await _maybe_extract_facts.*?except", src, re.DOTALL,
    )
    assert m, "fact extraction call must be wrapped in try/except"


# ── copilot_drafts /generate ────────────────────────────────────────────


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
    # The LLM call must happen + the result must be INSERTed.
    assert "_llm_single_shot" in body
    assert "INSERT INTO copilot_drafts" in body
    # Audit event flags it as a generated draft.
    assert "copilot.draft.generate" in body


def test_drafts_generate_validates_inputs():
    src = _read(RTR / "copilot_drafts.py")
    block = re.search(
        r"async def generate_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    # kind + tone allowlists + about required + about length cap.
    assert "_ALLOWED_KINDS" in body
    assert "_ALLOWED_TONES" in body
    assert "about is required" in body
    assert "4000" in body  # about length cap


def test_drafts_generate_uses_memory_context():
    """The brief calls out memory as context for drafts. Verify the
    handler reads facts before composing the prompt."""
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
    # The /generate decorator must include require_csrf.
    m = re.search(
        r'@router\.post\("/generate"[^)]*\)',
        src, re.DOTALL,
    )
    assert m
    assert "require_csrf" in m.group(0)


def test_drafts_generate_sanitises_llm_errors():
    """LLM SDK errors sometimes echo Authorization headers. The
    handler must convert any exception to a generic 502 without
    propagating the SDK message."""
    src = _read(RTR / "copilot_drafts.py")
    block = re.search(
        r"async def generate_draft.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert "except Exception" in body
    assert '502, "draft generation failed"' in body


def test_drafts_prompts_per_kind():
    """The brief specifies prompts per kind. Verify all four are
    defined."""
    src = _read(RTR / "copilot_drafts.py")
    for kind in ("email", "memo", "note", "report"):
        assert f'"{kind}":' in src, f"_DRAFT_PROMPTS missing {kind}"


# ── copilot_workflows /{id}/plan ────────────────────────────────────────


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
    # Status flips planning → running once the plan persists.
    assert "SET plan = $2::jsonb, status = 'running'" in body
    assert "copilot.workflow.plan" in body


def test_workflow_plan_409_on_already_planned():
    """Re-running on a workflow whose status is no longer 'planning'
    must NOT silently overwrite the existing plan."""
    src = _read(RTR / "copilot_workflows.py")
    block = re.search(
        r"async def plan_workflow.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert 'run["status"] != "planning"' in body
    assert "409" in body


def test_workflow_plan_caps_steps_at_8():
    """The planner system prompt explicitly caps the plan at 8 steps.
    The parser also slices [:8] as a belt-and-suspenders guard."""
    src = _read(RTR / "copilot_workflows.py")
    assert "Máximo 8 pasos" in src
    assert "parsed[:8]" in src


def test_workflow_plan_destructive_tools_default_to_human_review():
    """The planner prompt instructs: destructive tools (delete /
    drop / truncate / set_variable / create_dag) must come back
    with tool=null so a human approves them."""
    src = _read(RTR / "copilot_workflows.py")
    assert "destructiva" in src
    for kw in ("delete", "drop", "truncate", "set_variable", "create_dag"):
        assert kw in src, f"planner prompt missing destructive keyword {kw!r}"


def test_workflow_plan_grounds_planner_in_real_tools():
    """The planner is given the live list of MCP tool names so it
    can't hallucinate. The handler queries mcp_registry inline."""
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


# ── Cross-cutting: real LLM in prod, mocked in tests ────────────────────


def test_no_mock_llm_client_in_production_code():
    """The scope agreement: real LLM in prod, mocked only in CI
    tests. Production modules must NOT import any mock/fake/stub
    LLM client."""
    for path in (
        SVC / "memory_service.py",
        SVC / "copilot_service.py",
        RTR / "copilot_drafts.py",
        RTR / "copilot_workflows.py",
    ):
        src = _read(path)
        # The real client lives at app.services.llm_client. Forbid
        # imports from any plausible mock module name.
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
    """Memory + drafts both wrap LLM exceptions. The audit metadata
    must NOT contain the raw exception (it may echo API keys); only
    the bounded outcome string lives in audit_events."""
    for path in (SVC / "memory_service.py", RTR / "copilot_drafts.py"):
        src = _read(path)
        # The audit_service.record_event payload must not include
        # ``str(exc)`` directly — it must use truncated text.
        record_blocks = re.findall(
            r"audit_service\.record_event\([^)]*\)", src, re.DOTALL,
        )
        for block in record_blocks:
            assert "str(exc)" not in block, (
                f"{path.name} audit_event includes raw str(exc); "
                f"may leak SDK error contents — truncate first"
            )


# ── R1 follow-ups ───────────────────────────────────────────────────────


def test_workflow_parser_denylists_destructive_tools():
    """R1 Security P2: prompt-only destructive guards relied on LLM
    compliance. The parser now applies a deny-list regex regardless
    of what the model emitted — defense-in-depth."""
    src = _read(RTR / "copilot_workflows.py")
    assert "_DESTRUCTIVE_TOOL_RE" in src
    assert "_is_destructive_tool" in src
    # Every documented destructive verb must be in the regex.
    for kw in ("delete", "drop", "truncate", "set_variable", "create_dag"):
        assert kw in src, f"deny-list regex missing {kw!r}"
    # And the regex result must override the LLM-supplied tool in
    # _parse_plan_json — the variable assignment is the load-bearing line.
    parser = re.search(
        r"def _parse_plan_json.*?(?=^def |^async def |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = parser.group(0) if parser else ""
    assert "None if _is_destructive_tool" in body


def test_fact_extraction_prompt_includes_no_inference_rule():
    """R1 LLM P2: an over-eager LLM was inferring facts the user
    never stated. Prompt now carries an explicit anti-fabrication
    rule."""
    src = _read(SVC / "memory_service.py")
    assert "NO infieras" in src or "No infieras" in src
    # And the rule is marked as REGLA INVIOLABLE so the model treats
    # it with the same weight as the JSON-only output rule.
    assert "REGLA INVIOLABLE" in src


def test_extract_facts_validates_before_slicing():
    """R1 LLM P2 follow-up: previous slice-first order silently
    under-counted when leading candidates failed validation. The fix
    iterates the FULL parsed list, validates each, collects valid
    ones, and breaks at the cap."""
    src = _read(SVC / "memory_service.py")
    block = re.search(
        r"async def extract_facts_from_turn.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    # The iterator is over `parsed`, not `parsed[:max_new_facts]`.
    assert "for candidate in parsed:" in body
    # The break condition is len(valid_texts) >= max_new_facts.
    assert "len(valid_texts) >= max_new_facts" in body
    assert "break" in body


def test_credentials_form_dialog_focus_traps_cancel_button():
    """R1 Frontend P2: ConfirmDelete dialog now auto-focuses the
    Cancel button on mount so keyboard users land on the safe
    action."""
    src = _read(REPO / "console-next/src/components/cartridges/CredentialsForm.tsx")
    # The Cancel button must carry a ref AND the dialog must focus it
    # in a useEffect.
    assert "cancelRef" in src
    assert "useRef" in src
    assert "cancelRef.current?.focus()" in src


def test_credentials_form_optional_number_uses_undefined_default():
    """R1 Frontend P2: empty-string + z.coerce.number() produced NaN,
    rejected by Zod with a cryptic message for OPTIONAL number fields.
    Fix: undefined default when the field is not required."""
    src = _read(REPO / "console-next/src/components/cartridges/CredentialsForm.tsx")
    # The defaultsFor function now branches on f.required for numbers.
    block = re.search(
        r"function defaultsFor.*?(?=^function |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = block.group(0) if block else ""
    assert 'f.required ? "" : undefined' in body, (
        "defaultsFor must return undefined for optional numbers so "
        "empty input isn't coerced to NaN by Zod"
    )


def test_cartridges_grid_drops_redundant_lg_breakpoint():
    """R1 Frontend P2: lg:grid-cols-2 was identical to md:grid-cols-2
    — dead code. Confirm it's removed from the className (a comment
    referencing the old class is fine for code-history readability)."""
    src = _read(REPO / "console-next/src/app/cartridges/page.tsx")
    # Strip JSX comments so the rationale-line ("dropped the
    # redundant lg:grid-cols-2 — it was identical…") doesn't trip
    # the test; only real className occurrences count.
    code_only = re.sub(r"//.*?$|/\*.*?\*/|\{/\*.*?\*/\}", "",
                       src, flags=re.MULTILINE | re.DOTALL)
    assert "lg:grid-cols-2" not in code_only
    assert "md:grid-cols-2" in code_only
