from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


TemplateDecorator = Callable[[dict[str, Any]], dict[str, Any]]


def template_ids_for_business_item(item: Mapping[str, Any]) -> list[str]:
    anomaly_type = str(item.get("anomaly_type") or "")
    cartridge = str(item.get("cartridge") or "")
    module_id = str(item.get("module_id") or "")
    if cartridge == "replicon":
        if anomaly_type in {"low_margin", "wip_variance", "non_billable_ratio"}:
            return [
                "prepare_billing_review",
                "prepare_replicon_adjustment",
                "create_followup_task",
            ]
        return [
            "prepare_replicon_adjustment",
            "request_owner_review",
            "create_followup_task",
        ]
    if cartridge == "sap_s4hana":
        if (
            anomaly_type in {"negative_revenue", "aged_sales_backlog"}
            or module_id == "sap_s4hana_sales"
        ):
            return [
                "prepare_s4_revenue_review",
                "prepare_sap_review",
                "create_followup_task",
            ]
        if anomaly_type in {
            "missing_address",
            "missing_tax_id",
            "duplicate_business_partner",
        }:
            return [
                "prepare_s4_business_partner_review",
                "prepare_sap_review",
                "create_followup_task",
            ]
        if (
            anomaly_type == "supplier_spend_concentration"
            or module_id == "sap_s4hana_procurement"
        ):
            return [
                "prepare_s4_procurement_review",
                "prepare_sap_review",
                "create_followup_task",
            ]
        return ["prepare_sap_review", "request_owner_review", "create_followup_task"]
    if cartridge == "sap_hcm":
        if anomaly_type == "terminated_but_active":
            return [
                "prepare_hcm_access_review",
                "request_owner_review",
                "create_followup_task",
            ]
        return [
            "prepare_hcm_org_review",
            "request_owner_review",
            "create_followup_task",
        ]
    if cartridge == "sap_successfactors":
        if module_id == "sap_successfactors_recruiting":
            return [
                "prepare_successfactors_recruiting_review",
                "prepare_successfactors_review",
                "create_followup_task",
            ]
        return [
            "prepare_successfactors_review",
            "request_owner_review",
            "create_followup_task",
        ]
    return ["request_owner_review", "create_followup_task"]


def templates_for_business_item(
    item: Mapping[str, Any],
    *,
    templates: Mapping[str, dict[str, Any]],
    decorate: TemplateDecorator,
) -> list[dict[str, Any]]:
    return [
        decorate(templates[template_id])
        for template_id in template_ids_for_business_item(item)
        if template_id in templates
    ]


def primary_template_for_business_item(
    item: Mapping[str, Any],
    *,
    templates: Mapping[str, dict[str, Any]],
    decorate: TemplateDecorator,
) -> dict[str, Any]:
    available = templates_for_business_item(
        item,
        templates=templates,
        decorate=decorate,
    )
    return available[0] if available else dict(templates["request_owner_review"])


__all__ = (
    "primary_template_for_business_item",
    "template_ids_for_business_item",
    "templates_for_business_item",
)
