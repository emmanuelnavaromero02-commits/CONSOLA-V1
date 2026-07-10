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
async def test_tenant_anthropic_key_is_loaded_from_workspace_vault(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_client = _load_llm_client()
    captured = {}

    async def fake_vault_secret(scope, key, user_context=None):
        captured["scope"] = scope
        captured["key"] = key
        captured["vault_user_context"] = user_context
        return "tenant-anthropic-key"

    async def fake_anthropic(*_args, **kwargs):
        captured["api_key"] = kwargs.get("api_key")
        captured["user_context"] = kwargs.get("user_context")
        return "ok", [], []

    monkeypatch.setattr(llm_client, "_vault_secret", fake_vault_secret)
    monkeypatch.setattr(llm_client, "_anthropic_chat", fake_anthropic)

    user_context = {
        "id": 42,
        "role": "user",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
    }
    reply, _viewer, _messages = await llm_client.chat(
        system="sys",
        messages=[],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
        user_context=user_context,
    )

    assert reply == "ok"
    assert captured["scope"] == "llm"
    assert captured["key"] == "anthropic_api_key"
    assert captured["vault_user_context"] == user_context
    assert captured["api_key"] == "tenant-anthropic-key"
    assert captured["user_context"] == user_context


@pytest.mark.asyncio
async def test_admin_anthropic_key_prefers_active_workspace_vault(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm_client = _load_llm_client()
    captured = {}

    async def fake_vault_secret(scope, key, user_context=None):
        captured["scope"] = scope
        captured["key"] = key
        captured["vault_user_context"] = user_context
        return "admin-workspace-anthropic-key"

    async def fake_anthropic(*_args, **kwargs):
        captured["api_key"] = kwargs.get("api_key")
        return "ok", [], []

    monkeypatch.setattr(llm_client, "_vault_secret", fake_vault_secret)
    monkeypatch.setattr(llm_client, "_anthropic_chat", fake_anthropic)

    user_context = {
        "id": 1,
        "role": "admin",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
    }
    reply, _viewer, _messages = await llm_client.chat(
        system="sys",
        messages=[],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
        user_context=user_context,
    )

    assert reply == "ok"
    assert captured["scope"] == "llm"
    assert captured["key"] == "anthropic_api_key"
    assert captured["vault_user_context"] == user_context
    assert captured["api_key"] == "admin-workspace-anthropic-key"


def test_workspace_vault_headers_include_signed_security_context(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT", "console_to_vault_key_with_more_than_32_chars")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "s" * 64)
    llm_client = _load_llm_client()

    headers = llm_client._vault_headers(
        {
            "id": 42,
            "role": "user",
            "workspace_role": "tenant_admin",
            "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "active_workspace_id": "11111111-1111-1111-1111-111111111111",
        }
    )

    assert headers["x-internal-service"] == "console"
    assert headers["x-api-key"] == "console_to_vault_key_with_more_than_32_chars"
    assert "x-security-context" in headers
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in headers["x-security-context"]


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
