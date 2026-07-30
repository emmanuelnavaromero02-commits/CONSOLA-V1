from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_authorization_snapshot import (
    AuthorizationSnapshot,
)


def token_authorization_matches(
    token: Mapping[str, Any], authorization: AuthorizationSnapshot
) -> bool:
    return all(
        str(token.get(key) or "") == expected
        for key, expected in (
            ("access_revision_digest", authorization.access_revision_digest),
            ("rbac_policy_digest", authorization.rbac_policy_digest),
        )
    )


__all__ = ("token_authorization_matches",)
