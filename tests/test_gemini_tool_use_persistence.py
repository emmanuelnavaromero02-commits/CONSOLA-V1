"""Sprint v1.43.1 — Codex P0-2: Gemini chat preserves tool_use blocks
so the copilot's approval gate + persistence + history replay work
identically with both providers.

Strategy: stub the google-genai client. We don't need a real network
call — we just need to verify that _gemini_chat emits the
Anthropic-shape blocks copilot_service consumes (``role/content`` with
``tool_use`` and ``tool_result`` dicts), with stable ``tool_use_id``
correlation between the assistant and user turns.
"""
from __future__ import annotations

import asyncio
import sys
import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


def _load_llm_module():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import llm_client as mod
    return mod


@pytest.fixture
def llm_module():
    """Fresh import per test. Also installs the gtypes stub here so
    every call site inside _gemini_chat hits no-ops — there's no point
    deferring it to each test, the SDK validators fire from any path
    that touches gtypes.Content."""
    mod = _load_llm_module()

    fake_gtypes = types.SimpleNamespace()
    fake_gtypes.Content = lambda **kw: object()
    fake_gtypes.Part    = types.SimpleNamespace(
        from_text=lambda text=None: object(),
        from_function_response=lambda name=None, response=None: object(),
    )
    fake_gtypes.FunctionDeclaration   = lambda **kw: object()
    fake_gtypes.Tool                   = lambda **kw: object()
    fake_gtypes.GenerateContentConfig  = lambda **kw: object()
    # _to_genai_schema (line 328) reads gtypes.Type.STRING etc. to
    # translate the JSON-schema "type" field into the SDK enum.
    fake_gtypes.Type = types.SimpleNamespace(
        STRING="STRING", NUMBER="NUMBER", INTEGER="INTEGER",
        BOOLEAN="BOOLEAN", ARRAY="ARRAY", OBJECT="OBJECT",
    )
    fake_gtypes.Schema = lambda **kw: object()
    mod.gtypes = fake_gtypes
    mod.GEMINI_CACHE_ENABLED = False
    return mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fake_response_text_only(text: str, llm_module):
    """Build a fake Gemini response with a single text part."""
    part = MagicMock()
    part.text = text
    part.function_call = None
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    candidate.finish_reason = None
    resp = MagicMock()
    resp.candidates = [candidate]
    resp.text = text
    resp.usage_metadata = None
    return resp


def _fake_response_tool_call(name: str, args: dict, llm_module):
    """Build a fake Gemini response with one function_call part."""
    part = MagicMock()
    part.text = None
    fc = MagicMock()
    fc.name = name
    fc.args = args
    part.function_call = fc
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    candidate.finish_reason = None
    resp = MagicMock()
    resp.candidates = [candidate]
    resp.usage_metadata = None
    return resp


def test_gemini_returns_text_block_when_no_tool_called(llm_module, monkeypatch):
    """Without function calls, the assistant turn must come back as
    a single ``{type: text}`` block — not a bare string."""
    resp = _fake_response_text_only("Hola, ¿cómo estás?", llm_module)
    async def fake(**kw): return resp
    monkeypatch.setattr(llm_module, "_gemini_generate_with_retry", fake)
    # Skip the cache code path so we exercise the inline gen_config branch.
    monkeypatch.setattr(llm_module, "GEMINI_CACHE_ENABLED", False)
    # Skip token_store side-effect.
    monkeypatch.setattr(llm_module.token_store, "record", AsyncMock())

    text, viewer_urls, msgs = _run(llm_module._gemini_chat(
        system="sys", messages=[{"role": "user", "content": "hola"}],
        tools=[], invoke_tool=AsyncMock(), tool_server_map={}, on_event=None,
    ))
    assert text == "Hola, ¿cómo estás?"
    assert viewer_urls == []
    # The new chunk (msgs[1:]) is the assistant turn with a text block.
    new_chunk = msgs[1:]
    assert len(new_chunk) == 1
    assert new_chunk[0]["role"] == "assistant"
    assert new_chunk[0]["content"] == [
        {"type": "text", "text": "Hola, ¿cómo estás?"},
    ]


def test_gemini_emits_anthropic_shape_tool_use_blocks(llm_module, monkeypatch):
    """A function_call must come back as a ``tool_use`` block with a
    stable id, and the matching tool_result must reference the SAME
    id so copilot's history-replay reconstructs valid Anthropic
    structure."""
    # Two responses: first triggers a tool, second answers with text.
    responses = [
        _fake_response_tool_call("srv__do_thing", {"x": 1}, llm_module),
        _fake_response_text_only("Listo.", llm_module),
    ]
    calls = iter(responses)
    async def fake_generate(**kw): return next(calls)
    llm_module._gemini_generate_with_retry = fake_generate
    llm_module.token_store.record = AsyncMock()

    seen = []
    async def invoke(server_id, bare_name, args):
        seen.append((server_id, bare_name, args))
        return {"ok": True}

    text, _, msgs = _run(llm_module._gemini_chat(
        system="sys", messages=[{"role": "user", "content": "do it"}],
        tools=[{"name": "srv__do_thing", "description": "",
                "input_schema": {"type": "object", "properties": {}}}],
        invoke_tool=invoke,
        tool_server_map={"srv__do_thing": "srv"},
        on_event=None,
    ))

    assert text == "Listo."
    assert seen == [("srv", "do_thing", {"x": 1})]

    # Walk the new chunk and find the assistant turn with tool_use.
    new_chunk = msgs[1:]   # strip the original user message
    assistant_turn = next(m for m in new_chunk
                          if m["role"] == "assistant" and isinstance(m["content"], list)
                          and any(b.get("type") == "tool_use" for b in m["content"]))
    tool_use_block = next(b for b in assistant_turn["content"]
                          if b.get("type") == "tool_use")
    assert tool_use_block["name"] == "srv__do_thing"
    assert tool_use_block["input"] == {"x": 1}
    tu_id = tool_use_block["id"]
    assert tu_id.startswith("gem_"), "ID should be prefixed for traceability"

    # The follow-up user message must carry the matching tool_result
    # with the SAME tool_use_id.
    user_turn = next(m for m in new_chunk
                     if m["role"] == "user" and isinstance(m["content"], list)
                     and any(b.get("type") == "tool_result" for b in m["content"]))
    tr_block = next(b for b in user_turn["content"]
                    if b.get("type") == "tool_result")
    assert tr_block["tool_use_id"] == tu_id


def test_gemini_tool_use_id_stable_within_turn(llm_module, monkeypatch):
    """Two function_calls inside the SAME turn get DIFFERENT ids — the
    tool_result for call A doesn't accidentally point at call B's id."""
    # Single response with TWO function_call parts.
    p1 = MagicMock(); p1.text = None
    fc1 = MagicMock(); fc1.name = "srv__a"; fc1.args = {"k": 1}; p1.function_call = fc1
    p2 = MagicMock(); p2.text = None
    fc2 = MagicMock(); fc2.name = "srv__b"; fc2.args = {"k": 2}; p2.function_call = fc2
    content = MagicMock()
    content.parts = [p1, p2]
    candidate = MagicMock()
    candidate.content = content
    candidate.finish_reason = None
    resp1 = MagicMock()
    resp1.candidates = [candidate]
    resp1.usage_metadata = None
    resp2 = _fake_response_text_only("ok", llm_module)
    queue = iter([resp1, resp2])

    async def fake_generate(**kw): return next(queue)
    llm_module._gemini_generate_with_retry = fake_generate
    llm_module.token_store.record = AsyncMock()

    async def invoke(*a, **kw): return {"ok": True}

    _, _, msgs = _run(llm_module._gemini_chat(
        system="sys", messages=[{"role": "user", "content": "do both"}],
        tools=[
            {"name": "srv__a", "description": "", "input_schema": {"type": "object", "properties": {}}},
            {"name": "srv__b", "description": "", "input_schema": {"type": "object", "properties": {}}},
        ],
        invoke_tool=invoke,
        tool_server_map={"srv__a": "srv", "srv__b": "srv"},
        on_event=None,
    ))

    # Find the assistant turn with the 2 tool_use blocks.
    new_chunk = msgs[1:]
    assistant_turn = next(m for m in new_chunk
                          if m["role"] == "assistant" and isinstance(m["content"], list)
                          and sum(1 for b in m["content"] if b.get("type") == "tool_use") == 2)
    ids = [b["id"] for b in assistant_turn["content"] if b.get("type") == "tool_use"]
    assert len(ids) == 2
    assert ids[0] != ids[1], "two tool_use blocks must have distinct ids"

    user_turn = next(m for m in new_chunk
                     if m["role"] == "user" and isinstance(m["content"], list)
                     and sum(1 for b in m["content"] if b.get("type") == "tool_result") == 2)
    result_ids = [b["tool_use_id"] for b in user_turn["content"]
                  if b.get("type") == "tool_result"]
    # Each result references its corresponding tool_use by id, in order.
    assert result_ids == ids


def test_gemini_tool_invocation_exception_returned_as_error_envelope(llm_module, monkeypatch):
    """If invoke_tool raises, we send an error envelope back to Gemini
    on the next round instead of crashing the whole turn."""
    responses = [
        _fake_response_tool_call("srv__bad", {}, llm_module),
        _fake_response_text_only("Lo siento, falló.", llm_module),
    ]
    queue = iter(responses)
    async def fake_generate(**kw): return next(queue)
    llm_module._gemini_generate_with_retry = fake_generate
    llm_module.token_store.record = AsyncMock()

    async def invoke(*a, **kw):
        raise RuntimeError("nope")

    text, _, msgs = _run(llm_module._gemini_chat(
        system="sys", messages=[{"role": "user", "content": "trigger"}],
        tools=[{"name": "srv__bad", "description": "",
                "input_schema": {"type": "object", "properties": {}}}],
        invoke_tool=invoke,
        tool_server_map={"srv__bad": "srv"},
        on_event=None,
    ))
    # No exception escapes; the model produced its final reply.
    assert text == "Lo siento, falló."
    # And the user-turn carries the error envelope as tool_result content.
    new_chunk = msgs[1:]
    user_turn = next(m for m in new_chunk
                     if m["role"] == "user" and isinstance(m["content"], list)
                     and any(b.get("type") == "tool_result" for b in m["content"]))
    tr_block = next(b for b in user_turn["content"] if b.get("type") == "tool_result")
    assert "error" in str(tr_block["content"])


def test_uuid_import_present(llm_module):
    """v1.43.1: _gemini_chat mints tool_use_ids via uuid.uuid4; the
    module must import uuid at the top."""
    src = Path(llm_module.__file__).read_text(encoding="utf-8")
    assert "import uuid" in src
