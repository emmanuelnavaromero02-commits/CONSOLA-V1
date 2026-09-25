from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException


def temporary_admin_reset_password() -> str:
    return f"{secrets.token_urlsafe(24)}Aa1!"


async def load_active_reset_target_user(user_id: int, auth_service: Any) -> dict:
    target_user = await auth_service.get_user_by_id(user_id)
    if not target_user or not target_user.get("is_active"):
        raise HTTPException(404, "user not found or inactive")
    return target_user


async def replace_user_password_for_reset(
    user_id: int,
    temporary_password: str,
    auth_service: Any,
) -> None:
    pool = await auth_service.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE users
                   SET password_hash = $1,
                       must_change_password = TRUE,
                       is_active = TRUE
                 WHERE id = $2
                   AND is_active = TRUE
                 RETURNING id
                """,
                auth_service.hash_password(temporary_password),
                user_id,
            )
            if not updated:
                raise HTTPException(404, "user not found or inactive")
            await conn.fetchval("SELECT omega_auth_revoke_user_tokens($1)", user_id)


async def send_admin_reset_email(
    target_user: dict,
    token: str,
    email_service: Any,
    reset_link: str,
    reset_ttl_hours: int,
) -> bool:
    subject, html = email_service.render_password_reset(
        target_user.get("name"), reset_link, reset_ttl_hours
    )
    return await email_service.send_email(target_user["email"], subject, html)


async def audit_admin_password_reset(
    *,
    admin: dict,
    user_id: int,
    request_ip: str | None,
    user_agent: str | None,
    sent: bool,
    audit_service: Any,
) -> None:
    await audit_service.record_event(
        admin.get("id"),
        admin.get("email"),
        "password_reset.sent",
        "user",
        str(user_id),
        ip=request_ip,
        user_agent=user_agent,
        metadata={
            "email_sent": sent,
            "temporary_password_issued": True,
            "password_delivery": "one_time_response",
            "sessions_revoked": True,
        },
    )


async def admin_send_reset_payload(
    *,
    user_id: int,
    admin: dict,
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    tokens_service: Any,
    email_service: Any,
    reset_link_for_token,
    reset_ttl_hours: int,
    assert_can_manage_target_user,
) -> dict:
    target_user = await load_active_reset_target_user(user_id, auth_service)
    await assert_can_manage_target_user(admin, user_id)
    temporary_password = temporary_admin_reset_password()
    await replace_user_password_for_reset(user_id, temporary_password, auth_service)
    token, _ = await tokens_service.create(user_id, "reset")
    sent = await send_admin_reset_email(
        target_user,
        token,
        email_service,
        reset_link_for_token(token),
        reset_ttl_hours,
    )
    await audit_admin_password_reset(
        admin=admin,
        user_id=user_id,
        request_ip=request_ip,
        user_agent=user_agent,
        sent=sent,
        audit_service=audit_service,
    )
    return {
        "sent": sent,
        "temporary_password": temporary_password,
        "password_delivery": "one_time_response",
    }
