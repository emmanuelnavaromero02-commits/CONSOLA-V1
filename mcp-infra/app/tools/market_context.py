"""Read-only governed market context tools."""
from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.registry import tool
from app.security import get_internal_api_key


_REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500").rstrip("/")
_SAFE_FILTER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_MAX_LIMIT = 500
_PROVIDERS = {
    "banxico": {"dataset": "banxico_market_context", "id_fields": ("series_id",)},
    "inegi": {"dataset": "inegi_market_context", "id_fields": ("series_id",)},
    "sec_edgar": {"dataset": "sec_market_context", "id_fields": ("cik", "ticker")},
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _refinement_headers() -> dict[str, str]:
    key = (os.environ.get("INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT") or "").strip()
    if not key:
        if _is_production():
            raise HTTPException(500, "missing INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT")
        key = get_internal_api_key()
    return {"x-api-key": key, "x-internal-service": "mcp-infra"}


def _safe_values(value: Any, label: str) -> set[str]:
    if value is None:
        return set()
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise HTTPException(400, f"{label} must be a string or list")
    out: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or not _SAFE_FILTER_RE.fullmatch(text):
            raise HTTPException(400, f"invalid {label}")
        out.add(text)
    return out


def _selected_providers(
    provider: str | None,
    providers: list[str] | None,
    allowed_providers: list[str] | None,
) -> list[str]:
    requested = _safe_values(provider, "provider") | _safe_values(providers, "providers")
    allowed = [str(item) for item in (allowed_providers or []) if str(item) in _PROVIDERS]
    if requested:
        unknown = requested - set(_PROVIDERS)
        if unknown:
            raise HTTPException(400, "unknown market context provider")
        blocked = requested - set(allowed)
        if blocked:
            raise HTTPException(403, "market context provider not allowed")
        return [item for item in allowed if item in requested]
    return allowed


async def _query_refinement_dataset(
    dataset: str,
    security_context: dict[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    payload = {
        "tool": "query_dataset",
        "args": {"name": dataset, "limit": limit},
        "security_context": security_context,
    }
    try:
        async with httpx.AsyncClient(headers=_refinement_headers(), timeout=30) as client:
            response = await client.post(f"{_REFINEMENT_URL}/mcp/invoke", json=payload)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Refinement timed out while reading market context") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Refinement unavailable while reading market context") from exc
    if response.status_code == 404:
        return [], "not_materialized"
    if response.status_code >= 400:
        raise HTTPException(502, "Refinement rejected market context read")
    data = response.json()
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(502, "Refinement failed market context read")
    rows = data.get("data") if isinstance(data, dict) else None
    return (rows if isinstance(rows, list) else []), None


def _row_matches(row: dict[str, Any], metrics: set[str], entities: set[str], usable_only: bool) -> bool:
    if usable_only and not bool(row.get("usable")):
        return False
    if metrics and str(row.get("metric_name") or "") not in metrics:
        return False
    if entities:
        keys = {
            str(row.get("series_id") or ""),
            str(row.get("cik") or ""),
            str(row.get("ticker") or ""),
        }
        if not keys.intersection(entities):
            return False
    return True


def _scalar(value: Any) -> str | bool | None:
    if value is None or isinstance(value, bool):
        return value
    return str(value)


def _normalize(provider: str, dataset: str, row: dict[str, Any]) -> dict[str, Any]:
    identity = {
        field: str(row.get(field) or "")
        for field in _PROVIDERS[provider]["id_fields"]
        if row.get(field) is not None
    }
    return {
        "provider": provider,
        "dataset": dataset,
        "metric_name": str(row.get("metric_name") or ""),
        "as_of": _scalar(row.get("as_of")),
        "value_decimal": _scalar(row.get("value")),
        "unit": str(row.get("unit") or ""),
        "country_code": str(row.get("country_code") or ""),
        "frequency": str(row.get("frequency") or ""),
        "confidence": _scalar(row.get("confidence")),
        "freshness_status": str(row.get("freshness_status") or ""),
        "usable": bool(row.get("usable")),
        "identity": identity,
        "evidence": {
            "source_authority": str(row.get("source_authority") or ""),
            "source_host": str(row.get("source_host") or ""),
            "request_hash": str(row.get("request_hash") or ""),
            "payload_hash": str(row.get("payload_hash") or ""),
            "run_id": str(row.get("run_id") or ""),
        },
    }


@tool(
    name="market_context_read",
    description="Read governed Banxico, INEGI and SEC market context from Gold datasets only.",
    input_schema={
        "type": "object",
        "properties": {
            "provider": {"type": "string", "description": "banxico | inegi | sec_edgar"},
            "providers": {"type": "array", "items": {"type": "string"}},
            "metric_names": {"type": "array", "items": {"type": "string"}},
            "entity_ids": {"type": "array", "items": {"type": "string"}},
            "usable_only": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 100},
        },
        "required": [],
    },
)
async def market_context_read(
    provider: str | None = None,
    providers: list[str] | None = None,
    metric_names: list[str] | None = None,
    entity_ids: list[str] | None = None,
    usable_only: bool = False,
    limit: int = 100,
    security_context: dict[str, Any] | None = None,
    allowed_providers: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(security_context, dict) or not security_context.get("trusted"):
        raise HTTPException(403, "trusted security_context required")
    selected = _selected_providers(provider, providers, allowed_providers)
    if not selected:
        raise HTTPException(403, "no market context providers allowed")
    metrics = _safe_values(metric_names, "metric_names")
    entities = _safe_values(entity_ids, "entity_ids")
    safe_limit = min(max(int(limit or 100), 1), _MAX_LIMIT)

    context: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for item in selected:
        dataset = str(_PROVIDERS[item]["dataset"])
        rows, reason = await _query_refinement_dataset(dataset, security_context, safe_limit)
        if reason:
            skipped.append({"provider": item, "dataset": dataset, "reason": reason})
            continue
        for row in rows:
            if isinstance(row, dict) and _row_matches(row, metrics, entities, usable_only):
                context.append(_normalize(item, dataset, row))
            if len(context) >= safe_limit:
                break
        if len(context) >= safe_limit:
            break

    return {
        "context": context,
        "count": len(context),
        "skipped": skipped,
        "providers": selected,
    }
