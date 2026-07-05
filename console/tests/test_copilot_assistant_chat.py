from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.copilot.assistant_chat import (
    LLM_CONFIGURATION_MESSAGE,
    assistant_chat_payload,
)


class LLMConfigurationError(Exception):
    pass


class LLMProviderError(Exception):
    pass


@pytest.mark.asyncio
async def test_assistant_chat_payload_calls_assistant_with_user():
    captured = {}

    async def assistant_chat(message, history, *, user):
        captured["message"] = message
        captured["history"] = history
        captured["user"] = user
        return {"reply": "ok"}

    async def call_with_optional_user(func, message, history, *, user):
        return await func(message, history, user=user)

    result = await assistant_chat_payload(
        body={"message": "hola", "history": [{"role": "user", "content": "x"}]},
        user={"id": 7},
        assistant_chat=assistant_chat,
        call_with_optional_user=call_with_optional_user,
        llm_configuration_error=LLMConfigurationError,
        llm_provider_error=LLMProviderError,
        logger_info=lambda *_args, **_kwargs: None,
        logger_warning=lambda *_args, **_kwargs: None,
    )

    assert result == {"reply": "ok"}
    assert captured == {
        "message": "hola",
        "history": [{"role": "user", "content": "x"}],
        "user": {"id": 7},
    }


@pytest.mark.asyncio
async def test_assistant_chat_payload_returns_configuration_guidance():
    logs = []

    async def call_with_optional_user(*_args, **_kwargs):
        raise LLMConfigurationError("missing key")

    result = await assistant_chat_payload(
        body={},
        user={"id": 7},
        assistant_chat=lambda: None,
        call_with_optional_user=call_with_optional_user,
        llm_configuration_error=LLMConfigurationError,
        llm_provider_error=LLMProviderError,
        logger_info=lambda message, **kwargs: logs.append((message, kwargs)),
        logger_warning=lambda *_args, **_kwargs: None,
    )

    assert result["reply"] == LLM_CONFIGURATION_MESSAGE
    assert result["viewer_urls"] == []
    assert result["messages"] == [
        {"role": "assistant", "content": LLM_CONFIGURATION_MESSAGE}
    ]
    assert logs[0][0] == "assistant chat blocked by LLM configuration"
    assert logs[0][1]["extra"]["user_id"] == 7


@pytest.mark.asyncio
async def test_assistant_chat_payload_maps_provider_error():
    warnings = []

    async def call_with_optional_user(*_args, **_kwargs):
        raise LLMProviderError("rate limited")

    with pytest.raises(HTTPException) as exc:
        await assistant_chat_payload(
            body={},
            user={"id": 7},
            assistant_chat=lambda: None,
            call_with_optional_user=call_with_optional_user,
            llm_configuration_error=LLMConfigurationError,
            llm_provider_error=LLMProviderError,
            logger_info=lambda *_args, **_kwargs: None,
            logger_warning=lambda message, **kwargs: warnings.append((message, kwargs)),
        )

    assert exc.value.status_code == 502
    assert "rate limited" in exc.value.detail
    assert warnings[0][0] == "assistant chat provider error"
    assert warnings[0][1]["extra"]["user_id"] == 7
