from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.admin import user_mutations


def _deps(**overrides):
    base = {name: None for name in (
        "admin_user", "request_ip", "user_agent", "auth_service", "audit_service",
    )}
    base["admin_user"] = {"id": 1, "role": "admin"}
    base.update(overrides)
    return base


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call, body",
    [
        ("create", {"email": "a@example.com", "password": "x" * 16, "is_superuser": True}),
        ("create", {"email": "a@example.com", "password": "x" * 16, "tenant_id": "other"}),
        ("update", {"role": "analyst", "password_hash": "forged"}),
        ("update", {"is_active": "maybe"}),
        ("invite", {"email": "a@example.com", "workspace_id": "w", "role_id": 1}),
    ],
)
async def test_unknown_or_mistyped_fields_are_rejected_before_any_write(call, body):
    function = {
        "create": user_mutations.create_admin_user_payload,
        "update": user_mutations.update_admin_user_payload,
        "invite": user_mutations.invite_admin_user_payload,
    }[call]
    kwargs = {"body": body, **_deps()}
    if call == "update":
        kwargs["user_id"] = 2
    with pytest.raises(HTTPException) as exc:
        await function(**{key: value for key, value in kwargs.items()}, **{
            name: None for name in function.__code__.co_varnames[: function.__code__.co_kwonlyargcount]
            if name not in kwargs
        })
    assert exc.value.status_code == 422
