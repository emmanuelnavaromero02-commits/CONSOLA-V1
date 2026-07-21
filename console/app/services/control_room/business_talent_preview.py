from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_eligibility import (
    BusinessEligibilityError,
    require_business_eligible,
)


ItemLoader = Callable[[str, dict], Awaitable[dict[str, Any]]]
GoldLoader = Callable[[str, dict | None, int], Awaitable[list[dict[str, Any]]]]
TemplateResolver = Callable[[dict[str, Any], str | None], dict[str, Any]]
ScopeResolver = Callable[[dict | None], tuple[str | None, str | None]]
_DATASET = "sap_successfactors_talent_action_candidates"


def _count(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _gold_item(row: Mapping[str, Any]) -> dict[str, Any]:
    action_id = str(row.get("action_id") or "").strip()
    generated_at = row.get("generated_at")
    return {
        **dict(row),
        "id": action_id,
        "kind": "anomaly",
        "item_kind": "anomaly",
        "cartridge": "sap_successfactors",
        "connector_id": "sap_successfactors",
        "source_dataset": _DATASET,
        "source_system": "sap_successfactors",
        "entity_kind": "Accion recomendada",
        "entity_id": action_id,
        "entity_label": row.get("title") or action_id,
        "anomaly_type": row.get("action_type") or "talent_action",
        "description": row.get("recommendation"),
        "detected_at": generated_at,
        "metric_type": "count",
        "evaluation_status": (
            "blocked"
            if str(row.get("status") or "").lower() == "blocked"
            else "success"
        ),
        "evidence": {
            "source_dataset": _DATASET,
            "record_id": f"source:{action_id}",
        },
    }


async def _resolve_item(
    action_id: str,
    user: dict | None,
    *,
    load_item: ItemLoader,
    load_gold_rows: GoldLoader,
) -> dict[str, Any]:
    rows = await load_gold_rows(_DATASET, user, 100)
    row = next(
        (entry for entry in rows if str(entry.get("action_id") or "") == action_id),
        None,
    )
    item = (
        _gold_item(row) if row is not None else await load_item(action_id, user or {})
    )
    try:
        require_business_eligible(item)
    except BusinessEligibilityError as exc:
        raise HTTPException(
            409,
            detail={
                "code": exc.code,
                "message": "Talent action is diagnostic-only.",
                "reason": exc.result.reason.value,
            },
        ) from None
    return item


async def build_talent_action_preview(
    user: dict | None,
    body: Mapping[str, Any] | None,
    *,
    load_item: ItemLoader,
    load_gold_rows: GoldLoader,
    resolve_template: TemplateResolver,
    resolve_scope: ScopeResolver,
) -> dict[str, Any]:
    payload = dict(body or {})
    action_id = str(payload.get("action_id") or payload.get("id") or "").strip()
    if not action_id:
        raise HTTPException(400, "action_id is required")
    item = await _resolve_item(
        action_id,
        user,
        load_item=load_item,
        load_gold_rows=load_gold_rows,
    )
    if (
        str(item.get("cartridge") or "") != "sap_successfactors"
        or str(item.get("source_dataset") or "") != _DATASET
    ):
        raise HTTPException(409, "item is not a SuccessFactors talent action")
    template_id = str(payload.get("template_id") or "").strip() or None
    template = resolve_template(item, template_id)
    tenant_id, workspace_id = resolve_scope(user)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": item.get("connection_id"),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "status": "preview_only",
        "action_id": action_id,
        "box_id": str(payload.get("box_id") or "").strip() or None,
        "title": item.get("title"),
        "severity": item.get("severity"),
        "affected_count": _count(item.get("affected_count")),
        "recommendation": item.get("recommendation") or item.get("description"),
        "method": item.get("method") or item.get("anomaly_type"),
        "template_id": template.get("template_id"),
        "template": {
            key: template.get(key)
            for key in ("template_id", "label", "action_kind", "template_type")
            if template.get(key) is not None
        },
        "recommendation_only": True,
        "write_back_enabled": False,
        "compensation_enabled": False,
        "requires_approval": True,
        "external_mutations": [],
        "steps": [
            {
                "id": "validate_scope",
                "label": "Validar C/P/A y blockers",
                "status": "required",
            },
            {
                "id": "review_roster",
                "label": "Revisar roster enmascarado",
                "status": "required",
            },
            {
                "id": "open_decision",
                "label": "Crear decision supervisada",
                "status": "preview_only",
            },
        ],
    }


__all__ = ("build_talent_action_preview",)
