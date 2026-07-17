from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def dumps_jsonb(value: Any) -> str:
    return json.dumps(json_safe(value), default=str, allow_nan=False)


__all__ = ("dumps_jsonb", "json_safe")
