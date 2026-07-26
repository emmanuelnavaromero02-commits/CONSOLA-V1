from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_digest import action_contract_digest


TEMPLATE_CONTRACT_VERSION = "control-room-template-contract/v1"
_TEXT_FIELDS = (
    "template_id",
    "cartridge_id",
    "action_kind",
    "template_type",
    "risk_level",
    "mode_default",
    "surface",
    "capability",
    "writeback_contract",
)


def template_contract(template: Mapping[str, Any]) -> dict[str, Any]:
    values = {field: str(template.get(field) or "").strip() for field in _TEXT_FIELDS}
    if not all(values.values()):
        raise ValueError("action template contract is incomplete")
    writeback_version = template.get("writeback_version")
    if not isinstance(writeback_version, int) or isinstance(writeback_version, bool):
        raise ValueError("action template write-back version is invalid")
    requires_approval = template.get("requires_approval")
    if not isinstance(requires_approval, bool):
        raise ValueError("action template approval policy is invalid")
    return {
        "version": TEMPLATE_CONTRACT_VERSION,
        **values,
        "requires_approval": requires_approval,
        "writeback_version": writeback_version,
    }


def template_contract_digest(template: Mapping[str, Any]) -> str:
    return action_contract_digest(template_contract(template))


__all__ = (
    "TEMPLATE_CONTRACT_VERSION",
    "template_contract",
    "template_contract_digest",
)
