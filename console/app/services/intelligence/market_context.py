from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

from app.services.intelligence.gold_fetcher import query_gold_dataset_rows


EXTERNAL_MARKET_CONTEXT_TYPE = "external_market_context"
DEFAULT_UNCERTAINTY_PCT = Decimal("0.02")
MAX_UNCERTAINTY_PCT = Decimal("0.25")
MIN_USABLE_CONFIDENCE = Decimal("0.80")

METRIC_SOURCES = {
    "usd_mxn_fix": ("banxico", "banxico_market_context"),
    "target_rate": ("banxico", "banxico_market_context"),
    "tiie_28d": ("banxico", "banxico_market_context"),
    "udi_value": ("banxico", "banxico_market_context"),
    "igae_index": ("inegi", "inegi_market_context"),
    "unemployment_rate": ("inegi", "inegi_market_context"),
    "consumer_price_index": ("inegi", "inegi_market_context"),
    "core_consumer_price_index": ("inegi", "inegi_market_context"),
}


def has_external_market_specs(payload: dict[str, Any]) -> bool:
    for spec in (payload.get("input_variables") or {}).values():
        if isinstance(spec, dict) and str(spec.get("type") or "").strip() == EXTERNAL_MARKET_CONTEXT_TYPE:
            return True
    for option in payload.get("options") or []:
        if not isinstance(option, dict):
            continue
        for spec in (option.get("input_variables") or {}).values():
            if isinstance(spec, dict) and str(spec.get("type") or "").strip() == EXTERNAL_MARKET_CONTEXT_TYPE:
                return True
    return False


async def resolve_market_context_inputs(payload: dict[str, Any], user: dict | None) -> dict[str, Any]:
    clean = dict(payload)
    if not clean.get("use_external_market_context"):
        if has_external_market_specs(clean):
            raise HTTPException(422, "external_market_context variables require use_external_market_context=true")
        return clean

    evidence: list[dict[str, str]] = []
    assumptions: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    clean["input_variables"] = await _resolve_variables(
        clean.get("input_variables") or {},
        user,
        cache,
        evidence,
        assumptions,
    )
    options = []
    for option in clean.get("options") or []:
        if not isinstance(option, dict):
            options.append(option)
            continue
        updated = dict(option)
        updated["input_variables"] = await _resolve_variables(
            option.get("input_variables") or {},
            user,
            cache,
            evidence,
            assumptions,
        )
        options.append(updated)
    if options:
        clean["options"] = options

    merged_assumptions = dict(clean.get("assumptions") or {})
    existing = merged_assumptions.get("external_market_context")
    existing_items = existing if isinstance(existing, list) else []
    merged_assumptions["external_market_context"] = [*existing_items, *assumptions]
    clean["assumptions"] = merged_assumptions
    clean["evidence_refs"] = _merge_evidence(clean.get("evidence_refs"), evidence)
    return clean


async def _resolve_variables(
    variables: dict[str, Any],
    user: dict | None,
    cache: dict[str, dict[str, Any]],
    evidence: list[dict[str, str]],
    assumptions: list[dict[str, Any]],
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for variable, spec in variables.items():
        if not isinstance(spec, dict) or str(spec.get("type") or "").strip() != EXTERNAL_MARKET_CONTEXT_TYPE:
            resolved[variable] = spec
            continue
        metric_name = _metric_name(variable, spec)
        row = cache.get(metric_name)
        if row is None:
            row = await _load_metric(metric_name, user)
            cache[metric_name] = row
        distribution = _distribution_from_row(variable, metric_name, spec, row)
        resolved[variable] = distribution
        evidence_ref, assumption = _evidence(variable, metric_name, row, distribution)
        evidence.append(evidence_ref)
        assumptions.append(assumption)
    return resolved


def _metric_name(variable: str, spec: dict[str, Any]) -> str:
    metric = str(spec.get("metric_name") or variable or "").strip()
    if metric not in METRIC_SOURCES:
        raise HTTPException(422, f"market context metric not allowlisted: {metric}")
    return metric


async def _load_metric(metric_name: str, user: dict | None) -> dict[str, Any]:
    _provider, dataset = METRIC_SOURCES[metric_name]
    rows = await query_gold_dataset_rows(dataset, user, limit=200)
    for row in rows:
        if str(row.get("metric_name") or "") != metric_name:
            continue
        if not bool(row.get("usable")):
            raise HTTPException(422, f"market context metric is not usable: {metric_name}")
        if str(row.get("freshness_status") or "") != "ready":
            raise HTTPException(422, f"market context metric is stale: {metric_name}")
        if _confidence(row.get("confidence"), metric_name) < MIN_USABLE_CONFIDENCE:
            raise HTTPException(422, f"market context metric confidence is too low: {metric_name}")
        return row
    raise HTTPException(404, f"market context metric not found: {metric_name}")


def _decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, f"{label} must be decimal") from exc
    if not parsed.is_finite():
        raise HTTPException(422, f"{label} must be finite")
    return parsed


def _confidence(value: Any, metric_name: str) -> Decimal:
    confidence = _decimal(value, f"{metric_name}.confidence")
    if confidence < 0 or confidence > 1:
        raise HTTPException(422, f"market context metric confidence is invalid: {metric_name}")
    return confidence


def _uncertainty_pct(spec: dict[str, Any]) -> Decimal:
    value = _decimal(spec.get("uncertainty_pct", DEFAULT_UNCERTAINTY_PCT), "uncertainty_pct")
    if value < 0 or value > MAX_UNCERTAINTY_PCT:
        raise HTTPException(422, "uncertainty_pct must be between 0 and 0.25")
    return value


def _distribution_from_row(
    variable: str,
    metric_name: str,
    spec: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    method = str(spec.get("distribution_method") or "point_with_uncertainty").strip()
    if method != "point_with_uncertainty":
        raise HTTPException(422, "unsupported market context distribution_method")
    value = _decimal(row.get("value"), f"{metric_name}.value")
    uncertainty = _uncertainty_pct(spec)
    delta = abs(value) * uncertainty
    low = value - delta
    high = value + delta
    if low > high:
        low, high = high, low
    return {
        "type": "triangular",
        "low": float(low),
        "mode": float(value),
        "high": float(high),
        "source": "external_market_context",
        "metric_name": metric_name,
        "variable": variable,
    }


def _evidence(
    variable: str,
    metric_name: str,
    row: dict[str, Any],
    distribution: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    provider, dataset = METRIC_SOURCES[metric_name]
    evidence_id = ":".join(
        item for item in [provider, metric_name, str(row.get("as_of") or ""), str(row.get("payload_hash") or "")[:12]] if item
    )
    assumption = {
        "variable": variable,
        "metric_name": metric_name,
        "provider": provider,
        "dataset": dataset,
        "as_of": str(row.get("as_of") or ""),
        "unit": str(row.get("unit") or ""),
        "confidence": str(row.get("confidence") or ""),
        "freshness_status": str(row.get("freshness_status") or ""),
        "distribution_method": "point_with_uncertainty",
        "bounds": {
            "low": distribution["low"],
            "mode": distribution["mode"],
            "high": distribution["high"],
        },
        "source_authority": str(row.get("source_authority") or ""),
        "source_host": str(row.get("source_host") or ""),
        "payload_hash": str(row.get("payload_hash") or ""),
        "request_hash": str(row.get("request_hash") or ""),
    }
    return {"type": "market_context", "id": evidence_id}, assumption


def _merge_evidence(existing: Any, market_refs: list[dict[str, str]]) -> list[dict[str, str]]:
    refs = [item for item in (existing or []) if isinstance(item, dict)]
    seen = {(str(item.get("type")), str(item.get("id"))) for item in refs}
    for item in market_refs:
        key = (item["type"], item["id"])
        if key not in seen:
            refs.append(item)
            seen.add(key)
        if len(refs) > 20:
            raise HTTPException(422, "evidence_refs must be a list with at most 20 items")
    return refs
