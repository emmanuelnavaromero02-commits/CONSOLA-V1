from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException


DatasetFetcher = Callable[[str, dict | None, int], Awaitable[list[dict[str, Any]]]]

DEFAULT_LIMIT = 5000
CONTRACT_PATH = Path("app/config/intelligence.yaml")
SIGNAL_KIND = "intelligence_signal"
TERMINAL_SIGNAL_STATUSES = {"approved", "dismissed", "resolved"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, default=json_default)


def public_json(value: Any) -> Any:
    return json.loads(json_dumps(value))


def workspace_scope(user: dict | None) -> tuple[str | None, str]:
    workspace_id = (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id")
    tenant_id = (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id")
    if not workspace_id:
        raise HTTPException(400, "active workspace is required")
    return (str(tenant_id) if tenant_id else None), str(workspace_id)


def allowed_cartridges(user: dict | None) -> set[str] | None:
    if not user:
        return None
    values = user.get("allowed_cartridges") or user.get("active_cartridges")
    if not isinstance(values, list):
        return None
    cleaned = {str(value).strip() for value in values if str(value or "").strip()}
    if not cleaned or "*" in cleaned:
        return None
    return cleaned


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        parsed = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def time_key(value: Any) -> tuple[int, str]:
    if value is None:
        return (0, "")
    if isinstance(value, datetime):
        return (2, value.isoformat())
    if isinstance(value, date):
        return (2, value.isoformat())
    text = str(value).strip()
    if not text:
        return (0, "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return (2, parsed.isoformat())
    except ValueError:
        return (1, text)


def period_key(row: dict[str, Any], time_field: str) -> str:
    value = row.get(time_field)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def field_or_literal(row: dict[str, Any], field: str | None, fallback: str) -> str:
    if not field or field == "__all__":
        return fallback
    value = row.get(field)
    if value is not None and str(value).strip():
        return str(value)
    return field if field and field not in row else fallback


def stable_id(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=json_default).encode("utf-8")
    return "intel:" + hashlib.sha256(raw).hexdigest()[:24]


def sample_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=json_default).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def severity(abs_pct: float, rules: dict[str, Any]) -> str:
    warning = float(rules.get("warning_pct") or 0.20)
    critical = float(rules.get("critical_pct") or 0.45)
    if abs_pct >= critical:
        return "critical"
    if abs_pct >= max(warning * 1.5, warning + 0.10):
        return "high"
    if abs_pct >= warning:
        return "medium"
    return "low"


def signal_type(expected_behavior: str, actual: float, expected: float) -> str:
    if actual == expected:
        return "watch"
    higher_is_good = expected_behavior == "higher_is_good"
    lower_is_good = expected_behavior == "lower_is_good"
    if higher_is_good:
        return "opportunity" if actual > expected else "risk"
    if lower_is_good:
        return "risk" if actual > expected else "opportunity"
    return "watch"


def confidence(sample_count: int, abs_pct: float) -> float:
    base = 0.45 + min(0.25, math.sqrt(max(sample_count, 1)) * 0.08)
    return round(min(0.95, base + min(0.20, abs_pct / 2)), 2)


def prediction_confidence(observed_confidence: float, sample_count: int) -> float:
    history_cap = min(0.72, 0.42 + sample_count * 0.04)
    return round(max(0.25, min(history_cap, observed_confidence - 0.10)), 2)


def coerce_json_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}
