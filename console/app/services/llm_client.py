from __future__ import annotations

import json
import os
import re
from typing import Any, Callable
from urllib.parse import quote

import anthropic
import httpx
from openai import AsyncOpenAI

from app.security import get_internal_api_key
from app.services.security_context import build_security_context
from app.services import token_store

CHAT_PROVIDER       = os.environ.get("CHAT_LLM_PROVIDER", "anthropic")
OLLAMA_URL          = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
VAULT_URL           = os.environ.get("VAULT_URL", "http://vault:8300").rstrip("/")

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
    pass


class LLMProviderError(RuntimeError):
    pass


def _current_provider() -> str:
    provider = os.environ.get("CHAT_LLM_PROVIDER", CHAT_PROVIDER).strip().lower() or "anthropic"
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


def _is_platform_admin_context(user_context: dict | None) -> bool:
    role = str((user_context or {}).get("role") or "").strip()
    return role in {"owner", "super_admin", "admin"}


def _tenant_scope_parts(user_context: dict | None) -> tuple[str | None, str | None]:
    if not user_context:
        return None, None
    tenant_id = str(
        user_context.get("active_tenant_id") or user_context.get("tenant_id") or ""
    ).strip() or None
    workspace_id = str(
        user_context.get("active_workspace_id") or user_context.get("workspace_id") or ""
    ).strip() or None
    return tenant_id, workspace_id


def _tenant_llm_vault_scope(user_context: dict) -> str:
    tenant_id, workspace_id = _tenant_scope_parts(user_context)
    if not tenant_id or not workspace_id:
        raise LLMConfigurationError("active tenant/workspace is required for workspace LLM credentials")
    return "llm"


def _vault_headers(user_context: dict | None = None) -> dict[str, str]:
    pair_key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_VAULT") or get_internal_api_key()
    headers = {"x-api-key": pair_key, "x-internal-service": "console"}
    if user_context:
        headers["x-security-context"] = json.dumps(build_security_context(user_context), ensure_ascii=False)
    return headers


async def _vault_secret(scope: str, key: str, user_context: dict | None = None) -> str | None:
    try:
        async with httpx.AsyncClient(headers=_vault_headers(user_context), timeout=5) as client:
            response = await client.get(
                f"{VAULT_URL}/secrets/{quote(scope, safe='')}/{quote(key, safe='')}"
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        raise LLMConfigurationError("workspace LLM credential lookup failed") from exc
    value = data.get("value") if isinstance(data, dict) else None
    return str(value).strip() if value else None


async def _resolve_anthropic_api_key(user_context: dict | None = None) -> str:
    tenant_id, workspace_id = _tenant_scope_parts(user_context)
    if user_context and tenant_id and workspace_id:
        scope = "llm"
        value = await _vault_secret(scope, "anthropic_api_key", user_context)
        if value:
            return value
        if not _is_platform_admin_context(user_context):
            raise LLMConfigurationError(
                "Anthropic API key is required for this workspace; configure it in Operations > Vault"
            )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return api_key
    if tenant_id and workspace_id:
        raise LLMConfigurationError(
            "Anthropic API key is required for this workspace; configure it in Operations > Vault"
        )
    else:
        raise LLMConfigurationError("ANTHROPIC_API_KEY is required when CHAT_LLM_PROVIDER=anthropic")


def _provider_error_message(provider: str, exc: Exception) -> str:
    msg = str(exc).lower()
    if any(token in msg for token in ("401", "unauthorized", "invalid api key", "api key", "x-api-key", "authentication")):
        if provider == "anthropic":
            return "Anthropic authentication failed; verify ANTHROPIC_API_KEY"
        return f"{provider} authentication failed; verify provider credentials"
    if any(token in msg for token in ("429", "rate limit", "rate_limit", "resource_exhausted")):
        return f"{provider} rate limit reached; retry later or use another key"
    if any(token in msg for token in ("credit balance", "billing", "purchase credits", "insufficient_quota")):
        if provider == "anthropic":
            return "Anthropic billing or credit limit reached; add credits before using the live copilot"
        return f"{provider} billing or credit limit reached"
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


def _anthropic_client(api_key: str | None = None) -> anthropic.AsyncAnthropic:
    global _ant
    if api_key:
        return anthropic.AsyncAnthropic(api_key=api_key)
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
    user_context: dict | None = None,
) -> tuple[str, list[dict], list[dict]]:
    provider = _current_provider()
    anthropic_api_key: str | None = None
    if provider == "anthropic":
        anthropic_api_key = await _resolve_anthropic_api_key(user_context)
    else:
        _ensure_provider_configured(provider)
    try:
        if provider == "ollama":
            return await _openai_compat_chat(
                system, messages, tools, invoke_tool, tool_server_map, _ollama_client(),
                on_event,
                model=model, max_tokens=max_tokens, temperature=temperature,
                user_context=user_context,
            )
        return await _anthropic_chat(
            system, messages, tools, invoke_tool, tool_server_map, on_event,
            model=model, max_tokens=max_tokens, temperature=temperature,
            api_key=anthropic_api_key,
            user_context=user_context,
        )
    except LLMConfigurationError:
        raise
    except Exception as exc:
        raise LLMProviderError(_provider_error_message(provider, exc)) from exc


_SUMMARY_LABEL = {
    "data": "rows", "rows": "rows", "results": "results",
    "datasets": "datasets", "users": "users", "apps": "apps",
    "decisions": "decisions", "tools": "tools", "kpis": "kpis",
    "actions": "actions", "sources": "sources",
}


def _summarize_tool_result(result, tool: str) -> str:
    try:
        if isinstance(result, dict):
            if "error" in result:
                return f"error: {str(result['error'])[:80]}"
            def _plural(n, label):
                return f"{n} {label[:-1] if (n == 1 and label.endswith('s')) else label}"

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


_MAX_TOOL_RESULT_BYTES = int(os.environ.get("MAX_TOOL_RESULT_BYTES", "60000"))


def _clip_tool_result_for_model(result) -> str:
    try:
        full = json.dumps(result, default=str)
    except Exception:
        full = str(result)
    if len(full) <= _MAX_TOOL_RESULT_BYTES:
        return full

    if isinstance(result, dict):
        clipped: dict = {}
        for key in ("schema", "fields", "columns", "row_count", "url",
                    "name", "layer", "cartridge", "description"):
            if key in result:
                clipped[key] = result[key]
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


def _extract_viewer_urls(result: Any) -> list[dict]:
    if not isinstance(result, dict):
        return []
    url = str(result.get("url", ""))
    if "/viewer/" not in url:
        return []
    relative = re.sub(r"^https?://[^/]+", "", url)
    return [{"url": relative, "label": result.get("label", relative)}]


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
    api_key: str | None = None,
    user_context: dict | None = None,
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
        kwargs = {
            "model": chat_model,
            "max_tokens": chat_max_tokens,
            "system": system_blocks,
            "tools": ant_tools or [],
            "messages": msgs,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        async with _anthropic_client(api_key).messages.stream(**kwargs) as stream:
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
            user_context=user_context,
        )
        content_dicts = _content_to_dicts(response.content)
        tool_use_blocks = [b for b in content_dicts if b.get("type") == "tool_use"]

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
    user_context: dict | None = None,
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
                user_context=user_context,
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
