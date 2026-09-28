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
async def test_admin_anthropic_key_prefers_workspace_vault(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "global-anthropic-key")
    llm_client = _load_llm_client()
    captured = {}

    async def fake_vault_secret(scope, key, user_context=None):
        captured["scope"] = scope
        captured["key"] = key
        captured["vault_user_context"] = user_context
        return "workspace-admin-key"

    monkeypatch.setattr(llm_client, "_vault_secret", fake_vault_secret)

    user_context = {
        "id": 42,
        "role": "admin",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
    }

    assert await llm_client._resolve_anthropic_api_key(user_context) == "workspace-admin-key"
    assert captured["scope"] == "llm"
    assert captured["key"] == "anthropic_api_key"
    assert captured["vault_user_context"] == user_context


@pytest.mark.asyncio
async def test_admin_anthropic_key_falls_back_to_env_without_workspace_key(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "global-anthropic-key")
    llm_client = _load_llm_client()

    async def fake_vault_secret(_scope, _key, _user_context=None):
        return None

    monkeypatch.setattr(llm_client, "_vault_secret", fake_vault_secret)

    user_context = {
        "id": 42,
        "role": "admin",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
    }

    assert await llm_client._resolve_anthropic_api_key(user_context) == "global-anthropic-key"


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


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 1
    cache_creation_input_tokens = 2


class _FakeFinalMessage:
    usage = _FakeUsage()
    content = []


class _FakeAnthropicStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    @property
    def text_stream(self):
        async def _gen():
            return
            yield  # pragma: no cover

        return _gen()

    async def get_final_message(self):
        return _FakeFinalMessage()


class _FakeAnthropicMessages:
    def stream(self, **_kwargs):
        return _FakeAnthropicStream()


class _FakeAnthropicClient:
    messages = _FakeAnthropicMessages()


@pytest.mark.asyncio
async def test_anthropic_chat_records_duration_and_surface(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key-for-test")
    llm_client = _load_llm_client()
    recorded = {}

    async def fake_record(provider, model, input_tokens, output_tokens, *args, **kwargs):
        recorded.update(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=kwargs.get("duration_ms"),
            surface=kwargs.get("surface"),
        )

    monkeypatch.setattr(llm_client, "_anthropic_client", lambda _key=None: _FakeAnthropicClient())
    monkeypatch.setattr(llm_client.token_store, "record", fake_record)

    await llm_client.chat(
        system="sys",
        messages=[],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
        surface="copilot",
    )

    assert recorded["provider"] == "anthropic"
    assert recorded["input_tokens"] == 10
    assert recorded["output_tokens"] == 5
    assert isinstance(recorded["duration_ms"], int)
    assert recorded["duration_ms"] >= 0
    assert recorded["surface"] == "copilot"


@pytest.mark.asyncio
async def test_anthropic_chat_leaves_surface_null_when_untagged(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key-for-test")
    llm_client = _load_llm_client()
    recorded = {}

    async def fake_record(*_args, **kwargs):
        recorded.update(surface=kwargs.get("surface", "missing"))

    monkeypatch.setattr(llm_client, "_anthropic_client", lambda _key=None: _FakeAnthropicClient())
    monkeypatch.setattr(llm_client.token_store, "record", fake_record)

    await llm_client.chat(
        system="sys",
        messages=[],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
    )

    assert recorded["surface"] is None


@pytest.mark.asyncio
async def test_openai_compat_chat_records_duration_and_surface(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("CHAT_LLM_MODEL", raising=False)
    llm_client = _load_llm_client()
    recorded = {}

    async def fake_record(provider, model, input_tokens, output_tokens, *args, **kwargs):
        recorded.update(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=kwargs.get("duration_ms"),
            surface=kwargs.get("surface"),
        )

    class _Message:
        content = "hola"
        tool_calls = None

    class _Choice:
        message = _Message()
        finish_reason = "stop"

    class _OaiUsage:
        prompt_tokens = 4
        completion_tokens = 3

    class _OaiResponse:
        choices = [_Choice()]
        usage = _OaiUsage()

    class _Completions:
        async def create(self, **_kwargs):
            return _OaiResponse()

    class _Chat:
        completions = _Completions()

    class _OaiClient:
        chat = _Chat()

    monkeypatch.setattr(llm_client, "_ollama_client", lambda: _OaiClient())
    monkeypatch.setattr(llm_client.token_store, "record", fake_record)

    reply, _viewer, _messages = await llm_client.chat(
        system="sys",
        messages=[{"role": "user", "content": "hola"}],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
        surface="studio",
    )

    assert reply == "hola"
    assert recorded["input_tokens"] == 4
    assert recorded["output_tokens"] == 3
    assert isinstance(recorded["duration_ms"], int)
    assert recorded["surface"] == "studio"


def test_named_callers_tag_their_llm_surface():
    repo = Path(__file__).resolve().parents[1]
    expectations = {
        "console/app/services/copilot_service.py": ('surface="copilot"', 2),
        "console/app/services/studio_assistant.py": ('surface="studio"', 1),
        "console/app/domains/data_platform/rag_requests.py": ('surface="rag"', 1),
        "workspace/app/services/llm_client.py": ('surface="workspace"', 2),
    }
    for path, (needle, minimum) in expectations.items():
        source = (repo / path).read_text(encoding="utf-8")
        assert source.count(needle) >= minimum, f"{path} must tag {needle}"
