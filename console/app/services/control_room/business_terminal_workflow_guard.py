from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException


TERMINAL_WORKFLOW_STATUSES = frozenset({"approved", "dismissed", "resolved"})


def require_nonterminal_workflow(item: Mapping[str, Any], *, operation: str) -> None:
    status = str(item.get("status") or "open").strip().lower()
    if status not in TERMINAL_WORKFLOW_STATUSES:
        return
    raise HTTPException(
        409,
        {
            "code": "terminal_item",
            "message": f"terminal control room item cannot {operation}",
            "status": status,
        },
    )


__all__ = ("TERMINAL_WORKFLOW_STATUSES", "require_nonterminal_workflow")
