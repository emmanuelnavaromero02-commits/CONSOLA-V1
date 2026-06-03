from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _load_llm_client():
    repo = Path(__file__).resolve().parents[1]
    siblings = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    return importlib.import_module("app.services.llm_client")


def test_anthropic_provider_ignores_stale_gemini_model(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CHAT_LLM_MODEL", "gemini-1.5-flash")
    llm_client = _load_llm_client()

    assert llm_client._resolve_chat_model(None).startswith("claude-")
    assert llm_client._resolve_chat_model("gemini-2.5-flash").startswith("claude-")


@pytest.mark.asyncio
async def test_chat_preflights_missing_anthropic_key(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_client = _load_llm_client()

    with pytest.raises(llm_client.LLMConfigurationError, match="ANTHROPIC_API_KEY"):
        await llm_client.chat(
            system="sys",
            messages=[],
            tools=[],
            invoke_tool=None,
            tool_server_map={},
        )


@pytest.mark.asyncio
async def test_stale_gemini_provider_is_coerced_to_anthropic(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "gemini")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_client = _load_llm_client()

    assert llm_client._current_provider() == "anthropic"
    with pytest.raises(llm_client.LLMConfigurationError, match="ANTHROPIC_API_KEY"):
        await llm_client.chat(
            system="sys",
            messages=[],
            tools=[],
            invoke_tool=None,
            tool_server_map={},
        )


@pytest.mark.asyncio
async def test_chat_sanitizes_anthropic_auth_error(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key-for-test")
    llm_client = _load_llm_client()

    async def broken_anthropic(*_args, **_kwargs):
        raise RuntimeError("401 Unauthorized: invalid x-api-key fake-anthropic-key-for-test")

    monkeypatch.setattr(llm_client, "_anthropic_chat", broken_anthropic)

    with pytest.raises(llm_client.LLMProviderError) as exc_info:
        await llm_client.chat(
            system="sys",
            messages=[],
            tools=[],
            invoke_tool=None,
            tool_server_map={},
        )

    message = str(exc_info.value)
    assert "Anthropic authentication failed" in message
    assert "fake-anthropic-key-for-test" not in message


@pytest.mark.asyncio
async def test_chat_reports_anthropic_credit_limit_clearly(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key-for-test")
    llm_client = _load_llm_client()

    async def billing_blocked(*_args, **_kwargs):
        raise RuntimeError("Your credit balance is too low to access the Anthropic API. Please purchase credits.")

    monkeypatch.setattr(llm_client, "_anthropic_chat", billing_blocked)

    with pytest.raises(llm_client.LLMProviderError) as exc_info:
        await llm_client.chat(
            system="sys",
            messages=[],
            tools=[],
            invoke_tool=None,
            tool_server_map={},
        )

    message = str(exc_info.value)
    assert "Anthropic billing or credit limit reached" in message
    assert "fake-anthropic-key-for-test" not in message
