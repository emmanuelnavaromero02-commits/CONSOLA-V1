from __future__ import annotations

import json
import secrets as py_secrets
from typing import Any

from app.services import auth, audit_service


_SECRET_MASK = "***"


async def list_settings(category: str | None = None, include_secrets: bool = False) -> list[dict]:
    pool = await auth.pool()
    if category:
        rows = await pool.fetch(
            "SELECT key, value, is_secret, category, description, updated_at "
            "FROM system_settings WHERE category = $1 ORDER BY key",
            category,
        )
    else:
        rows = await pool.fetch(
            "SELECT key, value, is_secret, category, description, updated_at "
            "FROM system_settings ORDER BY category, key",
        )
    result = []
    for r in rows:
        item = dict(r)
        if item["is_secret"] and not include_secrets:
            item["value"] = _SECRET_MASK
        result.append(item)
    return result


async def get_setting(key: str, include_secret: bool = False) -> dict | None:
    pool = await auth.pool()
    row = await pool.fetchrow(
        "SELECT key, value, is_secret, category, description, updated_at "
        "FROM system_settings WHERE key = $1",
        key,
    )
    if not row:
        return None
    item = dict(row)
    if item["is_secret"] and not include_secret:
        item["value"] = _SECRET_MASK
    return item


async def set_setting(
    key: str,
    value: Any,
    user_id: int,
    user_email: str | None = None,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        UPDATE system_settings
        SET value = $2::jsonb, updated_by = $3, updated_at = NOW()
        WHERE key = $1
        RETURNING key, value, is_secret, category, description, updated_at
        """,
        key,
        json.dumps(value),
        user_id,
    )
    if not row:
        raise KeyError(f"setting '{key}' does not exist")
    await audit_service.record_event(
        user_id=user_id,
        email=user_email,
        action="settings.update",
        resource_type="system_setting",
        resource_id=key,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"is_secret": row["is_secret"]},
    )
    item = dict(row)
    if item["is_secret"]:
        item["value"] = _SECRET_MASK
    return item


async def reveal_setting(
    key: str,
    user_id: int,
    user_email: str | None = None,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict | None:
    item = await get_setting(key, include_secret=True)
    if not item:
        return None
    await audit_service.record_event(
        user_id=user_id,
        email=user_email,
        action="settings.reveal",
        resource_type="system_setting",
        resource_id=key,
        ip=ip,
        user_agent=user_agent,
        status="success",
    )
    return item


async def rotate_secret(
    key: str,
    user_id: int,
    user_email: str | None = None,
    length_bytes: int = 32,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    new_value = py_secrets.token_hex(length_bytes)
    # set_setting() already records settings.update with ip+UA; this entry
    # marks the rotate intent on top so an auditor can reconstruct both.
    result = await set_setting(key, new_value, user_id, user_email, ip=ip, user_agent=user_agent)
    await audit_service.record_event(
        user_id=user_id,
        email=user_email,
        action="settings.rotate",
        resource_type="system_setting",
        resource_id=key,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"length_bytes": length_bytes},
    )
    return result
