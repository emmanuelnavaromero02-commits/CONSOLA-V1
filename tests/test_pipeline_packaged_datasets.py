from __future__ import annotations

import pytest

from app.domains.pipeline.packaged_datasets import ensure_sync_packaged_datasets


@pytest.mark.anyio
async def test_ensure_sync_packaged_datasets_skips_other_cartridges():
    payload = await ensure_sync_packaged_datasets(
        cartridge="replicon",
        user=None,
        workspace_scope_from_user=None,
        get_db_pool=None,
        seed_packaged_datasets_for_workspace=None,
        logger_info=None,
    )

    assert payload == {"status": "skipped", "reason": "cartridge_not_packaged"}


@pytest.mark.anyio
async def test_ensure_sync_packaged_datasets_seeds_scoped_successfactors():
    seed_calls = []
    logs = []

    async def get_db_pool():
        return "pool"

    async def seed_for_workspace(pool, **kwargs):
        seed_calls.append((pool, kwargs))
        return {"status": "ok", "seeded_rows": 3}

    payload = await ensure_sync_packaged_datasets(
        cartridge="sap_successfactors",
        user={"sub": "user-1"},
        workspace_scope_from_user=lambda user: ("tenant-1", "workspace-1"),
        get_db_pool=get_db_pool,
        seed_packaged_datasets_for_workspace=seed_for_workspace,
        logger_info=lambda *args: logs.append(args),
    )

    assert payload == {"status": "ok", "seeded_rows": 3}
    assert seed_calls == [
        (
            "pool",
            {
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "cartridge_id": "sap_successfactors",
            },
        )
    ]
    assert logs[0][0].startswith("sync packaged dataset seed cartridge=%s")
