from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from typing import Any


def _default_options(
    item: Mapping[str, Any], impact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    impact_money = (
        f"${impact['estimate']:,.0f} {impact['currency']} en revision"
        if impact.get("status") == "ok" and impact.get("estimate") is not None
        else "Impacto no calculable"
    )
    return [
        {
            "id": "remediate",
            "label": "Remediar dato/proceso",
            "action": "Remediar dato/proceso",
            "money": impact_money,
            "time": "1-2 ciclos",
            "score": 92,
            "risk": "Bajo",
            "auto": True,
            "recommendation": item.get("recommendation"),
            "selected": False,
        },
        {
            "id": "exception",
            "label": "Aprobar excepcion temporal",
            "action": "Aprobar excepcion temporal",
            "money": "Costo medio",
            "time": "Mismo dia",
            "score": 68,
            "risk": "Medio",
            "auto": False,
            "recommendation": "Usar solo con responsable y fecha de control.",
            "selected": False,
        },
        {
            "id": "monitor",
            "label": "Monitorear sin cambio inmediato",
            "action": "Monitorear sin cambio inmediato",
            "money": "Sin gasto inmediato",
            "time": "Siguiente refresh",
            "score": 45,
            "risk": "Alto",
            "auto": False,
            "recommendation": "No recomendado para severidad alta o critica.",
            "selected": False,
        },
    ]


def omega_options(
    item: Mapping[str, Any],
    impact: Mapping[str, Any],
    *,
    decision_intelligence: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    options = _default_options(item, impact)
    intelligence = (
        dict(item["intelligence"])
        if isinstance(item.get("intelligence"), Mapping)
        else {}
    )
    if decision_intelligence:
        intelligence["decision_intelligence"] = dict(decision_intelligence)
    intelligence_options = intelligence.get("options")
    if isinstance(intelligence_options, list) and intelligence_options:
        projected = [
            {
                "id": str(
                    option.get("option_id") or option.get("id") or f"option_{index + 1}"
                ),
                "label": str(option.get("label") or "Opcion supervisada"),
                "action": str(
                    option.get("action_kind")
                    or option.get("label")
                    or "accion_supervisada"
                ),
                "money": (
                    f"${float(option.get('impact_expected') or 0):,.0f} USD esperado"
                ),
                "time": f"{float(option.get('time_cost') or 0):,.0f} puntos tiempo",
                "score": int(round(float(option.get("score") or 0))),
                "risk": f"{float(option.get('risk') or 0):,.0f}",
                "auto": False,
                "recommendation": str(
                    option.get("score_explanation") or item.get("recommendation") or ""
                ),
                "selected": bool(option.get("selected")),
            }
            for index, option in enumerate(intelligence_options[:3])
            if isinstance(option, Mapping)
        ]
        options = projected or options
    selected = str(item.get("selected_option_id") or "remediate")
    if selected not in {option["id"] for option in options}:
        selected = options[0]["id"] if options else "remediate"
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
