"""Workspace-scoped admin user visibility helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


async def workspace_rows_from_auth_stub(user_id: int, *, auth: Any) -> list[dict]:
    pool_factory = getattr(auth, "pool", None)
    if not callable(pool_factory):
        return []
    pool = await pool_factory()
    rows = await pool.fetch(
        "SELECT workspace_id::text AS workspace_id FROM user_workspace_roles WHERE user_id = $1",
        user_id,
    )
    return [dict(row) for row in rows]


async def target_user_workspace_ids(
    user_id: int,
    *,
    get_db_pool: Callable[[], Awaitable[Any]],
    auth: Any,
    workspace_scope_db_unavailable: Callable[[BaseException], bool],
) -> set[str]:
    try:
        pool = await get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not workspace_scope_db_unavailable(exc):
            raise
        rows = await workspace_rows_from_auth_stub(user_id, auth=auth)
        return {str(row["workspace_id"]) for row in rows if row.get("workspace_id")}
    rows = await pool.fetch(
        "SELECT workspace_id::text AS workspace_id FROM user_workspace_roles WHERE user_id = $1",
        user_id,
    )
    return {str(row["workspace_id"]) for row in rows if row["workspace_id"]}


async def workspace_summaries_for_users(
    user_ids: list[int],
    *,
    get_db_pool: Callable[[], Awaitable[Any]],
    workspace_scope_db_unavailable: Callable[[BaseException], bool],
) -> dict[int, list[dict]]:
    if not user_ids:
        return {}
    try:
        pool = await get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not workspace_scope_db_unavailable(exc):
            raise
        return {}
    rows = await pool.fetch(
        """
        SELECT uwr.user_id::int AS user_id,
               w.id::text AS workspace_id,
               w.name AS workspace_name,
               t.id::text AS tenant_id,
               t.name AS tenant_name,
               r.name AS workspace_role
          FROM user_workspace_roles uwr
          JOIN workspaces w ON w.id = uwr.workspace_id
          JOIN tenants t ON t.id = w.tenant_id
          JOIN roles r ON r.id = uwr.role_id
         WHERE uwr.user_id = ANY($1::int[])
         ORDER BY w.created_at ASC, w.name ASC, r.name ASC
        """,
        user_ids,
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(int(row["user_id"]), []).append(
            {
                "workspace_id": row["workspace_id"],
                "workspace_name": row["workspace_name"],
                "tenant_id": row["tenant_id"],
                "tenant_name": row["tenant_name"],
                "workspace_role": row["workspace_role"],
            }
        )
    return result


async def attach_workspace_summaries(
    users: list[dict],
    *,
    workspace_summaries: Callable[[list[int]], Awaitable[dict[int, list[dict]]]],
) -> list[dict]:
    ids = [int(u["id"]) for u in users if u.get("id") is not None]
    summaries = await workspace_summaries(ids)
    enriched: list[dict] = []
    for user in users:
        item = dict(user)
        if user.get("id") is not None:
            item["workspaces"] = summaries.get(int(user["id"]), [])
        else:
            item["workspaces"] = []
        enriched.append(item)
    return enriched


async def list_admin_users_payload(
    *,
    admin_user: dict,
    auth_list_users: Callable[..., Awaitable[list[dict]]],
    is_global_iam_admin: Callable[[dict | None], bool],
    visible_user_ids_for_admin: Callable[[dict, list[dict]], Awaitable[set[int]]],
    attach_workspace_summaries: Callable[[list[dict]], Awaitable[list[dict]]],
) -> dict:
    users = await auth_list_users(active_only=False)
    if is_global_iam_admin(admin_user):
        return {"users": await attach_workspace_summaries(users)}
    visible_ids = await visible_user_ids_for_admin(admin_user, users)
    scoped_users = [u for u in users if u.get("id") in visible_ids]
    return {"users": await attach_workspace_summaries(scoped_users)}


async def visible_user_ids_for_admin(
    admin_user: dict,
    users: list[dict],
    *,
    get_db_pool: Callable[[], Awaitable[Any]],
    workspace_scope_db_unavailable: Callable[[BaseException], bool],
    is_global_iam_admin: Callable[[dict | None], bool],
    session_workspace_ids: Callable[[dict | None], set[str]],
) -> set[int]:
    if is_global_iam_admin(admin_user):
        return {int(u["id"]) for u in users if u.get("id") is not None}
    workspace_ids = sorted(session_workspace_ids(admin_user))
    if not workspace_ids:
        return set()
    user_by_id = {int(u["id"]): u for u in users if u.get("id") is not None}
    try:
        pool = await get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not workspace_scope_db_unavailable(exc):
            raise
        visible: set[int] = set()
        admin_id = admin_user.get("id")
        for candidate in users:
            candidate_id = candidate.get("id")
            if candidate_id != admin_id and is_global_iam_admin(candidate):
                continue
            candidate_workspaces = session_workspace_ids(candidate)
            if candidate_id == admin_id or candidate_workspaces.intersection(
                workspace_ids
            ):
                visible.add(int(candidate_id))
        return visible
    rows = await pool.fetch(
        """
        SELECT DISTINCT user_id
          FROM user_workspace_roles
         WHERE workspace_id = ANY($1::uuid[])
        """,
        workspace_ids,
    )
    visible: set[int] = set()
    for row in rows:
        user_id = int(row["user_id"])
        if user_id == admin_user.get("id"):
            visible.add(user_id)
            continue
        candidate = user_by_id.get(user_id)
        if candidate and is_global_iam_admin(candidate):
            continue
        visible.add(user_id)
    return visible


async def set_workspace_role_for_user(
    user_id: int,
    workspace_id: str,
    role: str,
    *,
    get_db_pool: Callable[[], Awaitable[Any]],
) -> None:
    pool = await get_db_pool()
    role_id = await pool.fetchval("SELECT id FROM roles WHERE name = $1", role)
    if not role_id:
        raise HTTPException(400, "invalid workspace role")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM user_workspace_roles
                 WHERE user_id = $1
                   AND workspace_id = $2::uuid
                """,
                user_id,
                workspace_id,
            )
            await conn.execute(
                """
                INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
                VALUES ($1, $2::uuid, $3)
                """,
                user_id,
                workspace_id,
                role_id,
            )


async def assert_can_use_workspace(
    admin_user: dict,
    workspace_id: str | None,
    *,
    is_global_iam_admin: Callable[[dict | None], bool],
    session_workspace_ids: Callable[[dict | None], set[str]],
) -> None:
    if is_global_iam_admin(admin_user):
        return
    memberships = session_workspace_ids(admin_user)
    if not workspace_id or workspace_id not in memberships:
        raise HTTPException(403, "workspace access forbidden")


async def assert_can_manage_target_user(
    admin_user: dict,
    target_user_id: int,
    *,
    auth_get_user_by_id: Callable[[int], Awaitable[dict | None]],
    is_global_iam_admin: Callable[[dict | None], bool],
    session_workspace_ids: Callable[[dict | None], set[str]],
    target_workspace_ids: Callable[[int], Awaitable[set[str]]],
) -> None:
    if is_global_iam_admin(admin_user):
        return
    memberships = session_workspace_ids(admin_user)
    if not memberships:
        raise HTTPException(403, "workspace access forbidden")
    target = await auth_get_user_by_id(target_user_id)
    if is_global_iam_admin(target):
        raise HTTPException(403, "workspace admins cannot manage platform admins")
    target_workspaces = await target_workspace_ids(target_user_id)
    if not target_workspaces or memberships.isdisjoint(target_workspaces):
        raise HTTPException(403, "workspace access forbidden")
