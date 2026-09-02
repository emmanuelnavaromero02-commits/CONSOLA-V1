from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

SeedCallable = Callable[[Any], Awaitable[None]]
StartupSeed = tuple[str, SeedCallable]


def _default_startup_seeds() -> tuple[StartupSeed, ...]:
    from app.services.seed_dag_sources import seed_missing_dag_sources
    from app.services.seed_packaged_apps import seed_packaged_apps
    from app.services.seed_packaged_datasets import seed_packaged_datasets
    from app.services.seed_packaged_hints import seed_packaged_hints
    from app.services.reconcile_packaged_app_grants import (
        reconcile_packaged_app_grants,
    )

    return (
        ("seed_missing_dag_sources", seed_missing_dag_sources),
        ("seed_packaged_datasets", seed_packaged_datasets),
        ("seed_packaged_hints", seed_packaged_hints),
        ("seed_packaged_apps", seed_packaged_apps),
        ("reconcile_packaged_app_grants", reconcile_packaged_app_grants),
    )


async def run_packaged_startup_seeds(
    app: Any,
    *,
    get_db_pool: Callable[[], Awaitable[Any]],
    run_startup_seed: Callable[
        [Any, str, Callable[[], Awaitable[None]]], Awaitable[None]
    ],
    seeds: Sequence[StartupSeed] | None = None,
) -> None:
    seed_specs = tuple(seeds) if seeds is not None else _default_startup_seeds()

    for component, seed_func in seed_specs:

        async def _seed(seed_func: SeedCallable = seed_func) -> None:
            pool = await get_db_pool()
            await seed_func(pool)

        await run_startup_seed(app, component, _seed)


def env_flag_enabled(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


async def cancel_background_tasks(*tasks: Any) -> None:
    live_tasks = [task for task in tasks if task is not None]
    for task in live_tasks:
        task.cancel()
    for task in live_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
