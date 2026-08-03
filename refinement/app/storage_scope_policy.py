"""Canonical tenant/workspace placement for physical lakehouse keys."""

from __future__ import annotations


_SCOPE_INDEX_BY_ROOT = {
    "raw": 3,
    "silver": 3,
    "gold": 3,
    "uploads": 2,
}


def has_exact_storage_scope(key: str, tenant: str, workspace: str) -> bool:
    """Require one adjacent scope pair at the root-specific canonical position."""
    if not tenant or not workspace:
        return False
    parts = str(key or "").strip("/").split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        return False
    scope_index = _SCOPE_INDEX_BY_ROOT.get(parts[0])
    if scope_index is None or len(parts) <= scope_index + 1:
        return False
    tenant_positions = [
        index for index, part in enumerate(parts) if part.startswith("tenant_id=")
    ]
    workspace_positions = [
        index for index, part in enumerate(parts) if part.startswith("workspace_id=")
    ]
    return (
        tenant_positions == [scope_index]
        and workspace_positions == [scope_index + 1]
        and parts[scope_index] == f"tenant_id={tenant}"
        and parts[scope_index + 1] == f"workspace_id={workspace}"
    )
