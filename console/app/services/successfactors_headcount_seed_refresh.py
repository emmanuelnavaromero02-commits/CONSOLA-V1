from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import asyncpg

from app.services.seed_packaged_datasets import (
    _dataset_files,
    _datasets_has_column,
    _datasets_workspace_name_conflict_available,
    _parse_dataset,
    _set_seed_scope,
    _upsert_dataset_row,
)


logger = logging.getLogger(__name__)

_CARTRIDGE = "sap_successfactors"
HEADCOUNT_DATASET_NAMES = frozenset(
    {
        "sap_successfactors_headcount_by_company",
        "sap_successfactors_headcount_by_location",
        "sap_successfactors_headcount_by_department",
    }
)


def _selected_files(packaged: dict[str, list[Path]]) -> dict[str, list[Path]]:
    selected = [
        path
        for path in packaged.get(_CARTRIDGE, [])
        if path.stem in HEADCOUNT_DATASET_NAMES
    ]
    selected_names = {path.stem for path in selected}
    if selected_names != HEADCOUNT_DATASET_NAMES:
        missing = sorted(HEADCOUNT_DATASET_NAMES - selected_names)
        raise RuntimeError(f"missing packaged SuccessFactors headcount SQL: {missing}")
    return {_CARTRIDGE: sorted(selected)}


async def refresh_successfactors_headcount_definitions(pool: asyncpg.Pool) -> None:
    """Idempotently refresh only the three guarded headcount definitions."""

    packaged = _selected_files(_dataset_files())
    async with pool.acquire() as conn:
        workspaces = await conn.fetch(
            """
            SELECT id, tenant_id
              FROM workspaces
             ORDER BY created_at ASC, name ASC
            """
        )
        if not workspaces:
            logger.warning("[sf_headcount_seed_refresh] no workspace found; skipping")
            return
        has_tenant_id = await _datasets_has_column(conn, "tenant_id")
        scoped_conflict = await _datasets_workspace_name_conflict_available(conn)
        target_workspaces: list[Any] = list(
            workspaces if scoped_conflict else workspaces[:1]
        )
        datasets = [_parse_dataset(path) for path in packaged[_CARTRIDGE]]
        refreshed = 0
        async with conn.transaction():
            for workspace in target_workspaces:
                await _set_seed_scope(conn, workspace["tenant_id"], workspace["id"])
                for dataset in datasets:
                    await _upsert_dataset_row(
                        conn,
                        dataset=dataset,
                        tenant_id=workspace["tenant_id"],
                        workspace_id=workspace["id"],
                        has_tenant_id=has_tenant_id,
                        scoped_conflict=scoped_conflict,
                    )
                    refreshed += 1
    logger.info(
        "[sf_headcount_seed_refresh] refreshed %d scoped definitions",
        refreshed,
    )


__all__ = (
    "HEADCOUNT_DATASET_NAMES",
    "refresh_successfactors_headcount_definitions",
)
