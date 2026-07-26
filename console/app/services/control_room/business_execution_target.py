from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.control_room.business_action_digest import action_contract_digest


EXECUTION_TARGET_VERSION = "control-room-execution-target/v1"
_HCM_DEFAULT_PATH = "/sap/opu/odata/sap/ZHR_IT0008_SRV/BasicPaySet"
_INTERNAL_TARGETS = {
    "create_followup_task": "decision_actions",
    "create_investigation_note": "control_room_item_events",
    "mark_decision_for_monitoring": "control_room_items.metadata",
    "request_owner_review": "preview_only",
}
_PAYLOAD_ITEM_FIELDS = (
    "id",
    "kind",
    "title",
    "domain",
    "entity_kind",
    "entity_id",
    "entity_label",
    "source_dataset",
    "severity",
    "recommendation",
    "root_cause",
    "description",
    "sql",
    "metric",
    "anomaly_type",
    "cartridge",
    "threshold_state",
    "selected_option_id",
)
_STORED_IMPACT_FIELDS = ("impact_estimate", "impact_currency", "confidence")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _item_value(item: Mapping[str, Any], metadata: Mapping[str, Any], key: str) -> Any:
    value = item.get(key)
    return metadata.get(key) if value is None else value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"action execution target is missing {label}")
    return value


def _positive_number(value: Any) -> bool:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return number.is_finite() and number > 0


def _payload_item_values(
    item: Mapping[str, Any], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    values = {key: _item_value(item, metadata, key) for key in _PAYLOAD_ITEM_FIELDS}
    values.update(execution_reconciliation_context(item))
    if _positive_number(_item_value(item, metadata, "impact_estimate")):
        values.update(
            {key: _item_value(item, metadata, key) for key in _STORED_IMPACT_FIELDS}
        )
    return values


def execution_reconciliation_context(item: Mapping[str, Any]) -> dict[str, str]:
    metadata = _mapping(item.get("metadata"))
    value = _item_value(item, metadata, "threshold_state")
    return {"threshold_state": value if value in {"critical", "warning"} else "default"}


def _first_text(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _effective_locator(
    item: Mapping[str, Any], template_id: str, details: Mapping[str, Any]
) -> tuple[str, str]:
    entity_id = _required_text(item.get("entity_id"), "entity_id")
    if template_id in {"prepare_hcm_access_review", "prepare_hcm_org_review"}:
        return "pernr", _first_text(details, "pernr") or entity_id
    if template_id == "prepare_successfactors_review":
        return "user_id", _first_text(details, "user_id") or entity_id
    if template_id == "prepare_successfactors_recruiting_review":
        return "requisition_id", _required_text(
            details.get("requisition_id"), "requisition_id"
        )
    if template_id == "prepare_s4_business_partner_review":
        return "business_partner", _first_text(details, "business_partner") or entity_id
    if template_id == "prepare_s4_revenue_review":
        return "customer", _first_text(details, "customer_code") or entity_id
    if template_id == "prepare_s4_procurement_review":
        return "supplier", _first_text(details, "supplier_code") or entity_id
    return "entity_id", entity_id


def _external_target(
    template: Mapping[str, Any],
    details: Mapping[str, Any],
    connection: Mapping[str, Any],
) -> dict[str, Any]:
    template_id = str(template.get("template_id") or "")
    base_url = _first_text(connection, "base_url", "url", "sap_hcm_base_url")
    if not base_url:
        raise ValueError("action execution target is missing base_url")
    path = _first_text(details, "writeback_path", "endpoint")
    if not path and template_id == "prepare_hcm_access_review":
        path = (
            _first_text(
                connection, "it0008_endpoint", "sap_hcm_it0008_endpoint", "endpoint"
            )
            or _HCM_DEFAULT_PATH
        )
    if not path:
        path = _first_text(
            connection, "writeback_path", "default_writeback_path", "endpoint"
        )
    if not path:
        raise ValueError("action execution target is missing writeback path")
    return {
        "system": _required_text(template.get("cartridge_id"), "target system"),
        "adapter": _required_text(template.get("template_type"), "adapter"),
        "base_url_digest": action_contract_digest({"base_url": base_url}),
        "path_digest": action_contract_digest({"path": path}),
        "connection_digest": action_contract_digest(dict(connection)),
    }


def execution_target_contract(
    item: Mapping[str, Any], template: Mapping[str, Any]
) -> dict[str, Any]:
    template_id = _required_text(template.get("template_id"), "template_id")
    entity_kind = _required_text(item.get("entity_kind"), "entity_kind")
    entity_id = _required_text(item.get("entity_id"), "entity_id")
    metadata = _mapping(item.get("metadata"))
    details = {
        **_mapping(metadata.get("details")),
        **_mapping(item.get("details")),
    }
    connection = _mapping(metadata.get("connection"))
    locator_kind, locator_value = _effective_locator(item, template_id, details)
    if str(template.get("cartridge_id") or "") == "platform":
        internal_target = _INTERNAL_TARGETS.get(template_id)
        if not internal_target:
            raise ValueError("action execution target is not approved")
        target = {
            "system": "omega_control_room",
            "adapter": _required_text(template.get("template_type"), "adapter"),
            "resource": internal_target,
        }
    else:
        target = _external_target(template, details, connection)
    payload_inputs = {
        "item": _payload_item_values(item, metadata),
        "details": details,
    }
    return {
        "version": EXECUTION_TARGET_VERSION,
        "entity": {"kind": entity_kind, "id": entity_id},
        "locator": {"kind": locator_kind, "value": locator_value},
        "target": target,
        "payload_inputs_digest": action_contract_digest(payload_inputs),
    }


def execution_target_digest(
    item: Mapping[str, Any], template: Mapping[str, Any]
) -> str:
    return action_contract_digest(execution_target_contract(item, template))


__all__ = (
    "EXECUTION_TARGET_VERSION",
    "execution_reconciliation_context",
    "execution_target_contract",
    "execution_target_digest",
)
