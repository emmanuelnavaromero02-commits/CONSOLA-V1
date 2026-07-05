from __future__ import annotations

from types import SimpleNamespace

import asyncio
import pytest

from app.domains.system.lifespan import (
    cancel_background_tasks,
    env_flag_enabled,
    run_packaged_startup_seeds,
)


@pytest.mark.asyncio
async def test_run_packaged_startup_seeds_runs_components_through_readiness_wrapper():
    app = SimpleNamespace()
    pool = object()
    pool_calls = 0
    ran_components: list[str] = []
    seeded_pools: list[object] = []

    async def get_db_pool():
        nonlocal pool_calls
        pool_calls += 1
        return pool

    async def run_startup_seed(received_app, component, callback):
        assert received_app is app
        ran_components.append(component)
        await callback()

    async def seed_one(received_pool):
        seeded_pools.append(received_pool)

    async def seed_two(received_pool):
        seeded_pools.append(received_pool)

    await run_packaged_startup_seeds(
        app,
        get_db_pool=get_db_pool,
        run_startup_seed=run_startup_seed,
        seeds=(("seed_one", seed_one), ("seed_two", seed_two)),
    )

    assert ran_components == ["seed_one", "seed_two"]
    assert seeded_pools == [pool, pool]
    assert pool_calls == 2


def test_env_flag_enabled_parses_disabled_values():
    assert env_flag_enabled(None) is True
    assert env_flag_enabled(None, default=False) is False
    assert env_flag_enabled("true") is True
    assert env_flag_enabled("  yes ") is True
    assert env_flag_enabled("0") is False
    assert env_flag_enabled("false") is False
    assert env_flag_enabled("NO") is False
    assert env_flag_enabled("off") is False


@pytest.mark.asyncio
async def test_cancel_background_tasks_cancels_and_awaits_tasks():
    cancelled = []

    async def worker():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)

    await cancel_background_tasks(task, None)

    assert task.cancelled()
    assert cancelled == [True]
