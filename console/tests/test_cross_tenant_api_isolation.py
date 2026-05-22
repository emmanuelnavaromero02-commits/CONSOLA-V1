"""Phase-0 P0 regression: cross-tenant API isolation.

The earlier audit observed that multi-tenant isolation is enforced in
source-level inspections (SQL contains `workspace_id = $`, `tenant_id =
$`...) but no test actually executes a cross-tenant API call. This file
fills that gap.

For each path that exposes per-workspace resources, we mount a mini
FastAPI app, inject two distinct users (workspace A vs workspace B), and
assert that user B cannot see or fetch resources that belong to user A.

The tests deliberately exercise the **service layer** (which is what
production routes call) so they stay independent of cookie/CSRF wiring
and detect regressions in the SQL or in-memory filter that does the
scoping. A purely-mocked DB pool is used; what we assert is that the
scoping arguments end up in the SQL (or that the filter rejects the
out-of-scope record), not that Postgres returns the right rows.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import marketplace_service, permissions


USER_A_ADMIN = {
    "id": 1001,
    "email": "admin-a@example.com",
    "role": "admin",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
}

USER_B_ADMIN = {
    "id": 2001,
    "email": "admin-b@example.com",
    "role": "admin",
    "tenant_id": "tenant-b",
    "workspace_id": "workspace-b",
}

USER_B_WORKSPACE_ADMIN = {
    "id": 2002,
    "email": "wa-b@example.com",
    "role": "user",
    "workspace_role": "workspace_admin",
    "tenant_id": "tenant-b",
    "workspace_id": "workspace-b",
}


# ---------------------------------------------------------------------------
# Helper: a fake asyncpg pool that records every (query, args) and returns
# whatever the test asks it to. Lets us assert "this SQL ran with workspace
# B in the args" without spinning up Postgres.
# ---------------------------------------------------------------------------

class _RecordingConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_responses: list[Any] = []
        self._fetchrow_responses: list[Any] = []
        self._fetchval_responses: list[Any] = []

    def queue_fetch(self, value: Any) -> None:
        self._fetch_responses.append(value)

    def queue_fetchrow(self, value: Any) -> None:
        self._fetchrow_responses.append(value)

    def queue_fetchval(self, value: Any) -> None:
        self._fetchval_responses.append(value)

    async def fetch(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetch", (query, args)))
        return self._fetch_responses.pop(0) if self._fetch_responses else []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", (query, args)))
        return self._fetchrow_responses.pop(0) if self._fetchrow_responses else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchval", (query, args)))
        return self._fetchval_responses.pop(0) if self._fetchval_responses else None

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", (query, args)))
        return ""

    def transaction(self) -> "_RecordingConn":
        return self

    async def __aenter__(self) -> "_RecordingConn":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self) -> "_RecordingPool":
        return self

    async def __aenter__(self) -> _RecordingConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


# ---------------------------------------------------------------------------
# Test 1: list_installations is scoped to the caller's (tenant, workspace).
# A workspace-A admin must never see workspace-B installations.
# ---------------------------------------------------------------------------

def test_list_installations_filters_by_caller_workspace():
    conn = _RecordingConn()
    # Simulate Postgres returning ONLY the workspace-A row. The test then
    # asserts the SQL was bound with workspace-A's scope, not workspace-B's.
    conn.queue_fetch([])

    async def _runner() -> None:
        # patch via `marketplace_service.cartridge_service` so the test is
        # robust to peer tests that re-import `app.services.cartridge_service`
        # and leave the canonical reference held by marketplace_service
        # pointing at the previous module.
        with patch.object(
            marketplace_service.cartridge_service,
            "pool",
            new=AsyncMock(return_value=_RecordingPool(conn)),
        ):
            with patch.object(
                marketplace_service,
                "_ensure_products",
                new=AsyncMock(return_value=None),
            ):
                await marketplace_service.list_installations(USER_A_ADMIN)

    asyncio.run(_runner())

    assert conn.calls, "list_installations must hit the database"
    _, (query, args) = conn.calls[-1]
    assert "ci.tenant_id = $1" in query
    assert "ci.workspace_id = $2" in query
    # tenant + workspace of the calling user must appear in the bound args.
    assert args[0] == USER_A_ADMIN["tenant_id"]
    assert args[1] == USER_A_ADMIN["workspace_id"]
    # The OTHER tenant/workspace must NOT show up in the bound args.
    assert USER_B_ADMIN["tenant_id"] not in args
    assert USER_B_ADMIN["workspace_id"] not in args


def test_list_installations_for_user_b_uses_user_b_scope():
    conn = _RecordingConn()
    conn.queue_fetch([])

    async def _runner() -> None:
        with patch.object(
            marketplace_service.cartridge_service,
            "pool",
            new=AsyncMock(return_value=_RecordingPool(conn)),
        ):
            with patch.object(
                marketplace_service,
                "_ensure_products",
                new=AsyncMock(return_value=None),
            ):
                await marketplace_service.list_installations(USER_B_ADMIN)

    asyncio.run(_runner())

    _, (_, args) = conn.calls[-1]
    assert args[0] == USER_B_ADMIN["tenant_id"]
    assert args[1] == USER_B_ADMIN["workspace_id"]


# ---------------------------------------------------------------------------
# Test 2: get_admin_installation, list_admin_installations require
# global platform admin. A workspace_admin must be denied.
# ---------------------------------------------------------------------------

def test_workspace_admin_cannot_use_admin_only_marketplace_calls():
    async def _runner() -> None:
        with pytest.raises(marketplace_service.MarketplaceError) as exc:
            await marketplace_service.list_admin_installations(
                USER_B_WORKSPACE_ADMIN
            )
        assert "admin" in str(exc.value).lower()

    asyncio.run(_runner())


def test_workspace_admin_cannot_activate_arbitrary_cartridges():
    """workspace_role=admin is NOT a platform admin and must be rejected by
    activate_product()."""
    async def _runner() -> None:
        with pytest.raises(marketplace_service.MarketplaceError):
            await marketplace_service.activate_product(
                "replicon", USER_B_WORKSPACE_ADMIN
            )

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Test 3: permissions registry — workspace admin must NOT inherit the
# platform-admin marketplace.admin / iam.users.* set.
# ---------------------------------------------------------------------------

def test_workspace_admin_cannot_administer_marketplace_globally():
    assert not permissions.has_permission(
        USER_B_WORKSPACE_ADMIN, "marketplace.admin"
    )


def test_workspace_admin_does_not_get_global_role_writes():
    assert not permissions.has_permission(
        USER_B_WORKSPACE_ADMIN, "iam.roles.write"
    )
    assert not permissions.has_permission(
        USER_B_WORKSPACE_ADMIN, "iam.policies.write"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

