from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Set
from typing import Any


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _option_money(option: Mapping[str, Any]) -> str | None:
    value = _finite_number(option.get("impact_expected"))
    if value is None:
        return None
    currency = str(
        option.get("impact_currency") or option.get("currency") or ""
    ).strip()
    suffix = f" {currency} esperado" if currency else " esperado"
    return f"{value:,.0f}{suffix}"


def _option_time(option: Mapping[str, Any]) -> str | None:
    value = _finite_number(option.get("time_cost"))
    return f"{value:,.0f} puntos tiempo" if value is not None else None


def _option_risk(option: Mapping[str, Any]) -> str | None:
    value = _finite_number(option.get("risk"))
    return f"{value:,.0f}" if value is not None else None


def omega_options(
    item: Mapping[str, Any],
    impact: Mapping[str, Any],
    *,
    decision_intelligence: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    del impact
    intelligence = (
        dict(item["intelligence"])
        if isinstance(item.get("intelligence"), Mapping)
        else {}
    )
    if decision_intelligence:
        intelligence["decision_intelligence"] = dict(decision_intelligence)
    intelligence_options = intelligence.get("options")
    options: list[dict[str, Any]] = []
    if isinstance(intelligence_options, list) and intelligence_options:
        for option in intelligence_options:
            if not isinstance(option, Mapping):
                continue
            option_id = str(option.get("option_id") or option.get("id") or "").strip()
            label = str(option.get("label") or "").strip()
            action = str(option.get("action_kind") or "").strip()
            score = _finite_number(option.get("score"))
            if not option_id or not label or not action or score is None:
                continue
            options.append(
                {
                    "id": option_id,
                    "label": label,
                    "action": action,
                    "money": _option_money(option),
                    "time": _option_time(option),
                    "score": int(round(score)),
                    "risk": _option_risk(option),
                    "auto": False,
                    "recommendation": str(
                        option.get("score_explanation")
                        or item.get("recommendation")
                        or ""
                    ),
                    "selected": bool(option.get("selected")),
                }
            )
            if len(options) == 3:
                break
    persisted_selected = str(item.get("selected_option_id") or "").strip() or None
    selected = (
        persisted_selected
        if persisted_selected in {option["id"] for option in options}
        else None
    )
    if selected is None:
        selected = next(
            (option["id"] for option in options if option.get("selected")),
            None,
        )
    for option in options:
        option["selected"] = option["id"] == selected
    return options, selected, intelligence


def omega_priority(
    item: Mapping[str, Any],
    impact: Mapping[str, Any],
    *,
    lesson_count: int,
    eligible_parent_ids: Set[str] | None,
    priority_builder: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    persisted = item.get("priority")
    if not isinstance(persisted, Mapping) or persisted.get("score") is None:
        return priority_builder(
            {**item, "lesson_count": lesson_count},
            impact,
            eligible_parent_ids=eligible_parent_ids,
        )
    score = max(0, min(100, int(persisted.get("score") or 0)))
    band = persisted.get("band") or (
        "critical"
        if score >= 90
        else "high"
        if score >= 75
        else "medium"
        if score >= 55
        else "low"
    )
    raw_drivers = persisted.get("drivers")
    if isinstance(raw_drivers, Mapping):
        drivers = [
            {
                "label": str(key).replace("_", " ").title(),
                "value": value,
                "points": value,
            }
            for key, value in raw_drivers.items()
        ]
    elif isinstance(raw_drivers, list):
        drivers = raw_drivers
    else:
        drivers = []
    return {**persisted, "score": score, "band": band, "drivers": drivers}


def omega_execution(
    *,
    execution_status: str,
    primary_system: str,
    approved: bool,
    decision_id: Any,
    action_templates: list[dict[str, Any]],
    external_writeback_enabled: bool,
    supported_templates: Set[str],
) -> dict[str, Any]:
    return {
        "status": execution_status,
        "external_writeback_enabled": external_writeback_enabled,
        "supervised_execution_enabled": True,
        "execution_contract": "supervised_execution",
        "supported_writeback_templates": sorted(supported_templates),
        "templates": action_templates,
        "actions": [
            {
                "id": "preview",
                "sys": "omega",
                "act": "Generar preview de accion",
                "label": "Preview seguro",
                "done": execution_status
                in {"preview_generated", "dry_run_validated", "executed"},
                "approved": execution_status
                in {"preview_generated", "dry_run_validated", "executed"},
                "auto": True,
            },
            {
                "id": "dry_run",
                "sys": primary_system,
                "act": "Validar dry-run sin write-back",
                "label": "Dry-run seguro",
                "done": execution_status in {"dry_run_validated", "executed"},
                "approved": execution_status in {"dry_run_validated", "executed"},
                "auto": True,
            },
            {
                "id": "owner_review",
                "sys": primary_system,
                "act": "Validar owner y remediacion",
                "label": "Validar owner y remediacion",
                "done": approved,
                "approved": approved,
                "auto": False,
            },
            {
                "id": "internal_writeback",
                "sys": "omega",
                "act": "Crear seguimiento operativo supervisado",
                "label": "Ejecucion supervisada",
                "done": execution_status == "executed",
                "approved": execution_status == "executed",
                "auto": False,
            },
            {
                "id": "audit_log",
                "sys": "omega",
                "act": "Registrar bitacora y evidencia",
                "label": "Registrar bitacora y evidencia",
                "done": bool(decision_id),
                "approved": bool(decision_id),
                "auto": True,
            },
        ],
    }


__all__ = ("omega_execution", "omega_options", "omega_priority")
