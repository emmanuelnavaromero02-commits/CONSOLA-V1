from __future__ import annotations

import inspect
from typing import Any, Callable


def runtime_user(user: Any) -> dict[str, Any] | None:
    if isinstance(user, dict):
        return user
    if user is None:
        return None
    return {"role": "owner", "allowed_cartridges": ["*"]}


async def call_with_optional_user(fn: Callable[..., Any], *args: Any, user=None):
    user_arg = runtime_user(user)
    try:
        params = inspect.signature(fn).parameters
        accepts_user = "user" in params or any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
        )
    except (TypeError, ValueError):
        accepts_user = False
    result = fn(*args, user=user_arg) if accepts_user else fn(*args)
    if inspect.isawaitable(result):
        return await result
    return result
