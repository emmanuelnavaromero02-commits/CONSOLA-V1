from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

SeedCallable = Callable[[Any], Awaitable[None]]
StartupSeed = tuple[str, SeedCallable]


def _default_startup_seeds() -> tuple[StartupSeed, ...]:
    from app.services.seed_dag_sources import seed_missing_dag_sources
    from app.services.seed_packaged_apps import seed_packaged_apps
    from app.services.seed_packaged_datasets import seed_packaged_datasets
    from app.services.seed_packaged_hints import seed_packaged_hints

    return (
        ("seed_missing_dag_sources", seed_missing_dag_sources),
        ("seed_packaged_datasets", seed_packaged_datasets),
        ("seed_packaged_hints", seed_packaged_hints),
        ("seed_packaged_apps", seed_packaged_apps),
    )


async def run_packaged_startup_seeds(
    app: Any,
    *,
    get_db_pool: Callable[[], Awaitable[Any]],
    run_startup_seed: Callable[[Any, str, Callable[[], Awaitable[None]]], Awaitable[None]],
    seeds: Sequence[StartupSeed] | None = None,
) -> None:
    seed_specs = tuple(seeds) if seeds is not None else _default_startup_seeds()

    for component, seed_func in seed_specs:
        async def _seed(seed_func: SeedCallable = seed_func) -> None:
            pool = await get_db_pool()
            await seed_func(pool)

        await run_startup_seed(app, component, _seed)
