from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import control_room_service as service
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_execution_target import (
    execution_target_digest,
)
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from tests.control_room_live_preview_authority import (
    TEMPLATE_ID,
    backend_is_blocked,
    cleanup_preview_scopes,
    mutate_preview_target,
    preview_snapshot,
    seed_preview_scopes,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


CASES = (
    ("entity", "refresh_first"),
    ("connection", "refresh_first"),
    ("entity", "preview_first"),
    ("connection", "preview_first"),
)


def _changed_snapshot(item: dict, target_kind: str) -> dict:
    changed = deepcopy(item)
    if target_kind == "entity":
        changed["entity_id"] = f"{item['entity_id']}-B"
    else:
        metadata = changed.setdefault("metadata", {})
        metadata.setdefault("connection", {})["writeback_path"] = "/writeback/B"
    return changed


def _expected_success_counts(before: dict) -> dict:
    return {name: int(value) + 1 for name, value in before.items()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_kind,lock_order",
    CASES,
    ids=(
        "entity-refresh-first",
        "path-refresh-first",
        "entity-preview-first",
        "path-preview-first",
    ),
)
async def test_live_preview_linearizes_against_refresh_target_changes(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
    lock_order: str,
) -> None:
    scopes = await seed_preview_scopes(
        postgres_with_real_init_schema, omega_console_live_dsn
    )
    active, sibling = scopes
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=6)
    writer = observer = None
    transaction = None
    preview_task = mutation_task = None
    release = asyncio.Event()
    try:
        before = await preview_snapshot(postgres_with_real_init_schema, active)
        sibling_before = await preview_snapshot(postgres_with_real_init_schema, sibling)
        changed = _changed_snapshot(active.item, target_kind)
        assert business_observation_fingerprint(active.item) == (
            business_observation_fingerprint(changed)
        )
        assert execution_target_digest(
            active.item, ACTION_TEMPLATES[TEMPLATE_ID]
        ) != execution_target_digest(changed, ACTION_TEMPLATES[TEMPLATE_ID])

        entered = asyncio.Event()
        preview_pid: int | None = None
        original_lock = service.lock_authoritative_execution_context

        async def gated_lock(conn, *args, **kwargs):
            nonlocal preview_pid
            preview_pid = int(await conn.fetchval("SELECT pg_backend_pid()"))
            if lock_order == "refresh_first":
                entered.set()
                await release.wait()
                return await original_lock(conn, *args, **kwargs)
            context = await original_lock(conn, *args, **kwargs)
            entered.set()
            await release.wait()
            return context

        monkeypatch.setattr(service, "lock_authoritative_execution_context", gated_lock)
        monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=pool))
        audit = service.audit_service
        monkeypatch.setattr(audit, "_audit_auth_module", lambda: service.auth)
        monkeypatch.setattr(
            service, "_item_for_mutation", AsyncMock(return_value=active.item)
        )

        preview_task = asyncio.create_task(
            service.action_preview(
                active.item_id,
                active.user,
                template_id=TEMPLATE_ID,
                binding_id=active.binding_id,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        writer = await asyncpg.connect(postgres_with_real_init_schema)
        observer = await asyncpg.connect(postgres_with_real_init_schema)
        transaction = writer.transaction()
        await transaction.start()

        if lock_order == "refresh_first":
            target_b = await mutate_preview_target(
                writer, active, target_kind=target_kind
            )
            release.set()
            assert preview_pid is not None
            assert await backend_is_blocked(observer, preview_pid)
            await transaction.commit()
            transaction = None
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(preview_task, timeout=5)
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "item_business_state_changed"
        else:
            writer_pid = int(await writer.fetchval("SELECT pg_backend_pid()"))
            mutation_task = asyncio.create_task(
                mutate_preview_target(writer, active, target_kind=target_kind)
            )
            assert await backend_is_blocked(observer, writer_pid)
            release.set()
            response = await asyncio.wait_for(preview_task, timeout=5)
            target_b = await asyncio.wait_for(mutation_task, timeout=5)
            await transaction.commit()
            transaction = None
            assert "authority_audit" not in response["action_run"]["metadata"]
            assert response["payload"]["item"]["entity_id"] == active.item["entity_id"]

        after = await preview_snapshot(postgres_with_real_init_schema, active)
        sibling_after = await preview_snapshot(postgres_with_real_init_schema, sibling)
        assert after["item"] == target_b
        if lock_order == "refresh_first":
            assert after["counts"] == before["counts"]
            assert after["runs"] == before["runs"] == []
        else:
            assert after["counts"] == _expected_success_counts(before["counts"])
            assert len(after["runs"]) == 1
            assert after["runs"][0]["metadata"]["authority_audit"]["binding_id"] == (
                active.binding_id
            )
        assert sibling_after == sibling_before
    finally:
        release.set()
        if transaction is not None:
            await transaction.rollback()
        if writer is not None:
            await writer.close()
        if observer is not None:
            await observer.close()
        for task in (preview_task, mutation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (preview_task, mutation_task) if task is not None),
            return_exceptions=True,
        )
        await pool.close()
        await cleanup_preview_scopes(postgres_with_real_init_schema, scopes)


@pytest.mark.asyncio
async def test_live_preview_rejects_binding_at_exact_expiry_before_first_dml(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes = await seed_preview_scopes(
        postgres_with_real_init_schema, omega_console_live_dsn
    )
    active, sibling = scopes
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    try:
        before = await preview_snapshot(postgres_with_real_init_schema, active)
        sibling_before = await preview_snapshot(postgres_with_real_init_schema, sibling)
        original_guard = service.require_authoritative_binding_current
        calls = 0

        def expire_at_boundary(context, user):
            nonlocal calls
            calls += 1
            expires_at = datetime.fromisoformat(
                context.binding.expires_at.replace("Z", "+00:00")
            )
            return original_guard(context, user, clock=lambda: expires_at)

        monkeypatch.setattr(
            service, "require_authoritative_binding_current", expire_at_boundary
        )
        monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(
            service, "_item_for_mutation", AsyncMock(return_value=active.item)
        )

        with pytest.raises(HTTPException) as exc:
            await service.action_preview(
                active.item_id,
                active.user,
                template_id=TEMPLATE_ID,
                binding_id=active.binding_id,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "item_business_state_changed"
        assert calls == 1
        assert await preview_snapshot(postgres_with_real_init_schema, active) == before
        assert (
            await preview_snapshot(postgres_with_real_init_schema, sibling)
            == sibling_before
        )
    finally:
        await pool.close()
        await cleanup_preview_scopes(postgres_with_real_init_schema, scopes)


@pytest.mark.asyncio
async def test_live_dry_run_rejects_stale_snapshot_without_mutations(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes = await seed_preview_scopes(
        postgres_with_real_init_schema, omega_console_live_dsn
    )
    active, sibling = scopes
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    try:
        before = await preview_snapshot(postgres_with_real_init_schema, active)
        sibling_before = await preview_snapshot(postgres_with_real_init_schema, sibling)
        writer = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            target_b = await mutate_preview_target(writer, active, target_kind="entity")
        finally:
            await writer.close()
        monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=pool))

        async def no_rows(*_args, **_kwargs):
            return []

        with pytest.raises(HTTPException) as cross_scope:
            await service.action_preview(
                active.item_id,
                sibling.user,
                template_id=TEMPLATE_ID,
                binding_id=active.binding_id,
                fetcher=no_rows,
            )
        assert cross_scope.value.status_code == 404
        monkeypatch.setattr(
            service, "_item_for_mutation", AsyncMock(return_value=active.item)
        )

        with pytest.raises(HTTPException) as exc:
            await service.action_dry_run(
                active.item_id,
                active.user,
                template_id=TEMPLATE_ID,
                binding_id=active.binding_id,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "item_business_state_changed"
        after = await preview_snapshot(postgres_with_real_init_schema, active)
        assert after["item"] == target_b
        assert after["counts"] == before["counts"]
        assert after["runs"] == []
        assert (
            await preview_snapshot(postgres_with_real_init_schema, sibling)
            == sibling_before
        )
    finally:
        await pool.close()
        await cleanup_preview_scopes(postgres_with_real_init_schema, scopes)
