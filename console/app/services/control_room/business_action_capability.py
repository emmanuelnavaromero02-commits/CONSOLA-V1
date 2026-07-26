from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CAPABILITY_TEMPLATES: dict[str, frozenset[str]] = {
    "business_workflow": frozenset(
        {
            "create_followup_task",
            "create_investigation_note",
            "mark_decision_for_monitoring",
            "request_owner_review",
        }
    ),
    "replicon_writeback": frozenset(
        {"prepare_billing_review", "prepare_replicon_adjustment"}
    ),
    "sap_hcm_writeback": frozenset(
        {"prepare_hcm_access_review", "prepare_hcm_org_review"}
    ),
    "sap_s4hana_writeback": frozenset(
        {
            "prepare_sap_review",
            "prepare_s4_revenue_review",
            "prepare_s4_business_partner_review",
            "prepare_s4_procurement_review",
        }
    ),
    "sap_successfactors_writeback": frozenset(
        {"prepare_successfactors_review", "prepare_successfactors_recruiting_review"}
    ),
}

CARTRIDGE_CAPABILITIES: dict[str, frozenset[str]] = {
    "platform": frozenset({"business_workflow"}),
    "replicon": frozenset({"business_workflow", "replicon_writeback"}),
    "sap_hcm": frozenset({"business_workflow", "sap_hcm_writeback"}),
    "sap_s4hana": frozenset({"business_workflow", "sap_s4hana_writeback"}),
    "sap_successfactors": frozenset(
        {"business_workflow", "sap_successfactors_writeback"}
    ),
}


def business_experience_template_allowed(
    *, source_cartridge: str, template: Mapping[str, Any]
) -> bool:
    if str(template.get("surface") or "") != "business_experience":
        return False
    capability = str(template.get("capability") or "")
    template_id = str(template.get("template_id") or "")
    allowed_capabilities = CARTRIDGE_CAPABILITIES.get(source_cartridge, frozenset())
    return bool(
        capability in allowed_capabilities
        and template_id in CAPABILITY_TEMPLATES.get(capability, frozenset())
    )


__all__ = (
    "CAPABILITY_TEMPLATES",
    "CARTRIDGE_CAPABILITIES",
    "business_experience_template_allowed",
)
