from __future__ import annotations

from app.schemas.control_room_experience_actions import (
    ExperienceAction,
    ExperienceActionKind,
)
from app.services.control_room.business_action_authority_policy import (
    APPROVE_EXCEPTION_TEMPLATE_ID,
    DECISION_PROPOSAL_TEMPLATE_ID,
    EXECUTABLE_TEMPLATE_ID,
    OPEN_IN_STUDIO_TEMPLATE_ID,
    REOPEN_EXCEPTION_TEMPLATE_ID,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES


PUBLIC_ACTION_KINDS: dict[str, ExperienceActionKind] = {
    EXECUTABLE_TEMPLATE_ID: "followup_task",
    APPROVE_EXCEPTION_TEMPLATE_ID: "exception_approval",
    OPEN_IN_STUDIO_TEMPLATE_ID: "studio_adjustment",
    DECISION_PROPOSAL_TEMPLATE_ID: "decision_proposal",
    REOPEN_EXCEPTION_TEMPLATE_ID: "exception_reopen",
}


def public_action(
    action_handle: str, template_id: str = EXECUTABLE_TEMPLATE_ID
) -> ExperienceAction:
    template = ACTION_TEMPLATES[template_id]
    return ExperienceAction(
        action_handle=action_handle,
        kind=PUBLIC_ACTION_KINDS[template_id],
        label=str(template["label"]),
        enabled=True,
        requires_approval=bool(template["requires_approval"]),
    )


__all__ = ("PUBLIC_ACTION_KINDS", "public_action")
