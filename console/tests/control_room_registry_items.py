from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch


def registry_installations(service: Any) -> list[dict[str, Any]]:
    return [
        {
            "cartridge_id": module.cartridge,
            "installation_status": "ready",
            "current_step": "registry",
            "label": module.label,
            "category": "platform" if module.operational else "cartridge",
        }
        for module in service.MODULES
        if module.sources
    ]


async def collect_registry_items(service: Any, user: dict, **kwargs: Any) -> dict[str, Any]:
    with patch.object(
        service, "_installed_cartridges", AsyncMock(return_value=registry_installations(service))
    ), patch.object(service, "_load_threshold_rows", AsyncMock(return_value=[])):
        return await service._collect_items(user, **kwargs)  # noqa: SLF001
