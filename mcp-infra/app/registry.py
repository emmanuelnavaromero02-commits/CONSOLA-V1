"""
Tool registry — decorators register tools; main.py exposes them via MCP endpoints.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

_tools: dict[str, dict] = {}


def tool(name: str, description: str, input_schema: dict) -> Callable:
    """Decorator that registers a function as an MCP tool."""
    def decorator(fn: Callable) -> Callable:
        _tools[name] = {
            "name":         name,
            "description":  description,
            "input_schema": input_schema,
            "fn":           fn,
        }
        return fn
    return decorator


def list_tools() -> list[dict]:
    return [
        {
            "name":         t["name"],
            "description":  t["description"],
            "input_schema": t["input_schema"],
        }
        for t in _tools.values()
    ]


async def invoke(tool_name: str, args: dict) -> Any:
    if tool_name not in _tools:
        raise ValueError(f"Unknown tool: '{tool_name}'")
    fn = _tools[tool_name]["fn"]
    if inspect.iscoroutinefunction(fn):
        return await fn(**args)
    return fn(**args)
