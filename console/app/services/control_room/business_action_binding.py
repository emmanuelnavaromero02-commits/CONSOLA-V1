from __future__ import annotations

import re
import unicodedata

from app.services.control_room.business_action_registry import ACTION_TEMPLATES


_ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{1,240}$")
_TEMPLATE_ID = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
_PREVIEW_PREFIX = "/api/control-room/items/"
_PREVIEW_SUFFIX = "/action-preview"


def valid_action_item_id(value: object) -> bool:
    return isinstance(value, str) and _ITEM_ID.fullmatch(value) is not None


def valid_action_template_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _TEMPLATE_ID.fullmatch(value) is not None
        and value in ACTION_TEMPLATES
    )


def valid_action_template_binding(
    template_id: str,
    *,
    label: str,
    requires_approval: bool,
) -> bool:
    template = ACTION_TEMPLATES.get(template_id)
    return bool(
        template
        and label == template.get("label")
        and requires_approval is bool(template.get("requires_approval", True))
    )


def preview_action_endpoint(item_id: str) -> str:
    if not valid_action_item_id(item_id):
        raise ValueError("invalid action item id")
    return f"{_PREVIEW_PREFIX}{item_id}{_PREVIEW_SUFFIX}"


def valid_preview_action_endpoint(endpoint: object, *, item_id: str) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 320:
        return False
    try:
        expected = preview_action_endpoint(item_id)
    except ValueError:
        return False
    return endpoint == expected


def normalize_action_idempotency_key(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("idempotency_key must be a string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("idempotency_key contains control characters")
    normalized = value.strip()
    if not normalized:
        raise ValueError("idempotency_key must not be empty")
    if len(normalized) > 128:
        raise ValueError("idempotency_key exceeds 128 characters")
    return normalized


__all__ = (
    "normalize_action_idempotency_key",
    "preview_action_endpoint",
    "valid_action_item_id",
    "valid_action_template_binding",
    "valid_action_template_id",
    "valid_preview_action_endpoint",
)
