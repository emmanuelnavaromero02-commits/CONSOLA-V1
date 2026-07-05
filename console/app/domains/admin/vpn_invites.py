"""VPN invite helpers for admin user lifecycle routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


async def create_vpn_config_link(
    user_id: int,
    email: str,
    *,
    vpn_configured: Callable[[], bool],
    vpn_service: Any,
    tokens_service: Any,
    vpn_link: Callable[[str], str],
    internal_error_request_id: Callable[[], str],
    logger_exception: Callable[..., None],
    vpn_error_cls: type[BaseException],
) -> dict:
    try:
        if not vpn_configured():
            return {
                "issued": False,
                "error": "VPN_API_URL / VPN_API_PASSWORD no configurados",
            }
        wg_id = await vpn_service.create_client(email)
        vpn_tok, _ = await tokens_service.create(user_id, "vpn", wg_client_id=wg_id)
        try:
            conf_text = await vpn_service.get_config(wg_id)
        except Exception:
            conf_text = None
        return {
            "issued": True,
            "link": vpn_link(vpn_tok),
            "wg_client_id": wg_id,
            "conf_text": conf_text,
        }
    except vpn_error_cls as exc:
        request_id = internal_error_request_id()
        logger_exception(
            "vpn issuance failed request_id=%s",
            request_id,
            extra={"request_id": request_id, "exception_type": type(exc).__name__},
        )
        return {"issued": False, "error": "Internal Error", "request_id": request_id}
    except Exception as exc:
        request_id = internal_error_request_id()
        logger_exception(
            "unexpected vpn issuance failure request_id=%s",
            request_id,
            extra={"request_id": request_id, "exception_type": type(exc).__name__},
        )
        return {"issued": False, "error": "Internal Error", "request_id": request_id}


async def issue_vpn_for_user(
    user_id: int,
    email: str,
    name: str | None,
    *,
    create_vpn_config_link: Callable[[int, str], Awaitable[dict]],
    email_service: Any,
    vpn_ttl_hours: int,
) -> dict:
    result = await create_vpn_config_link(user_id, email)
    if not result.get("issued"):
        return result
    subject, html = email_service.render_vpn_config(
        name, result["link"], vpn_ttl_hours
    )
    sent = await email_service.send_email(email, subject, html)
    return {
        "issued": True,
        "email_sent": sent,
        "wg_client_id": result["wg_client_id"],
    }


async def reissue_vpn_for_user_payload(
    *,
    user_id: int,
    admin_user: dict[str, Any],
    request_ip: str | None,
    user_agent: str | None,
    auth_service: Any,
    audit_service: Any,
    assert_can_manage_target_user: Callable[[dict[str, Any], int], Awaitable[None]],
    issue_vpn_for_user: Callable[[int, str, str | None], Awaitable[dict]],
) -> dict:
    target_user = await auth_service.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")

    await assert_can_manage_target_user(admin_user, user_id)
    result = await issue_vpn_for_user(
        user_id, target_user["email"], target_user.get("name")
    )
    await audit_service.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "vpn.reissued",
        "user",
        str(user_id),
        ip=request_ip,
        user_agent=user_agent,
        metadata={
            "issued": bool(result.get("issued")),
            "email_sent": result.get("email_sent"),
        },
    )
    return {"reissued": result.get("issued", False), **result}


async def rollback_failed_invite(
    user_id: int,
    vpn_result: dict | None = None,
    *,
    vpn_service: Any,
    auth_service: Any,
    logger_warning: Callable[..., None],
    logger_exception: Callable[..., None],
) -> None:
    vpn_client_id = (vpn_result or {}).get("wg_client_id")
    if vpn_client_id:
        try:
            await vpn_service.delete_client(str(vpn_client_id))
        except Exception:
            logger_warning("invite rollback could not delete vpn client", exc_info=True)
    try:
        await auth_service.delete_user(user_id)
    except Exception:
        logger_exception("invite rollback could not delete user_id=%s", user_id)
