"""Pure helpers for account activation, reset and VPN invite flows."""

from __future__ import annotations

import io
import re
import secrets
from collections.abc import Mapping
from typing import Pattern

from fastapi import HTTPException


def normalize_email_or_400(
    value: object | None,
    *,
    email_re: Pattern[str],
    required_message: str = "email is required",
) -> str:
    email = str(value or "").strip().lower()
    if not email:
        raise HTTPException(400, required_message)
    if len(email) > 254 or not email_re.fullmatch(email):
        raise HTTPException(400, "invalid email")
    return email


def password_min_length(auth_module: object, default: int = 12) -> int:
    return int(getattr(auth_module, "MIN_PASSWORD_LENGTH", default))


def validate_password_or_400(
    password: object | None,
    *,
    min_length: int,
    field: str = "password",
) -> str:
    value = str(password or "")
    if not value:
        raise HTTPException(400, f"{field} is required")
    if len(value) < min_length:
        raise HTTPException(400, f"el password debe tener al menos {min_length} caracteres")
    return value


def token_link(app_base_url: str, path: str, token: str) -> str:
    return f"{app_base_url.rstrip('/')}/{path.lstrip('/')}?token={token}"


def vpn_token_link(app_base_url: str, token: str) -> str:
    return f"{app_base_url.rstrip('/')}/vpn-config/{token}"


def vpn_configured(env: Mapping[str, str | None]) -> bool:
    return bool(env.get("VPN_API_URL") and env.get("VPN_API_PASSWORD"))


def safe_filename(email: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(email or "user")).strip("._-")
    return (cleaned or "user")[:120]


def pack_vpn_conf(conf_text: str, email: str) -> tuple[bytes, str]:
    import pyzipper

    password = secrets.token_urlsafe(9)
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(f"{safe_filename(email)}.conf", conf_text)
    return buf.getvalue(), password
