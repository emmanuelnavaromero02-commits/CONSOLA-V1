from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException

from app.services.control_room.business_access import (
    actor_id as optional_actor_id,
    can_read_workspace_wide,
)
from app.services.control_room.business_action_authority_policy import (
    actor_id,
    require_distinct_actors,
)
from app.services.permissions import has_permission


SYSTEM_MAKER = "system:control-room"
CHECKER_PERMISSIONS = ("control_room.approve", "control_room.execute")


@dataclass(frozen=True)
class CouncilMaker:
    origin: Literal["system", "person"]
    user_id: int | None = None

    @classmethod
    def system(cls) -> CouncilMaker:
        return cls("system", None)

    @classmethod
    def person(cls, user_id: object) -> CouncilMaker:
        resolved = optional_actor_id(user_id)
        if resolved is None:
            raise HTTPException(403, "action authority is unavailable")
        return cls("person", resolved)

    @property
    def label(self) -> str:
        if self.origin == "system":
            return SYSTEM_MAKER
        return f"user:{self.user_id}"


def require_council_distinct_actors(maker: CouncilMaker, checker_user_id: int) -> None:
    checker = optional_actor_id(checker_user_id)
    if checker is None:
        raise HTTPException(403, "action authority is unavailable")
    if maker.origin == "system":
        if maker.user_id is not None:
            raise HTTPException(403, "action authority is unavailable")
        return
    if maker.origin != "person" or maker.user_id is None:
        raise HTTPException(403, "action authority is unavailable")
    require_distinct_actors(maker.user_id, checker)


def is_council_checker(user: Mapping[str, Any]) -> bool:
    return all(has_permission(dict(user), key) for key in CHECKER_PERMISSIONS)


def require_council_checker(user: Mapping[str, Any]) -> int:
    checker = actor_id(user)
    if not is_council_checker(user):
        raise HTTPException(403, "action authority is unavailable")
    return checker


def council_reads_workspace(user: Mapping[str, Any]) -> bool:
    return can_read_workspace_wide(user) or has_permission(
        dict(user), "control_room.approve"
    )


__all__ = (
    "CHECKER_PERMISSIONS",
    "SYSTEM_MAKER",
    "CouncilMaker",
    "council_reads_workspace",
    "is_council_checker",
    "require_council_checker",
    "require_council_distinct_actors",
)
