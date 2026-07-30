from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.services import control_room_service
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
)
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.surface_snapshot import SurfaceScope, SurfaceSnapshot
from app.services.db_scope import run_with_db_scope
from tests.test_control_room_live_postgres_workflows import AsyncNoop, _item, _rows


@dataclass(frozen=True)
class AuthorityScope:
    tenant_id: str
    workspace_id: str
    item_id: str
    maker: dict[str, Any]
    checker: dict[str, Any]
    item: dict[str, Any]


@dataclass(frozen=True)
class AuthoritySeed:
    admin_dsn: str
    console_dsn: str
    first: AuthorityScope
    second: AuthorityScope
    outsider: dict[str, Any]


async def _new_user(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    workspace_role: str,
    suffix: str,
) -> dict[str, Any]:
    user_id = int(
        await conn.fetchval(
            """INSERT INTO users(email, password_hash, role, tenant_id, is_active)
               VALUES ($1, 'x', 'user', $2::uuid, TRUE) RETURNING id""",
            f"authority-{suffix}@example.test",
            tenant_id,
        )
    )
    await conn.execute(
        """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
           SELECT $1, $2::uuid, id FROM roles WHERE name=$3""",
        user_id,
        workspace_id,
        workspace_role,
    )
    return {
        "id": user_id,
        "email": f"authority-{suffix}@example.test",
        "role": "user",
        "workspace_role": workspace_role,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "allowed_cartridges": ["sap_hcm"],
    }


async def _seed_scope(conn: asyncpg.Connection, *, suffix: str) -> AuthorityScope:
    tenant_id = str(
        await conn.fetchval(
            "INSERT INTO tenants(name, slug) VALUES ($1, $2) RETURNING id",
            f"Authority tenant {suffix}",
            f"authority-{suffix}",
        )
    )
    workspace_id = str(
        await conn.fetchval(
            "INSERT INTO workspaces(tenant_id, name) VALUES ($1, $2) RETURNING id",
            tenant_id,
            f"Authority workspace {suffix}",
        )
    )
    maker = await _new_user(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workspace_role="workspace_admin",
        suffix=f"maker-{suffix}",
    )
    checker = await _new_user(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workspace_role="control_room_approver",
        suffix=f"checker-{suffix}",
    )
    item_id = f"authority-item-{suffix}"
    item = _item(item_id, tenant_id, workspace_id)
    await persist_item_rows(
        conn,
        _rows([item], tenant_id, workspace_id, owner=maker["id"]),
        owner_scope_id=maker["id"],
    )
    decision = await create_and_link_decision(
        conn,
        user=maker,
        item={**item, "owner_user_id": maker["id"]},
        workspace_id=workspace_id,
        ensure_item_row=AsyncNoop(),
        record_item_event=AsyncNoop(),
    )
    item.update(
        owner_user_id=maker["id"],
        decision_id=int(decision["id"]),
        status="decision_created",
        execution_status="not_started",
    )
    return AuthorityScope(tenant_id, workspace_id, item_id, maker, checker, item)


async def seed_authority(admin_dsn: str, console_dsn: str) -> AuthoritySeed:
    conn = await asyncpg.connect(admin_dsn)
    try:
        suffix = uuid.uuid4().hex[:10]
        first = await _seed_scope(conn, suffix=f"a-{suffix}")
        second = await _seed_scope(conn, suffix=f"b-{suffix}")
        outsider = await _new_user(
            conn,
            tenant_id=first.tenant_id,
            workspace_id=first.workspace_id,
            workspace_role="viewer",
            suffix=f"outsider-{suffix}",
        )
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(console_dsn, min_size=1, max_size=4)
    try:
        loaded = []
        for scope in (first, second):
            item = await load_persisted_command_item(
                scope.item_id,
                scope.maker,
                pool_factory=lambda: _pool(pool),
                run_scoped=run_with_db_scope,
                item_statuses=control_room_service.ITEM_STATUSES,
                severity_weights=control_room_service.SEVERITY_WEIGHT,
            )
            if item is None:
                raise RuntimeError("authority fixture item is not readable")
            loaded.append(
                AuthorityScope(
                    scope.tenant_id,
                    scope.workspace_id,
                    scope.item_id,
                    scope.maker,
                    scope.checker,
                    item,
                )
            )
    finally:
        await pool.close()
    return AuthoritySeed(admin_dsn, console_dsn, loaded[0], loaded[1], outsider)


async def seed_authority_item(
    seed: AuthoritySeed, base: AuthorityScope, marker: str
) -> AuthorityScope:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        item_id = f"authority-item-{marker}-{uuid.uuid4().hex[:8]}"
        item = _item(item_id, base.tenant_id, base.workspace_id)
        await persist_item_rows(
            conn,
            _rows(
                [item],
                base.tenant_id,
                base.workspace_id,
                owner=base.maker["id"],
            ),
            owner_scope_id=base.maker["id"],
        )
        decision = await create_and_link_decision(
            conn,
            user=base.maker,
            item={**item, "owner_user_id": base.maker["id"]},
            workspace_id=base.workspace_id,
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
        )
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=2)
    try:
        loaded = await load_persisted_command_item(
            item_id,
            base.maker,
            pool_factory=lambda: _pool(pool),
            run_scoped=run_with_db_scope,
            item_statuses=control_room_service.ITEM_STATUSES,
            severity_weights=control_room_service.SEVERITY_WEIGHT,
        )
    finally:
        await pool.close()
    if loaded is None:
        raise RuntimeError("authority fixture item is not readable")
    assert int(loaded.get("decision_id") or 0) == int(decision["id"])
    return AuthorityScope(
        base.tenant_id,
        base.workspace_id,
        item_id,
        base.maker,
        base.checker,
        loaded,
    )


async def _pool(pool: asyncpg.Pool) -> asyncpg.Pool:
    return pool


def snapshot(scope: AuthorityScope) -> SurfaceSnapshot:
    return SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=SurfaceScope(scope.tenant_id, scope.workspace_id),
        items=(scope.item,),
        diagnostics=(),
        sources=(),
        installations=(),
    )


__all__ = (
    "AuthoritySeed",
    "seed_authority",
    "seed_authority_item",
    "snapshot",
)
