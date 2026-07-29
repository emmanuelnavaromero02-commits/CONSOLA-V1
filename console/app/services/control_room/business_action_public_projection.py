from __future__ import annotations

from app.schemas.control_room_experience_actions import ExperienceAction
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES


def public_action(action_handle: str) -> ExperienceAction:
    template = ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID]
    return ExperienceAction(
        action_handle=action_handle,
        label=str(template["label"]),
        enabled=True,
        requires_approval=True,
    )


__all__ = ("public_action",)
