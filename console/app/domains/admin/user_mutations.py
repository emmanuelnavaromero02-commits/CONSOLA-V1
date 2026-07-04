"""Admin user mutation request handlers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


async def create_admin_user_payload(
    *,
    body: dict,
    admin_user: dict,
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    normalize_email_or_400: Callable[[Any], str],
    validate_password_or_400: Callable[[Any], str],
    assignable_role: Callable[[str | None, dict | None], str],
    is_global_iam_admin: Callable[[dict | None], bool],
    assert_can_use_workspace: Callable[[dict, str | None], Awaitable[None]],
    set_workspace_role_for_user: Callable[[int, str, str], Awaitable[None]],
    workspace_scope_db_unavailable: Callable[[BaseException], bool],
) -> dict:
    email_raw = body.get("email")
    password = body.get("password") or ""
    if not email_raw or not password:
        raise HTTPException(400, "email and password are required")
    email = normalize_email_or_400(email_raw)
    password = validate_password_or_400(password)
    if await auth_service.get_user_by_email(email):
        raise HTTPException(409, f"user with email {email} already exists")
    requested_role = assignable_role(body.get("role"), admin_user)
    platform_role = requested_role if is_global_iam_admin(admin_user) else "user"
    workspace_role = (
        requested_role if not is_global_iam_admin(admin_user) else None
    )
    workspace_id = (
        body.get("workspace_id") or admin_user.get("active_workspace_id") or ""
    ).strip() or None
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")
    await assert_can_use_workspace(admin_user, workspace_id)
    try:
        create_user_kwargs = {
            "email": email,
            "password": password,
            "name": body.get("name"),
            "role": platform_role,
        }
        # Test doubles from older auth contracts may not expose workspace_id;
        # production auth.create_user does and assigns the membership in the
        # same transaction after the route has validated workspace scope.
        if "workspace_id" in inspect.signature(auth_service.create_user).parameters:
            create_user_kwargs["workspace_id"] = workspace_id
        target_user = await auth_service.create_user(**create_user_kwargs)
        if workspace_role and target_user.get("id"):
            try:
                await set_workspace_role_for_user(
                    int(target_user["id"]), workspace_id, workspace_role
                )
            except (RuntimeError, AttributeError) as exc:
                if not workspace_scope_db_unavailable(exc):
                    raise
    except RuntimeError:
        # create_user assigns workspace membership in the same transaction;
        # let the global 500 handler log + sanitize internal details.
        raise
    await audit_service.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.created",
        "user",
        str(target_user["id"]),
        ip=request_ip,
        user_agent=user_agent,
        metadata={
            "role": platform_role,
            "workspace_role": workspace_role,
            "workspace_id": workspace_id,
        },
    )
    return target_user
