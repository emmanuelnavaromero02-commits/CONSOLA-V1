from __future__ import annotations

from typing import Any

from fastapi import HTTPException


LLM_CONFIGURATION_MESSAGE = (
    "⚠️ El copiloto necesita una clave Anthropic para este workspace. "
    "Ábrela en Tokens y guarda la clave API del tenant antes de usar el chat."
)


async def assistant_chat_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    assistant_chat: Any,
    call_with_optional_user: Any,
    llm_configuration_error: type[BaseException],
    llm_provider_error: type[BaseException],
    logger_info: Any,
    logger_warning: Any,
) -> Any:
    try:
        return await call_with_optional_user(
            assistant_chat,
            body.get("message", ""),
            body.get("history", []),
            user=user,
        )
    except llm_configuration_error as exc:
        logger_info(
            "assistant chat blocked by LLM configuration",
            extra={"user_id": user.get("id"), "reason": str(exc)},
        )
        return {
            "reply": LLM_CONFIGURATION_MESSAGE,
            "viewer_urls": [],
            "messages": [
                {"role": "assistant", "content": LLM_CONFIGURATION_MESSAGE}
            ],
        }
    except llm_provider_error as exc:
        logger_warning(
            "assistant chat provider error",
            extra={"user_id": user.get("id"), "reason": str(exc)},
        )
        raise HTTPException(
            status_code=502, detail=f"El proveedor LLM respondió con error. {exc}"
        ) from exc
