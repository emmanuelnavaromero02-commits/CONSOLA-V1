"""Copilot LLM key payload helpers."""

from __future__ import annotations

from typing import Any


def llm_secret_keys(data: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in data.get("keys") or []:
        if key:
            keys.add(str(key))
    for item in data.get("secrets") or []:
        if isinstance(item, dict) and item.get("key"):
            keys.add(str(item["key"]))
    return keys
