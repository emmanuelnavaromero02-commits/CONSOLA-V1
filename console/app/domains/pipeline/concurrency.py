from __future__ import annotations

import asyncio
from typing import Any


async def gather_by_entity(
    work: dict[str, Any], timeout: float
) -> tuple[dict[str, Any], set[str], set[str]]:
    if not work:
        return {}, set(), set()
    tasks_by_task = {asyncio.create_task(coro): entity for entity, coro in work.items()}
    done, pending = await asyncio.wait(tasks_by_task, timeout=max(timeout, 0.001))
    results: dict[str, Any] = {}
    failed: set[str] = set()
    for task in done:
        entity = tasks_by_task[task]
        try:
            results[entity] = task.result()
        except Exception:
            failed.add(entity)
    for task in pending:
        task.cancel()
    return results, {tasks_by_task[task] for task in pending}, failed
