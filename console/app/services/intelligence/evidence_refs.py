from __future__ import annotations

from typing import Any

from fastapi import HTTPException


FORBIDDEN_SCOPE_KEYS = {"tenant_id", "workspace_id", "security_context"}
MARKET_CONTEXT_EVIDENCE_TYPE = "market_context"


def _forbidden_path(value: Any, *, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            text_key = str(key)
            path = f"{prefix}.{text_key}" if prefix else text_key
            if text_key in FORBIDDEN_SCOPE_KEYS:
                return path
            nested = _forbidden_path(item, prefix=path)
            if nested:
                return nested
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            nested = _forbidden_path(item, prefix=f"{prefix}[{idx}]")
            if nested:
                return nested
    return None


def _short_text(value: Any, *, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(422, f"{field} is required")
    if len(text) > max_length:
        raise HTTPException(422, f"{field} is too long")
    return text


def normalize_evidence_refs(value: Any, *, max_items: int = 20) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise HTTPException(422, f"evidence_refs must be a list with at most {max_items} items")
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(422, "evidence_refs entries must be objects")
        if _forbidden_path(item):
            raise HTTPException(422, "evidence_refs cannot include scope fields")
        refs.append(
            {
                "type": _short_text(item.get("type"), field="evidence_refs.type", max_length=64),
                "id": _short_text(item.get("id"), field="evidence_refs.id", max_length=256),
            }
        )
    return refs


def merge_evidence_refs(*values: Any, max_items: int = 20) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        for ref in normalize_evidence_refs(value, max_items=max_items):
            key = (ref["type"], ref["id"])
            if key in seen:
                continue
            merged.append(ref)
            seen.add(key)
            if len(merged) > max_items:
                raise HTTPException(422, f"evidence_refs must be a list with at most {max_items} items")
    return merged


def market_context_refs(refs: Any) -> list[dict[str, str]]:
    return [
        ref
        for ref in normalize_evidence_refs(refs)
        if ref["type"] == MARKET_CONTEXT_EVIDENCE_TYPE
    ]


def external_evidence_metadata(refs: Any) -> dict[str, Any]:
    market_refs = market_context_refs(refs)
    if not market_refs:
        return {}
    return {
        "external_evidence": {
            "market_context_policy": "evidence_only",
            "market_context_ref_count": len(market_refs),
            "market_context_refs": market_refs,
        }
    }


def attach_external_evidence_metadata(
    metrics: dict[str, Any],
    refs: Any,
) -> dict[str, Any]:
    metadata = external_evidence_metadata(refs)
    if not metadata:
        return dict(metrics or {})
    clean = dict(metrics or {})
    existing = clean.get("external_evidence")
    external = existing if isinstance(existing, dict) else {}
    external.update(metadata["external_evidence"])
    clean["external_evidence"] = external
    return clean
