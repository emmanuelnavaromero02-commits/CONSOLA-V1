"""
LLM client — Anthropic by default, with Ollama reserved for local developer use.

Env vars:
  CHAT_LLM_PROVIDER  anthropic | ollama   (default: anthropic)
  CHAT_LLM_MODEL     model name
  ANTHROPIC_API_KEY
  OLLAMA_URL
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

import anthropic
from openai import AsyncOpenAI

from app.services import token_store

CHAT_PROVIDER       = os.environ.get("CHAT_LLM_PROVIDER", "anthropic")
OLLAMA_URL          = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")

_PROVIDER_DEFAULTS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama":    "llama3.2",
}
CHAT_MODEL = os.environ.get(
    "CHAT_LLM_MODEL",
    _PROVIDER_DEFAULTS.get(CHAT_PROVIDER, "claude-haiku-4-5-20251001"),
)

_ant: anthropic.AsyncAnthropic | None = None
_ollama: AsyncOpenAI | None = None


class LLMConfigurationError(RuntimeError):
    """Raised before provider SDKs are constructed when required config is absent."""


class LLMProviderError(RuntimeError):
    """Sanitized provider failure safe to show to Studio/Copilot callers."""


def _current_provider() -> str:
    provider = os.environ.get("CHAT_LLM_PROVIDER", CHAT_PROVIDER).strip().lower() or "anthropic"
    # Gemini is intentionally disabled as an operational provider. Coerce stale
    # local .env files back to Anthropic so Copilot/Studio do not keep failing
    # against a provider the deployment no longer supports.
    if provider == "gemini":
        return "anthropic"
    return provider


def _provider_config_error(provider: str | None = None) -> str:
    provider_name = (provider or _current_provider()).strip().lower()
    if provider_name == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "ANTHROPIC_API_KEY is required when CHAT_LLM_PROVIDER=anthropic"
    if provider_name not in {"anthropic", "ollama"}:
        return f"Unsupported CHAT_LLM_PROVIDER={provider_name!r}"
    return ""


def _ensure_provider_configured(provider: str | None = None) -> None:
    reason = _provider_config_error(provider)
    if reason:
        raise LLMConfigurationError(reason)


def _provider_error_message(provider: str, exc: Exception) -> str:
    msg = str(exc).lower()
    if any(token in msg for token in ("401", "unauthorized", "invalid api key", "api key", "x-api-key", "authentication")):
        if provider == "anthropic":
            return "Anthropic authentication failed; verify ANTHROPIC_API_KEY"
        return f"{provider} authentication failed; verify provider credentials"
    if any(token in msg for token in ("429", "rate limit", "rate_limit", "resource_exhausted")):
        return f"{provider} rate limit reached; retry later or use another key"
    if any(token in msg for token in ("timeout", "timed out")):
        return f"{provider} request timed out"
    return f"{provider} provider returned an error; check server logs"


def _resolve_chat_model(model: str | None) -> str:
    provider = _current_provider()
    provider_default = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["anthropic"])
    configured_default = os.environ.get("CHAT_LLM_MODEL", "").strip()
    if provider == "anthropic" and configured_default and configured_default.startswith(("gemini-", "llama")):
        configured_default = ""
    if provider == "ollama" and configured_default and configured_default.startswith(("claude-", "gemini-")):
        configured_default = ""
    default_model = configured_default or provider_default
    value = (model or "").strip()
    if not value or value == "default":
        return default_model
    if provider == "anthropic" and value.startswith(("gemini-", "llama")):
        return default_model
    if provider == "ollama" and value.startswith(("claude-", "gemini-")):
        return default_model
    return value


def _anthropic_client() -> anthropic.AsyncAnthropic:
    global _ant
    if _ant is None:
        _ant = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _ant


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
    provider = _current_provider()
    _ensure_provider_configured(provider)
    try:
        if provider == "ollama":
            return await _openai_compat_chat(
                system, messages, tools, invoke_tool, tool_server_map, _ollama_client(),
                on_event,
                model=model, max_tokens=max_tokens, temperature=temperature,
            )
        return await _anthropic_chat(
            system, messages, tools, invoke_tool, tool_server_map, on_event,
            model=model, max_tokens=max_tokens, temperature=temperature,
        )
    except LLMConfigurationError:
        raise
    except Exception as exc:
        raise LLMProviderError(_provider_error_message(provider, exc)) from exc


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
            async for text_delta in stream.text_stream:
                if text_delta:
                    await _emit(on_event, {"type": "text_delta", "text": text_delta})
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


def _oai_message_value(msg: Any, key: str, default: Any = None) -> Any:
    if isinstance(msg, dict):
        return msg.get(key, default)
    return getattr(msg, key, default)


def _oai_tool_call_parts(tc: Any) -> tuple[str, str, dict]:
    fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
    call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
    if fn is None:
        return str(call_id or "tool_call"), "", {}
    name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")
    raw_args = fn.get("arguments", {}) if isinstance(fn, dict) else getattr(fn, "arguments", {})
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = {}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}
    return str(call_id or name or "tool_call"), str(name or ""), args


def _oai_assistant_message_for_history(msg: Any) -> dict:
    if isinstance(msg, dict):
        return msg
    if hasattr(msg, "model_dump"):
        return msg.model_dump(exclude_none=True)
    return {
        "role": "assistant",
        "content": _oai_message_value(msg, "content", "") or "",
        "tool_calls": _oai_message_value(msg, "tool_calls", None),
    }


async def _openai_compat_chat(
    system: str,
    messages: list[dict],
    tools: list[dict],
    invoke_tool: Callable,
    tool_server_map: dict[str, str],
    client: AsyncOpenAI,
    on_event: Callable | None = None,
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

        if response.usage:
            await token_store.record(
                CHAT_PROVIDER, chat_model,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

        tool_calls = _oai_message_value(msg, "tool_calls") or []
        if not tool_calls:
            text = _oai_message_value(msg, "content", "") or ""
            await _emit(on_event, {"type": "text", "text": text})
            msgs.append({"role": "assistant", "content": text})
            return text, viewer_urls, msgs

        msgs.append(_oai_assistant_message_for_history(msg))
        for tc in tool_calls:
            tool_call_id, fn_name, args = _oai_tool_call_parts(tc)
            if not fn_name:
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({"error": "invalid tool call"}),
                })
                continue
            server_id = tool_server_map.get(fn_name, "")
            bare_name = fn_name.split("__", 1)[-1]
            await _emit(on_event, {
                "type":   "tool_use",
                "tool":   bare_name,
                "server": server_id,
                "args":   args,
            })
            result    = await invoke_tool(server_id, bare_name, args)
            viewer_urls.extend(_extract_viewer_urls(result))
            await _emit(on_event, {
                "type":    "tool_result",
                "tool":    bare_name,
                "summary": _summarize_tool_result(result, bare_name),
            })
            msgs.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result)})

    return "(máximo de iteraciones alcanzado)", viewer_urls, msgs
