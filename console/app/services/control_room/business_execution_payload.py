from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from typing import Any


NumberParser = Callable[[Any], float | None]
WritebackBuilder = Callable[[dict[str, Any]], dict[str, Any]]


def build_action_payload(
    item: Mapping[str, Any],
    template: dict[str, Any],
    *,
    number: NumberParser,
    writeback_for: WritebackBuilder,
) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    action_kind = str(template.get("action_kind") or "owner_review")
    writeback = writeback_for(template)
    base = {
        "action_kind": action_kind,
        "template_id": template.get("template_id"),
        "entity": {
            "kind": item.get("entity_kind"),
            "id": item.get("entity_id"),
            "label": item.get("entity_label"),
        },
        "source_dataset": item.get("source_dataset"),
        "severity": item.get("severity"),
        "recommendation": item.get("recommendation"),
        "writeback": writeback,
    }
    if action_kind == "billing_review":
        return {
            **base,
            "replicon": {
                "project": details.get("project_name")
                or details.get("proyecto")
                or item.get("entity_label"),
                "revenue_manager": details.get("revenue_manager"),
                "revenue_usd": number(details.get("revenue_usd")),
                "margin_pct": number(details.get("margen_bruto_pct")),
                "wip_usd": number(details.get("wip_usd")),
                "billing_gap_usd": number(details.get("billing_gap_usd")),
            },
            "prepared_actions": [
                "validar WIP y facturacion contra contrato",
                "confirmar owner financiero",
                "preparar ajuste Replicon sin ejecutarlo",
            ],
        }
    if action_kind == "replicon_adjustment":
        return {
            **base,
            "replicon": {
                "consultant": details.get("consultant_name")
                or details.get("consultor")
                or item.get("entity_label"),
                "project": details.get("project_name") or details.get("proyecto"),
                "allocation_pct": number(details.get("pct_asignacion")),
                "billable_hours": number(details.get("billable_hours")),
                "non_billable_hours": number(details.get("horas_no_facturables")),
            },
            "prepared_actions": [
                "validar asignacion/timesheet",
                "preparar ajuste para owner",
            ],
        }
    if action_kind == "investigation_note":
        return {
            **base,
            "note": {
                "summary": item.get("root_cause")
                or item.get("description")
                or item.get("title"),
                "recommendation": item.get("recommendation"),
                "evidence_sql": item.get("sql"),
            },
            "prepared_actions": [
                "registrar nota de investigacion",
                "mantener evidencia ligada al item",
            ],
        }
    if action_kind == "decision_monitoring":
        return {
            **base,
            "monitoring": {
                "metric": item.get("metric") or item.get("anomaly_type"),
                "severity": item.get("severity"),
                "entity": item.get("entity_label") or item.get("entity_id"),
                "source_dataset": item.get("source_dataset"),
            },
            "prepared_actions": [
                "marcar decision para seguimiento",
                "vincular control y leccion esperada",
            ],
        }
    if action_kind in {
        "s4_revenue_review",
        "s4_business_partner_review",
        "s4_procurement_review",
        "sap_review",
    }:
        return {
            **base,
            "sap_s4hana": {
                "business_partner": details.get("business_partner"),
                "customer": details.get("customer_code")
                or details.get("customer_name"),
                "supplier": details.get("supplier_code")
                or details.get("supplier_name"),
                "open_value": number(details.get("open_value")),
                "revenue": number(details.get("revenue")),
                "spend": number(details.get("total_spend")),
            },
            "prepared_actions": [
                "validar maestro/partida en SAP",
                "adjuntar evidencia a decision",
                "bloquear write-back hasta aprobacion",
            ],
        }
    if action_kind in {"hcm_access_review", "hcm_org_review"}:
        return {
            **base,
            "sap_hcm": {
                "pernr": details.get("pernr") or item.get("entity_id"),
                "position": details.get("position") or details.get("plans"),
                "cost_center": details.get("cost_center") or details.get("kostl"),
                "monthly_cost_usd": number(
                    details.get("monthly_cost_usd") or details.get("salary_monthly_usd")
                ),
            },
            "prepared_actions": [
                "validar baja/posicion/centro de costo",
                "preparar bloqueo o correccion para aprobacion",
            ],
        }
    if action_kind in {
        "successfactors_employee_review",
        "successfactors_recruiting_review",
    }:
        return {
            **base,
            "sap_successfactors": {
                "user_id": details.get("user_id") or item.get("entity_id"),
                "manager": details.get("manager_id") or details.get("manager"),
                "department": details.get("department"),
                "job_code": details.get("job_code"),
                "requisition": details.get("requisition_id"),
            },
            "prepared_actions": [
                "validar owner en SuccessFactors",
                "preparar correccion o excepcion auditada",
            ],
        }
    return {
        **base,
        "prepared_actions": [
            "solicitar revision de owner",
            "mantener evidencia y fecha de control",
        ],
    }


def build_execution_payload(
    item: Mapping[str, Any],
    mode: str,
    template: dict[str, Any],
    *,
    impact: Mapping[str, Any],
    action_payload: Mapping[str, Any],
    writeback: Mapping[str, Any],
    external_writeback_enabled: bool,
    supported_internal_templates: Collection[str],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "external_writeback_enabled": external_writeback_enabled,
        "supervised_execution_enabled": True,
        "execution_contract": "supervised_execution",
        "dry_run": mode != "execute_live",
        "template": template,
        "target_system": template.get("cartridge_id")
        if template.get("cartridge_id") != "platform"
        else item.get("cartridge"),
        "item": {
            "id": item.get("id"),
            "title": item.get("title"),
            "kind": item.get("kind"),
            "cartridge": item.get("cartridge"),
            "domain": item.get("domain"),
            "source_dataset": item.get("source_dataset"),
            "entity_kind": item.get("entity_kind"),
            "entity_id": item.get("entity_id"),
            "entity_label": item.get("entity_label"),
            "anomaly_type": item.get("anomaly_type"),
            "severity": item.get("severity"),
            "selected_option_id": item.get("selected_option_id"),
        },
        "impact": dict(impact),
        "action_payload": dict(action_payload),
        "writeback": dict(writeback),
        "operations": [
            {
                "operation": template.get("action_kind"),
                "status": "pending_execution"
                if mode == "execute_live"
                else "preview"
                if mode == "preview"
                else "validated",
                "requires_approval": True,
                "external_write": bool(writeback.get("external")),
                "internal_write": bool(writeback.get("supported"))
                and not bool(writeback.get("external")),
                "payload": dict(action_payload),
                "writeback": dict(writeback),
                "evidence": {
                    "sql": item.get("sql"),
                    "recommendation": item.get("recommendation"),
                    "root_cause": item.get("root_cause"),
                },
            }
        ],
        "guardrails": {
            "human_approval_required": True,
            "supervised_execution_available": True,
            "external_writeback_blocked_by_default": not external_writeback_enabled,
            "writeback_blocked_by_default": not external_writeback_enabled,
            "feature_flag": "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
            "supported_templates": sorted(supported_internal_templates),
        },
    }


__all__ = ("build_action_payload", "build_execution_payload")
