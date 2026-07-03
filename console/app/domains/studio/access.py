"""Studio access helpers."""

from __future__ import annotations

from typing import Any


def cartridge_visible_for_context(ctx: dict[str, Any], cartridge_id: str) -> bool:
    allowed = {
        str(cartridge).strip()
        for cartridge in (ctx.get("allowed_cartridges") or [])
        if str(cartridge).strip()
    }
    if "*" in allowed:
        return True
    return str(cartridge_id) in allowed
