"""Shared policy helpers for AI-driven tool execution.

Copilot and Agents both receive tool calls from an LLM. This module keeps the
server-side rules in one place: classify risk, map risk to permission, scrub
arguments, validate model-supplied input, and detect obvious prompt-injection
attempts before a tool can run.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.services import permissions, tool_manifest


RISK_PERMISSION = {
    "read": "copilot.use",
    "write": "copilot.write",
    "destructive": "copilot.execute",
}

SECRET_KEYS = frozenset({
    "password", "passwd", "pass", "token", "secret", "api_key",
    "apikey", "api-key", "client_secret", "private_key",
    "auth_token", "bearer", "x-api-key", "internal_api_key",
})

BACKEND_CONTEXT_KEYS = frozenset({
    "security_context", "user_context", "_trusted_admin",
    "_server_trusted_context",
})

MAX_TOOL_ARGS_BYTES = 16_000
MAX_STRING_VALUE_CHARS = 8_000
MAX_ARRAY_ITEMS = 500
MAX_OBJECT_KEYS = 200
MAX_NESTING_DEPTH = 8

_PROMPT_INJECTION_RE = re.compile(
    r"(ignore|ignora|bypass|salt[aá]te|override|sobrescribe).{0,80}"
    r"(system|developer|approval|aprobaci[oó]n|policy|pol[ií]tica|permisos?)"
    r"|exfiltrat|filtra(?:r)?\s+secret|revela(?:r)?\s+(?:secret|token|password)",
    re.IGNORECASE | re.DOTALL,
)


class ToolPolicyError(ValueError):
    """Raised when a tool call violates shared AI execution policy."""


def classify(tool_name: str) -> dict[str, Any]:
    meta = tool_manifest.classify_tool(tool_name)
    risk = str(meta.get("risk_level") or "write")
    if risk not in RISK_PERMISSION:
        risk = "destructive"
    return {
        **meta,
        "risk_level": risk,
        "requires_approval": bool(meta.get("requires_approval")),
    }


def required_permission(risk_level: str) -> str:
    return RISK_PERMISSION.get(risk_level, RISK_PERMISSION["destructive"])


def has_permission(user: dict | None, risk_level: str) -> bool:
    return permissions.has_permission(user, required_permission(risk_level))


def scrub_args(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS or any(s in str(key).lower() for s in SECRET_KEYS):
                out[str(key)] = "***"
            else:
                out[str(key)] = scrub_args(item)
        return out
    if isinstance(value, list):
        return [scrub_args(item) for item in value]
    return value


def clip_args(value: Any, *, max_bytes: int = MAX_TOOL_ARGS_BYTES) -> Any:
    try:
        raw = json.dumps(value, default=str)
    except Exception:
        raw = str(value)
    if len(raw) <= max_bytes:
        return value
    return {
        "_truncated": True,
        "reason": f"tool args exceeded {max_bytes} bytes",
        "preview": raw[:1000],
    }


def validate_tool_args(
    tool_name: str,
    args: Any,
    input_schema: dict[str, Any] | None = None,
    *,
    risk_level: str = "write",
) -> dict[str, Any]:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolPolicyError("tool args must be a JSON object")
    _validate_json_value(args, depth=0)
    _reject_backend_context(args)
    if risk_level != "read":
        _reject_prompt_injection(args)
    _validate_schema(tool_name, args, input_schema or {})
    return args


def _validate_json_value(value: Any, *, depth: int) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise ToolPolicyError("tool args are too deeply nested")
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            raise ToolPolicyError("tool args object has too many keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ToolPolicyError("tool args object keys must be strings")
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ToolPolicyError("tool args array has too many items")
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, str) and len(value) > MAX_STRING_VALUE_CHARS:
        raise ToolPolicyError("tool args string value is too large")
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise ToolPolicyError("tool args must be JSON-serializable primitives")


def _reject_backend_context(args: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) in BACKEND_CONTEXT_KEYS:
                    raise ToolPolicyError(f"backend-owned arg is not allowed: {key}")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(args)


def _reject_prompt_injection(args: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, str) and _PROMPT_INJECTION_RE.search(value):
            raise ToolPolicyError("tool args contain prompt-injection language")
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(args)


def _validate_schema(tool_name: str, args: dict[str, Any], schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        return
    required = schema.get("required") or []
    if isinstance(required, list):
        missing = [str(key) for key in required if str(key) not in args]
        if missing:
            raise ToolPolicyError(f"{tool_name} missing required args: {', '.join(missing)}")
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return
    for key, prop_schema in properties.items():
        if key not in args or not isinstance(prop_schema, dict):
            continue
        _validate_schema_value(tool_name, key, args[key], prop_schema)


def _validate_schema_value(tool_name: str, key: str, value: Any, schema: dict[str, Any]) -> None:
    expected = schema.get("type")
    if isinstance(expected, list):
        expected_types = {str(t) for t in expected}
    elif expected:
        expected_types = {str(expected)}
    else:
        expected_types = set()
    if not expected_types:
        return
    if value is None and "null" in expected_types:
        return
    ok = (
        ("object" in expected_types and isinstance(value, dict))
        or ("array" in expected_types and isinstance(value, list))
        or ("string" in expected_types and isinstance(value, str))
        or ("integer" in expected_types and isinstance(value, int) and not isinstance(value, bool))
        or ("number" in expected_types and isinstance(value, (int, float)) and not isinstance(value, bool))
        or ("boolean" in expected_types and isinstance(value, bool))
    )
    if not ok:
        raise ToolPolicyError(f"{tool_name}.{key} has invalid type")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ToolPolicyError(f"{tool_name}.{key} is not an allowed value")
