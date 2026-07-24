from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.security_context import build_security_context


def allowed_business_cartridges(
    user: Mapping[str, Any] | None,
) -> frozenset[str] | None:
    context = build_security_context(dict(user) if user is not None else None)
    allowed = frozenset(
        str(value).strip()
        for value in (context.get("allowed_cartridges") or [])
        if str(value).strip()
    )
    return None if "*" in allowed else allowed


def business_cartridge_allowed(
    user: Mapping[str, Any] | None,
    cartridge_id: str,
    *,
    allow_platform: bool = False,
) -> bool:
    cartridge = str(cartridge_id or "").strip()
    if allow_platform and cartridge == "platform":
        return True
    allowed = allowed_business_cartridges(user)
    return bool(cartridge) and (allowed is None or cartridge in allowed)


def filter_business_cartridge_items(
    items: Sequence[Mapping[str, Any]],
    user: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in items
        if business_cartridge_allowed(
            user,
            str(item.get("cartridge") or item.get("cartridge_id") or ""),
            allow_platform=True,
        )
    ]


__all__ = (
    "allowed_business_cartridges",
    "business_cartridge_allowed",
    "filter_business_cartridge_items",
)
