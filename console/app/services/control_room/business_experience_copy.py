from __future__ import annotations

from collections.abc import Mapping

from app.services.control_room.business_observation import semantic_maps
from app.services.control_room.business_surface_identity import BusinessSurfaceIdentity
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)

MAX_STRUCTURAL_IDENTITY_LENGTH = 240


def visible_business_copy(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
    value: object,
    *,
    max_length: int,
) -> str | None:
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=max_length,
    ).text


def first_visible_business_copy(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
    *keys: str,
    max_length: int,
) -> str | None:
    for values in semantic_maps(item):
        for key in keys:
            value = values.get(key)
            if not isinstance(value, str):
                continue
            projected = visible_business_copy(
                item,
                identity,
                value,
                max_length=max_length,
            )
            if projected is not None:
                return projected
    return None


def structural_identity_is_safe(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> bool:
    for value in (
        identity.domain,
        identity.cartridge_id,
        identity.module_id,
    ):
        if len(value) > MAX_STRUCTURAL_IDENTITY_LENGTH:
            return False
        result = classify_visible_business_copy(
            value,
            item=item,
            identity=identity,
            max_length=MAX_STRUCTURAL_IDENTITY_LENGTH,
        )
        if result.allowed and result.text != value:
            return False
        if (
            not result.allowed
            and result.cause is not VisibleCopyCause.TECHNICAL_IDENTIFIER
        ):
            return False
    return True


__all__ = (
    "MAX_STRUCTURAL_IDENTITY_LENGTH",
    "first_visible_business_copy",
    "structural_identity_is_safe",
    "visible_business_copy",
)
