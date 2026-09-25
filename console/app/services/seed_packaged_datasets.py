from __future__ import annotations

import logging
import pathlib
from typing import Any

import asyncpg

from app.services.seed_packaged_catalog import (
    dataset_files,
    load_packaged_manifest,
    parse_dataset,
)
from app.services import seed_packaged_dataset_rows as row_helpers


logger = logging.getLogger(__name__)
_REGISTRY = pathlib.Path("/registry/cartridges")
_EXPECTED_CATALOG_FILES = 321
_EXPECTED_CATALOG_DIGEST = (
    "d0c7519304fe851ad01466bb725df6737b131ea12401d3f95c88e6b40e6640dc"
)

_parse_dataset = parse_dataset


async def _set_seed_scope(conn, tenant_id: object, workspace_id: object) -> None:
    await row_helpers.set_seed_scope(conn, tenant_id, workspace_id)


async def _datasets_has_column(conn, column_name: str) -> bool:
    return await row_helpers.datasets_has_column(conn, column_name)


async def _require_workspace_scoped_dataset_schema(conn) -> None:
    await row_helpers.require_workspace_scoped_dataset_schema(conn)


async def _update_dataset_row(conn, **kwargs) -> bool:
    return await row_helpers.update_dataset_row(conn, **kwargs)


async def _insert_dataset_row(conn, **kwargs) -> None:
    await row_helpers.insert_dataset_row(conn, **kwargs)


def _dataset_files() -> dict[str, list[pathlib.Path]]:
    return dataset_files(
        _REGISTRY,
        expected_files=_EXPECTED_CATALOG_FILES,
        expected_digest=_EXPECTED_CATALOG_DIGEST,
    )


def _load_packaged_manifest(
    packaged: dict[str, list[pathlib.Path]],
) -> dict[str, list[dict]]:
    return load_packaged_manifest(packaged)


async def _upsert_dataset_row(
    conn: asyncpg.Connection,
    *,
    dataset: dict,
    tenant_id: object,
    workspace_id: object,
    has_tenant_id: bool,
) -> None:
    kwargs = {
        "dataset": dataset,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "has_tenant_id": has_tenant_id,
    }
    if await _update_dataset_row(conn, **kwargs):
        return
    try:
        async with conn.transaction():
            await _insert_dataset_row(conn, **kwargs)
    except asyncpg.UniqueViolationError:
        if not await _update_dataset_row(conn, **kwargs):
            raise


async def _seed_packaged_dataset_rows(
    conn: asyncpg.Connection,
    *,
    packaged: dict[str, list[pathlib.Path]],
    target_workspaces: list[Any],
    has_tenant_id: bool,
) -> dict[str, int]:
    manifest = _load_packaged_manifest(packaged)
    workspaces = sorted(
        target_workspaces,
        key=lambda workspace: (str(workspace["tenant_id"]), str(workspace["id"])),
    )
    seeded_by_cartridge: dict[str, int] = {}
    for cartridge_id, datasets in manifest.items():
        seeded_rows = 0
        for workspace in workspaces:
            await _set_seed_scope(conn, workspace["tenant_id"], workspace["id"])
            for dataset in datasets:
                await _upsert_dataset_row(
                    conn,
                    dataset=dataset,
                    tenant_id=workspace["tenant_id"],
                    workspace_id=workspace["id"],
                    has_tenant_id=has_tenant_id,
                )
                seeded_rows += 1
        logger.info(
            "[seed_packaged_datasets] %s: seeded %d dataset rows across %d workspaces",
            cartridge_id,
            seeded_rows,
            len(workspaces),
        )
        seeded_by_cartridge[cartridge_id] = seeded_rows
    return seeded_by_cartridge


async def seed_packaged_datasets(pool: asyncpg.Pool) -> None:
    packaged = _dataset_files()
    if not packaged:
        raise ValueError("packaged dataset manifest is empty")
    async with pool.acquire() as conn, conn.transaction():
        await _require_workspace_scoped_dataset_schema(conn)
        workspaces = await conn.fetch(
            """SELECT id, tenant_id FROM public.workspaces
                 ORDER BY created_at ASC, name ASC, id ASC"""
        )
        if not workspaces:
            logger.warning("[seed_packaged_datasets] no workspace found; skipping")
            return
        await _seed_packaged_dataset_rows(
            conn,
            packaged=packaged,
            target_workspaces=list(workspaces),
            has_tenant_id=await _datasets_has_column(conn, "tenant_id"),
        )


async def seed_packaged_datasets_for_workspace(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str | None = None,
) -> dict[str, Any]:
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required")

    packaged = _dataset_files()
    if cartridge_id:
        selected = packaged.get(cartridge_id)
        packaged = {cartridge_id: selected} if selected else {}
    if not packaged:
        raise ValueError("packaged dataset manifest is empty")

    async with pool.acquire() as conn, conn.transaction():
        await _require_workspace_scoped_dataset_schema(conn)
        seeded = await _seed_packaged_dataset_rows(
            conn,
            packaged=packaged,
            target_workspaces=[{"id": workspace_id, "tenant_id": tenant_id}],
            has_tenant_id=await _datasets_has_column(conn, "tenant_id"),
        )
    return {
        "status": "success",
        "cartridge_id": cartridge_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "seeded_rows": sum(seeded.values()),
        "cartridges": seeded,
    }
