from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_VERIFIED_ATTR = "_surface_workflow_provenance_verified"


def set_surface_workflow_provenance(
    item: Mapping[str, Any],
    *,
    verified: bool,
) -> None:
    try:
        setattr(item, _VERIFIED_ATTR, verified is True)
    except AttributeError:
        return


def surface_workflow_provenance_verified(item: Mapping[str, Any]) -> bool:
    return getattr(item, _VERIFIED_ATTR, False) is True


__all__ = (
    "set_surface_workflow_provenance",
    "surface_workflow_provenance_verified",
)
