from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_execution_target import execution_target_digest
from app.services.control_room.business_template_contract import (
    template_contract_digest,
)


def runtime_action_digests(
    item: Mapping[str, Any], *, template_id: str
) -> dict[str, str]:
    template = ACTION_TEMPLATES.get(str(template_id or ""))
    if template is None:
        raise ValueError("action template is unknown")
    return {
        "execution_target_digest": execution_target_digest(item, template),
        "template_contract_digest": template_contract_digest(template),
    }


__all__ = ("runtime_action_digests",)
