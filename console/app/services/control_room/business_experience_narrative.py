from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.schemas.control_room_experience_actions import ExperienceNarrative
from app.services.control_room.business_experience_copy import visible_business_copy
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
)
from app.services.intelligence.narrative_copy import (
    CONFIDENCE_ORDER,
    GENERIC_LIMITATION_NOTE,
    template_explanation,
)

MAX_PUBLIC_LIMITATIONS = 4


def project_experience_narrative(
    narrative: Mapping[str, Any] | None,
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> ExperienceNarrative | None:
    if not isinstance(narrative, Mapping):
        return None

    def visible(value: Any, max_length: int) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return visible_business_copy(item, identity, value, max_length=max_length)

    status = "ready" if narrative.get("status") == "ready" else "template"
    explanation = visible(narrative.get("explanation"), 600)
    if explanation is None:
        status = "template"
        explanation = visible(template_explanation(identity.domain), 600)
    recommendation = visible(narrative.get("recommendation"), 600)
    basis_note = visible(narrative.get("basis_note"), 240)
    confidence = narrative.get("confidence_label")
    if (
        explanation is None
        or recommendation is None
        or basis_note is None
        or confidence not in CONFIDENCE_ORDER
    ):
        return None

    generic = visible(GENERIC_LIMITATION_NOTE, 240)
    limitations: list[str] = []
    raw_limitations = narrative.get("limitations")
    for phrase in raw_limitations if isinstance(raw_limitations, list) else []:
        projected = visible(phrase, 240) or generic
        if projected and projected not in limitations:
            limitations.append(projected)
    if len(limitations) > MAX_PUBLIC_LIMITATIONS and generic:
        limitations = [*limitations[: MAX_PUBLIC_LIMITATIONS - 1], generic]

    return ExperienceNarrative(
        status=status,
        explanation=explanation,
        recommendation=recommendation,
        confidence_label=confidence,
        confidence_reason=visible(narrative.get("confidence_reason"), 600),
        basis_note=basis_note,
        evidence_note=visible(narrative.get("evidence_note"), 240),
        limitations=limitations[:MAX_PUBLIC_LIMITATIONS],
    )


__all__ = ("MAX_PUBLIC_LIMITATIONS", "project_experience_narrative")
