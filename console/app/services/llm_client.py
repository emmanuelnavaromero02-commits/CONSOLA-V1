"""
LLM client — Anthropic, Gemini (native google-genai SDK), or Ollama.

Env vars:
  CHAT_LLM_PROVIDER  anthropic | gemini | ollama   (default: anthropic)
  CHAT_LLM_MODEL     model name
  ANTHROPIC_API_KEY
  GEMINI_API_KEY
  OLLAMA_URL
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import Any, Callable

import anthropic
from google import genai as google_genai
from google.genai import types as gtypes
from openai import AsyncOpenAI

from app.services import token_store

CHAT_PROVIDER       = os.environ.get("CHAT_LLM_PROVIDER", "anthropic")
OLLAMA_URL          = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
GEMINI_CACHE_ENABLED = os.environ.get("GEMINI_CACHE_ENABLED", "true").lower() == "true"

_PROVIDER_DEFAULTS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini":    "gemini-1.5-flash",
    "ollama":    "llama3.2",
}
CHAT_MODEL = os.environ.get(
    "CHAT_LLM_MODEL",
    _PROVIDER_DEFAULTS.get(CHAT_PROVIDER, "claude-haiku-4-5-20251001"),
)

_ant: anthropic.AsyncAnthropic | None = None
_google_client: google_genai.Client | None = None
_ollama: AsyncOpenAI | None = None


def _resolve_chat_model(model: str | None) -> str:
    value = (model or "").strip()
    if not value or value == "default":
        return CHAT_MODEL
    if CHAT_PROVIDER == "gemini" and not value.startswith("gemini-"):
        return CHAT_MODEL
    if CHAT_PROVIDER == "anthropic" and value.startswith(("gemini-", "llama")):
        return CHAT_MODEL
    if CHAT_PROVIDER == "ollama" and value.startswith(("claude-", "gemini-")):
        return CHAT_MODEL
    return value


def _anthropic_client() -> anthropic.AsyncAnthropic:
    global _ant
    if _ant is None:
        _ant = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _ant


def _gemini_client() -> google_genai.Client:
    global _google_client
    if _google_client is None:
        _google_client = google_genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return _google_client


def _ollama_client() -> AsyncOpenAI:
    global _ollama
    if _ollama is None:
        _ollama = AsyncOpenAI(api_key="ollama", base_url=f"{OLLAMA_URL}/v1/")
    return _ollama


async def chat(
    system: str,
    messages: list[dict],
    tools: list[dict],
    invoke_tool: Callable,
    tool_server_map: dict[str, str],
    on_event: Callable | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """If `on_event` is provided, it is awaited with dicts describing every
    tool invocation and its result, plus a final {'type':'text', 'text': reply}.
    The function still returns the same (reply, viewer_urls, messages) tuple
    so callers that ignore on_event keep working unchanged."""
    if CHAT_PROVIDER == "gemini":
        return await _gemini_chat(
            system, messages, tools, invoke_tool, tool_server_map, on_event,
            model=model, max_tokens=max_tokens, temperature=temperature,
        )
    if CHAT_PROVIDER == "ollama":
        return await _openai_compat_chat(
            system, messages, tools, invoke_tool, tool_server_map, _ollama_client(),
            model=model, max_tokens=max_tokens, temperature=temperature,
        )
    return await _anthropic_chat(
        system, messages, tools, invoke_tool, tool_server_map, on_event,
        model=model, max_tokens=max_tokens, temperature=temperature,
    )


# ── Result summarizer (for tool_result events) ────────────────────────────────

_SUMMARY_LABEL = {
    "data": "rows", "rows": "rows", "results": "results",
    "datasets": "datasets", "users": "users", "apps": "apps",
    "decisions": "decisions", "tools": "tools", "kpis": "kpis",
    "actions": "actions", "sources": "sources",
}


def _summarize_tool_result(result, tool: str) -> str:
    """Best-effort short summary of a tool result for the live trail."""
    try:
        if isinstance(result, dict):
            if "error" in result:
                return f"error: {str(result['error'])[:80]}"
            def _plural(n, label):
                return f"{n} {label[:-1] if (n == 1 and label.endswith('s')) else label}"

            # Prefer row_count when present (query_dataset / preview_transform)
            if "row_count" in result and isinstance(result["row_count"], int):
                return _plural(result["row_count"], "rows")
            for key, label in _SUMMARY_LABEL.items():
                if key in result and isinstance(result[key], list):
                    return _plural(len(result[key]), label)
            if "url" in result:
                return f"url: {result['url']}"
            if "saved" in result:
                return "saved"
            if "materialized" in result or "row_count" in result:
                return f"materialized · {result.get('row_count','?')} rows"
            keys = list(result.keys())[:4]
            return "ok · " + ", ".join(keys) if keys else "ok"
        if isinstance(result, list):
            return f"{len(result)} items"
        s = str(result)
        return s[:80] + ("…" if len(s) > 80 else "")
    except Exception:
        return "ok"


async def _emit(on_event, evt: dict):
    if on_event is None:
        return
    try:
        await on_event(evt)
    except Exception:
        pass


# ── Tool-result clipping (avoid blowing up the context window) ───────────────

_MAX_TOOL_RESULT_BYTES = int(os.environ.get("MAX_TOOL_RESULT_BYTES", "60000"))


def _clip_tool_result_for_model(result) -> str:
    """Serialize `result` to JSON, but if it's larger than _MAX_TOOL_RESULT_BYTES
    return a compact summary instead. The full result is still emitted to the
    UI via on_event — only the version we feed back to the model is clipped."""
    try:
        full = json.dumps(result, default=str)
    except Exception:
        full = str(result)
    if len(full) <= _MAX_TOOL_RESULT_BYTES:
        return full

    if isinstance(result, dict):
        clipped: dict = {}
        # Preserve schema/shape information whenever present
        for key in ("schema", "fields", "columns", "row_count", "url",
                    "name", "layer", "cartridge", "description"):
            if key in result:
                clipped[key] = result[key]
        # Carry first ~5 items of the largest list
        list_keys = [k for k, v in result.items() if isinstance(v, list)]
        for k in list_keys:
            preview = result[k][:5]
            clipped[k] = preview
            clipped[f"_{k}_total"] = len(result[k])
        clipped["_truncated"] = (
            f"result was {len(full)} bytes (>{_MAX_TOOL_RESULT_BYTES}); "
            "only schema and first 5 items of each list shown. "
            "Don't request raw data for dashboards — use the schema and let "
            "the published app fetch via /api/data/<dataset> at runtime."
        )
        return json.dumps(clipped, default=str)

    if isinstance(result, list):
        return json.dumps({
            "_truncated": (f"list of {len(result)} items, {len(full)} bytes; "
                           f"only first 5 shown"),
            "preview": result[:5],
        }, default=str)

    return json.dumps({
        "_truncated": f"{len(full)} bytes (>{_MAX_TOOL_RESULT_BYTES})",
        "preview": full[:1000],
    })


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_viewer_urls(result: Any) -> list[dict]:
    if not isinstance(result, dict):
        return []
    url = str(result.get("url", ""))
    if "/viewer/" not in url:
        return []
    relative = re.sub(r"^https?://[^/]+", "", url)
    return [{"url": relative, "label": result.get("label", relative)}]


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _content_to_dicts(content) -> list[dict]:
    out = []
    for block in content:
        if not hasattr(block, "type"):
            if isinstance(block, dict):
                out.append(block)
            continue
        if block.type == "text":
            out.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            out.append({"type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input})
    return out


async def _anthropic_chat(
    system: str,
    messages: list[dict],
    tools: list[dict],
    invoke_tool: Callable,
    tool_server_map: dict[str, str],
    on_event: Callable | None = None,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, list[dict], list[dict]]:
    chat_model = _resolve_chat_model(model)
    chat_max_tokens = int(max_tokens or 32000)
    ant_tools = [
        {
            "name":         t["name"],
            "description":  t.get("description", ""),
            "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]
    # Prompt caching: mark the last tool with cache_control to cache
    # the full block (system prompt + all tools) for 5 minutes.
    if ant_tools:
        ant_tools[-1]["cache_control"] = {"type": "ephemeral"}

    system_blocks = [{
        "type": "text",
        "text": system,
        "cache_control": {"type": "ephemeral"},
    }]

    msgs = list(messages)
    viewer_urls: list[dict] = []

    for _i in range(20):
        # Stream the SDK call (Anthropic recommends it for max_tokens >8k or
        # operations that may exceed 10 min). We still accumulate the final
        # message and use it the same way as a non-streamed response.
        kwargs = {
            "model": chat_model,
            "max_tokens": chat_max_tokens,
            "system": system_blocks,
            "tools": ant_tools or [],
            "messages": msgs,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        async with _anthropic_client().messages.stream(**kwargs) as stream:
            response = await stream.get_final_message()
        usage = response.usage
        cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        await token_store.record(
            "anthropic", chat_model,
            usage.input_tokens, usage.output_tokens,
            cache_create, cache_read,
        )
        content_dicts = _content_to_dicts(response.content)
        tool_use_blocks = [b for b in content_dicts if b.get("type") == "tool_use"]

        # Detect by content, not stop_reason — max_tokens cutoffs leave
        # tool_use blocks with stop_reason='max_tokens', which used to fall
        # into the "final text" branch and produce orphan tool_use in saved
        # messages, breaking the next request with 400.
        if not tool_use_blocks:
            text = next((b["text"] for b in content_dicts if b.get("type") == "text"), "")
            await _emit(on_event, {"type": "text", "text": text})
            return text, viewer_urls, msgs + [{"role": "assistant", "content": content_dicts}]

        tool_results = []
        for block in tool_use_blocks:
            server_id = tool_server_map.get(block["name"], "")
            bare_name = block["name"].split("__", 1)[-1]
            args      = block.get("input") or {}
            await _emit(on_event, {
                "type":   "tool_use",
                "tool":   bare_name,
                "server": server_id,
                "args":   args,
            })
            try:
                result = await invoke_tool(server_id, bare_name, args)
            except Exception as exc:                            # noqa: BLE001
                result = {"error": f"tool invocation failed: {exc}"}
            viewer_urls.extend(_extract_viewer_urls(result))
            await _emit(on_event, {
                "type":    "tool_result",
                "tool":    bare_name,
                "summary": _summarize_tool_result(result, bare_name),
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": _clip_tool_result_for_model(result),
            })
        msgs = msgs + [
            {"role": "assistant", "content": content_dicts},
            {"role": "user",      "content": tool_results},
        ]

    return "(máximo de iteraciones alcanzado)", viewer_urls, msgs


# ── Gemini native (google-genai) ──────────────────────────────────────────────

_GENAI_TYPE_MAP = {
    "string":  gtypes.Type.STRING,
    "number":  gtypes.Type.NUMBER,
    "integer": gtypes.Type.INTEGER,
    "boolean": gtypes.Type.BOOLEAN,
    "array":   gtypes.Type.ARRAY,
    "object":  gtypes.Type.OBJECT,
}


def _to_genai_schema(schema: dict) -> gtypes.Schema:
    raw = schema.get("type", "object")
    if isinstance(raw, list):
        raw = next((t for t in raw if t != "null"), "string")
    stype = _GENAI_TYPE_MAP.get(str(raw).lower(), gtypes.Type.OBJECT)

    kwargs: dict[str, Any] = {"type": stype}

    desc = schema.get("description", "")
    if desc:
        kwargs["description"] = desc

    props = schema.get("properties", {})
    if props:
        kwargs["properties"] = {k: _to_genai_schema(v) for k, v in props.items()}

    if stype == gtypes.Type.ARRAY:
        items_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}
        kwargs["items"] = _to_genai_schema(items_schema)
    elif "items" in schema and isinstance(schema["items"], dict):
        kwargs["items"] = _to_genai_schema(schema["items"])

    if schema.get("required"):
        kwargs["required"] = schema["required"]

    if schema.get("enum"):
        kwargs["enum"] = schema["enum"]

    return gtypes.Schema(**kwargs)


# Match Google's retryDelay across the shapes it shows up in:
#   "retry in 5s" / "retry after 5 seconds"
#   retryDelay: "5s"  /  retry_delay: "5s"
#   retry_delay { seconds: 5 }  (proto text format)
_GEMINI_RETRY_PATTERNS = [
    re.compile(r"retry\s*(?:in|after)\s+([\d.]+)\s*s", re.IGNORECASE),
    re.compile(r"retry[_]?delay[\s:=\"']*([\d.]+)\s*s", re.IGNORECASE),
    re.compile(r"retry[_]?delay[^}]*?seconds:\s*([\d.]+)", re.IGNORECASE),
]


def _gemini_retry_delay(msg: str, attempt: int) -> float:
    for pat in _GEMINI_RETRY_PATTERNS:
        m = pat.search(msg)
        if m:
            return min(float(m.group(1)) + 2, 60)
    return min(5 * (2 ** attempt), 60)  # 5, 10, 20, 40


async def _gemini_generate_with_retry(*, model: str, contents, config):
    """Wrap generate_content with retry on transient 503/429 from Gemini."""
    for attempt in range(4):
        try:
            return await _gemini_client().aio.models.generate_content(
                model=model, contents=contents, config=config,
            )
        except Exception as exc:
            msg = str(exc)
            transient = (
                "503" in msg or "UNAVAILABLE" in msg
                or "429" in msg or "RESOURCE_EXHAUSTED" in msg
            )
            if transient and attempt < 3:
                await asyncio.sleep(_gemini_retry_delay(msg, attempt))
                continue
            raise


_gemini_cache_by_sig: dict[str, str] = {}  # hash(system+tools) → cache resource name


def _gemini_cached_content_is_stale(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "cachedcontent not found" in msg
        or "cached content not found" in msg
        or ("cached_content" in msg and "not found" in msg)
        or ("cachedcontent" in msg and "permission_denied" in msg)
    )


def _gemini_cache_signature(system: str, tools: list[dict], model: str | None = None) -> str:
    import hashlib
    blob = (model or CHAT_MODEL) + "|" + system + "|" + "|".join(sorted(t["name"] for t in tools))
    # v1.43.2 (DevOps R1 follow-up): MD5 is used only as a cache-key
    # fingerprint for the Gemini cached-content resource — never for
    # authentication or integrity. ``usedforsecurity=False`` is the
    # Python 3.9+ contract that opts a hash out of FIPS-mode bans
    # and silences bandit B324.
    return hashlib.md5(blob.encode(), usedforsecurity=False).hexdigest()


async def _get_or_create_gemini_cache(
    system: str, tools: list[dict], gemini_tools: list | None, model: str,
) -> str | None:
    """Return a cached_content resource name, or None if caching unavailable."""
    sig = _gemini_cache_signature(system, tools, model)
    if sig in _gemini_cache_by_sig:
        return _gemini_cache_by_sig[sig]
    try:
        cache = await _gemini_client().aio.caches.create(
            model=model,
            config=gtypes.CreateCachedContentConfig(
                system_instruction=system,
                tools=gemini_tools,
                ttl="3600s",  # 1 hour
            ),
        )
        _gemini_cache_by_sig[sig] = cache.name
        return cache.name
    except Exception:
        return None  # too few tokens, model unsupported, or quota — fall back


def _gemini_inline_config(
    system: str,
    gemini_tools: list | None,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    kwargs: dict[str, Any] = dict(
        system_instruction=system,
        tools=gemini_tools,
    )
    if max_tokens is not None:
        kwargs["max_output_tokens"] = int(max_tokens)
    if temperature is not None:
        kwargs["temperature"] = temperature
    return gtypes.GenerateContentConfig(**kwargs)


def _proto_args_to_plain_dict(args) -> dict:
    """v1.43.1 R1 LLM-F3: ``fc.args`` from the google-genai SDK is a
    ``proto.marshal.collections.maps.MapComposite``. ``dict(args)``
    only does the TOP-LEVEL conversion — nested objects / arrays
    remain proto types and crash ``json.dumps`` later when
    copilot_service tries to persist them into
    ``conversation_messages.tool_calls`` JSONB.

    Walk the structure recursively: anything that quacks like a mapping
    becomes a ``dict``, sequences become ``list`` and primitives flow
    through. Unknown types collapse to ``str(value)`` as a last resort
    so we never raise from this conversion.
    """
    def _walk(v):
        if isinstance(v, dict):
            return {str(k): _walk(vv) for k, vv in v.items()}
        if hasattr(v, "items") and not isinstance(v, (str, bytes)):
            # MapComposite, OrderedDict subclasses, any mapping-like.
            return {str(k): _walk(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_walk(x) for x in v]
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        return str(v)

    if args is None:
        return {}
    out = _walk(args)
    # The top level must be a dict — proto MapComposite always is, but
    # a misbehaving caller could pass a list; coerce defensively.
    return out if isinstance(out, dict) else {"_args": out}


async def _gemini_chat(
    system: str,
    messages: list[dict],
    tools: list[dict],
    invoke_tool: Callable,
    tool_server_map: dict[str, str],
    on_event: Callable | None = None,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, list[dict], list[dict]]:
    chat_model = _resolve_chat_model(model)
    fn_decls = [
        gtypes.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=_to_genai_schema(t.get("input_schema", {"type": "object", "properties": {}})),
        )
        for t in tools
    ]
    gemini_tools = [gtypes.Tool(function_declarations=fn_decls)] if fn_decls else None

    # Try to use prompt cache (system + tools); fall back to inline if unavailable.
    cache_sig = _gemini_cache_signature(system, tools, chat_model) if GEMINI_CACHE_ENABLED else None
    cache_name = (
        await _get_or_create_gemini_cache(system, tools, gemini_tools, chat_model)
        if GEMINI_CACHE_ENABLED else None
    )
    if cache_name:
        cfg_kwargs: dict[str, Any] = {"cached_content": cache_name}
        if max_tokens is not None:
            cfg_kwargs["max_output_tokens"] = int(max_tokens)
        if temperature is not None:
            cfg_kwargs["temperature"] = temperature
        gen_config = gtypes.GenerateContentConfig(**cfg_kwargs)
    else:
        gen_config = _gemini_inline_config(
            system, gemini_tools, max_tokens=max_tokens, temperature=temperature,
        )

    # v1.43.1 R1 LLM-F1: walk every message, NOT just string-content
    # ones. _load_history rehydrates prior turns as Anthropic-shape
    # blocks (assistant with tool_use, user with tool_result). Without
    # converting those into Gemini's Part.from_function_call /
    # from_function_response, multi-turn history is dropped before
    # Gemini sees it and the approval-gate round-trip silently breaks.
    #
    # We also track ``tool_use_id → function name`` so a follow-up
    # tool_result block (which only carries the id) can be reattached
    # to the right function by name when building the Part.
    contents: list[gtypes.Content] = []
    tool_name_by_id: dict[str, str] = {}
    for m in messages:
        content = m.get("content", "")
        role = "user" if m["role"] == "user" else "model"
        if isinstance(content, str):
            contents.append(gtypes.Content(
                role=role, parts=[gtypes.Part.from_text(text=content)],
            ))
            continue
        if not isinstance(content, list):
            continue

        parts: list = []
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "text":
                parts.append(gtypes.Part.from_text(text=block.get("text") or ""))
            elif btype == "tool_use":
                tu_id = block.get("id")
                fn_name = block.get("name") or ""
                fn_input = block.get("input") or {}
                if tu_id:
                    tool_name_by_id[tu_id] = fn_name
                parts.append(gtypes.Part.from_function_call(
                    name=fn_name, args=fn_input,
                ))
            elif btype == "tool_result":
                tu_id = block.get("tool_use_id")
                # Recover the function name via the lookup we built
                # while walking the matching assistant turn earlier.
                fn_name = tool_name_by_id.get(tu_id, "") or ""
                body = block.get("content")
                if isinstance(body, (dict, list)):
                    response_payload = {"result": body}
                else:
                    response_payload = {"result": str(body) if body is not None else ""}
                parts.append(gtypes.Part.from_function_response(
                    name=fn_name, response=response_payload,
                ))
        if parts:
            contents.append(gtypes.Content(role=role, parts=parts))

    viewer_urls: list[dict] = []
    # v1.43.1 (Codex P0-2): accumulate Anthropic-shape blocks for the
    # caller. ``contents`` is Gemini's internal format; copilot_service
    # expects ``role/content`` dicts with ``tool_use``/``tool_result``
    # blocks (same shape llm_client._anthropic_chat returns). We build
    # them here per turn so the approval gate, persistence and
    # history-reload paths work identically with both providers.
    synthetic: list[dict] = list(messages)

    for _i in range(20):
        try:
            response = await _gemini_generate_with_retry(
                model=chat_model,
                contents=contents,
                config=gen_config,
            )
        except Exception as exc:
            if cache_name and cache_sig and _gemini_cached_content_is_stale(exc):
                _gemini_cache_by_sig.pop(cache_sig, None)
                cache_name = None
                gen_config = _gemini_inline_config(
                    system, gemini_tools, max_tokens=max_tokens, temperature=temperature,
                )
                response = await _gemini_generate_with_retry(
                    model=chat_model,
                    contents=contents,
                    config=gen_config,
                )
            else:
                raise

        if response.usage_metadata:
            um = response.usage_metadata
            cached = getattr(um, "cached_content_token_count", 0) or 0
            # prompt_token_count includes the cached portion — split them so cost is correct
            non_cached_input = max((um.prompt_token_count or 0) - cached, 0)
            await token_store.record(
                "gemini", chat_model,
                non_cached_input,
                um.candidates_token_count or 0,
                0,        # cache_creation tokens (Gemini bills creation only when calling caches.create)
                cached,   # cache_read tokens
            )

        candidate = response.candidates[0]
        finish    = getattr(candidate, "finish_reason", None)
        parts     = list(candidate.content.parts) if (candidate.content and candidate.content.parts) else []
        fn_calls  = [p for p in parts if p.function_call]
        text_parts = [p.text for p in parts if getattr(p, "text", None)]

        if not fn_calls:
            try:
                text = response.text or ""
            except Exception:
                text = " ".join(text_parts)
            if not text:
                reason = str(finish) if finish else "unknown"
                text = f"(Gemini no devolvió contenido — razón: {reason})"
            contents.append(gtypes.Content(role="model", parts=parts))
            await _emit(on_event, {"type": "text", "text": text})
            # Anthropic-shape final block so copilot_service persists
            # the text + any preceding tool_use/tool_result pairs.
            synthetic.append({
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            })
            return text, viewer_urls, synthetic

        contents.append(gtypes.Content(role="model", parts=parts))

        # Build Anthropic-shape assistant turn: text first (if any),
        # then one tool_use block per function_call with a stable id
        # the matching tool_result block will reference.
        assistant_blocks: list[dict] = []
        for t in text_parts:
            assistant_blocks.append({"type": "text", "text": t})
        # Map each Gemini function_call to a stable tool_use_id. Hex of
        # uuid4 is fine — it lives only within this turn's history and
        # never crosses provider boundaries.
        per_call_ids: list[str] = []
        for p in fn_calls:
            fc = p.function_call
            tool_use_id = f"gem_{uuid.uuid4().hex[:16]}"
            per_call_ids.append(tool_use_id)
            assistant_blocks.append({
                "type":  "tool_use",
                "id":    tool_use_id,
                "name":  fc.name,
                # v1.43.1 R1 LLM-F3: deep-convert before persistence.
                "input": _proto_args_to_plain_dict(fc.args),
            })
        synthetic.append({"role": "assistant", "content": assistant_blocks})

        fn_resp_parts = []
        tool_result_blocks: list[dict] = []
        for idx, p in enumerate(fn_calls):
            fc = p.function_call
            args = _proto_args_to_plain_dict(fc.args)
            server_id = tool_server_map.get(fc.name, "")
            bare_name = fc.name.split("__", 1)[-1]
            await _emit(on_event, {
                "type":   "tool_use",
                "tool":   bare_name,
                "server": server_id,
                "args":   args,
            })
            try:
                result = await invoke_tool(server_id, bare_name, args)
            except Exception as exc:                  # noqa: BLE001
                result = {"error": f"tool invocation failed: {exc}"}
            viewer_urls.extend(_extract_viewer_urls(result))
            await _emit(on_event, {
                "type":    "tool_result",
                "tool":    bare_name,
                "summary": _summarize_tool_result(result, bare_name),
            })
            clipped = _clip_tool_result_for_model(result)
            fn_resp_parts.append(
                gtypes.Part.from_function_response(
                    name=fc.name,
                    response={"result": clipped},
                )
            )
            tool_result_blocks.append({
                "type":         "tool_result",
                "tool_use_id":  per_call_ids[idx],
                "content":      clipped,
            })
        contents.append(gtypes.Content(role="user", parts=fn_resp_parts))
        synthetic.append({"role": "user", "content": tool_result_blocks})

    return "(máximo de iteraciones alcanzado)", viewer_urls, synthetic


# ── OpenAI-compatible (Ollama) ────────────────────────────────────────────────

def _to_oai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t.get("description", ""),
                "parameters":  t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


async def _openai_compat_chat(
    system: str,
    messages: list[dict],
    tools: list[dict],
    invoke_tool: Callable,
    tool_server_map: dict[str, str],
    client: AsyncOpenAI,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, list[dict], list[dict]]:
    chat_model = _resolve_chat_model(model)
    oai_tools = _to_oai_tools(tools) if tools else []
    msgs: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": m["role"], "content": content})

    viewer_urls: list[dict] = []

    for _i in range(20):
        kwargs: dict[str, Any] = {"model": chat_model, "messages": msgs}
        if oai_tools:
            kwargs["tools"] = oai_tools
        if max_tokens is not None:
            kwargs["max_tokens"] = int(max_tokens)
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg    = choice.message
        finish = choice.finish_reason or "stop"

        if response.usage:
            await token_store.record(
                CHAT_PROVIDER, chat_model,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

        if finish != "tool_calls" or not msg.tool_calls:
            text = msg.content or ""
            msgs.append({"role": "assistant", "content": text})
            return text, viewer_urls, msgs

        msgs.append(msg)
        for tc in msg.tool_calls:
            fn   = tc.function
            args = json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
            server_id = tool_server_map.get(fn.name, "")
            bare_name = fn.name.split("__", 1)[-1]
            result    = await invoke_tool(server_id, bare_name, args)
            viewer_urls.extend(_extract_viewer_urls(result))
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    return "(máximo de iteraciones alcanzado)", viewer_urls, msgs
