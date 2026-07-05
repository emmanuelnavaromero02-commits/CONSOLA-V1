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


async def update_admin_user_payload(
    *,
    user_id: int,
    body: dict,
    admin_user: dict,
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    validate_password_or_400: Callable[[Any], str],
    assignable_role: Callable[[str | None, dict | None], str],
    is_global_iam_admin: Callable[[dict | None], bool],
    assert_can_manage_target_user: Callable[[dict, int], Awaitable[None]],
    set_workspace_role_for_user: Callable[[int, str, str], Awaitable[None]],
    workspace_scope_db_unavailable: Callable[[BaseException], bool],
) -> dict:
    # Don't let an admin demote / disable themselves accidentally.
    if user_id == admin_user["id"] and (
        body.get("role") not in (None, admin_user.get("role"))
        or body.get("is_active") is False
    ):
        raise HTTPException(400, "you cannot demote or disable your own account")
    await assert_can_manage_target_user(admin_user, user_id)
    before = await auth_service.get_user_by_id(user_id)
    password = None
    if "password" in body:
        password = validate_password_or_400(body.get("password"))
    password_changed = password is not None
    role_update = body.get("role")
    workspace_role = None
    platform_role_update = None
    if role_update:
        requested_role = assignable_role(role_update, admin_user)
        if is_global_iam_admin(admin_user):
            platform_role_update = requested_role
        else:
            workspace_role = requested_role
    target_user = await auth_service.update_user(
        user_id,
        name=body.get("name"),
        role=platform_role_update,
        is_active=body.get("is_active"),
        password=password,
        escalation_notify=body.get("escalation_notify")
        if "escalation_notify" in body
        else None,
    )
    if not target_user:
        raise HTTPException(404, "user not found")
    if workspace_role:
        workspace_id = str(admin_user.get("active_workspace_id") or "")
        if not workspace_id:
            raise HTTPException(400, "active workspace is required")
        try:
            await set_workspace_role_for_user(user_id, workspace_id, workspace_role)
        except (RuntimeError, AttributeError) as exc:
            if not workspace_scope_db_unavailable(exc):
                raise
        target_user["workspace_role"] = workspace_role
    action = "user.updated"
    if before and before.get("role") != target_user.get("role"):
        action = "user.role_changed"
    elif before and before.get("is_active") and not target_user.get("is_active"):
        action = "user.disabled"
    elif before and not before.get("is_active") and target_user.get("is_active"):
        action = "user.enabled"
    await audit_service.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        action,
        "user",
        str(user_id),
        ip=request_ip,
        user_agent=user_agent,
        metadata={
            "role": target_user.get("role"),
            "is_active": target_user.get("is_active"),
            "password_changed": password_changed,
        },
    )
    if password_changed:
        # Password change is independently auditable: an admin overriding a
        # user's credential is privileged enough to warrant its own row, even
        # when bundled with other field updates in the same request.
        await audit_service.record_event(
            admin_user.get("id"),
            admin_user.get("email"),
            "user.password_changed",
            "user",
            str(user_id),
            ip=request_ip,
            user_agent=user_agent,
            metadata={"target_email": target_user.get("email")},
        )
    return target_user


async def delete_admin_user_payload(
    *,
    user_id: int,
    admin_user: dict,
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    assert_can_manage_target_user: Callable[[dict, int], Awaitable[None]],
) -> dict:
    if user_id == admin_user["id"]:
        raise HTTPException(400, "you cannot delete your own account")
    await assert_can_manage_target_user(admin_user, user_id)
    try:
        ok = await auth_service.delete_user(user_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "user not found")
    await audit_service.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.deleted",
        "user",
        str(user_id),
        ip=request_ip,
        user_agent=user_agent,
    )
    return {"deleted": True, "id": user_id}


async def invite_admin_user_payload(
    *,
    body: dict,
    admin_user: dict,
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    tokens_service: Any,
    email_service: Any,
    normalize_email_or_400: Callable[[Any], str],
    assignable_role: Callable[[str | None, dict | None], str],
    assert_can_use_workspace: Callable[[dict, str | None], Awaitable[None]],
    create_vpn_config_link: Callable[[int, str], Awaitable[dict]],
    pack_vpn_conf: Callable[[str, str], tuple[bytes, str]],
    safe_filename: Callable[[str], str],
    rollback_failed_invite: Callable[[int, dict | None], Awaitable[None]],
    activation_link: Callable[[str], str],
    invite_ttl_hours: int,
    vpn_ttl_hours: int,
    logger_exception: Callable[..., None],
) -> dict:
    email = normalize_email_or_400(body.get("email"))
    existing = await auth_service.get_user_by_email(email)
    if existing:
        raise HTTPException(409, f"user with email {email} already exists")
    role = assignable_role(body.get("role"), admin_user)
    workspace_id = (
        body.get("workspace_id") or admin_user.get("active_workspace_id") or ""
    ).strip() or None
    if workspace_id:
        await assert_can_use_workspace(admin_user, workspace_id)
    else:
        raise HTTPException(400, "workspace_id is required")
    target_user = await auth_service.create_invited_user(
        email=email,
        name=body.get("name"),
        role=role,
        workspace_id=workspace_id,
    )
    token, _ = await tokens_service.create(target_user["id"], "invite")
    activation_url = activation_link(token)

    vpn_result: dict = {"issued": False}
    if body.get("with_vpn", True):
        vpn_result = await create_vpn_config_link(target_user["id"], email)

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = pack_vpn_conf(vpn_result["conf_text"], email)
        attachments.append((f"{safe_filename(email)}.zip", zip_bytes, "application/zip"))

    try:
        if vpn_result.get("issued"):
            subject, html = email_service.render_invitation_with_vpn(
                target_user.get("name"),
                email,
                activation_url,
                vpn_result["link"],
                invite_ttl_hours,
                vpn_ttl_hours,
                vpn_password,
            )
            sent = await email_service.send_email(
                email, subject, html, attachments=attachments
            )
            vpn_result["email_sent"] = sent
        else:
            subject, html = email_service.render_invitation(
                target_user.get("name"), email, activation_url, invite_ttl_hours
            )
            sent = await email_service.send_email(email, subject, html)
        if not sent:
            raise RuntimeError("invitation email send failed")
    except Exception as exc:
        await rollback_failed_invite(int(target_user["id"]), vpn_result)
        logger_exception(
            "invitation email failed; rolled back user_id=%s", target_user["id"]
        )
        raise HTTPException(500, "Invitation email delivery failed") from exc
    await audit_service.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.invited",
        "user",
        str(target_user["id"]),
        ip=request_ip,
        user_agent=user_agent,
        metadata={
            "role": role,
            "workspace_id": workspace_id,
            "email_sent": sent,
            "vpn": vpn_result,
        },
    )
    return {
        "invited": True,
        "user": target_user,
        "email_sent": sent,
        "vpn": vpn_result,
    }


async def reinvite_admin_user_payload(
    *,
    user_id: int,
    body: dict | None,
    admin_user: dict,
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    tokens_service: Any,
    email_service: Any,
    assert_can_manage_target_user: Callable[[dict, int], Awaitable[None]],
    create_vpn_config_link: Callable[[int, str], Awaitable[dict]],
    pack_vpn_conf: Callable[[str, str], tuple[bytes, str]],
    safe_filename: Callable[[str], str],
    activation_link: Callable[[str], str],
    invite_ttl_hours: int,
    vpn_ttl_hours: int,
) -> dict:
    target_user = await auth_service.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await assert_can_manage_target_user(admin_user, user_id)
    if target_user.get("is_active"):
        raise HTTPException(400, "user already active; use password reset instead")
    token, _ = await tokens_service.create(user_id, "invite")
    activation_url = activation_link(token)
    payload = body or {}
    vpn_result: dict = {"issued": False}
    if payload.get("with_vpn", True):
        vpn_result = await create_vpn_config_link(user_id, target_user["email"])

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = pack_vpn_conf(
            vpn_result["conf_text"], target_user["email"]
        )
        attachments.append(
            (f"{safe_filename(target_user['email'])}.zip", zip_bytes, "application/zip")
        )

    if vpn_result.get("issued"):
        subject, html = email_service.render_invitation_with_vpn(
            target_user.get("name"),
            target_user["email"],
            activation_url,
            vpn_result["link"],
            invite_ttl_hours,
            vpn_ttl_hours,
            vpn_password,
        )
        sent = await email_service.send_email(
            target_user["email"], subject, html, attachments=attachments
        )
        vpn_result["email_sent"] = sent
    else:
        subject, html = email_service.render_invitation(
            target_user.get("name"),
            target_user["email"],
            activation_url,
            invite_ttl_hours,
        )
        sent = await email_service.send_email(target_user["email"], subject, html)
    await audit_service.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.reinvited",
        "user",
        str(user_id),
        ip=request_ip,
        user_agent=user_agent,
        metadata={"email_sent": sent, "vpn": vpn_result},
    )
    return {"reinvited": True, "email_sent": sent, "vpn": vpn_result}
