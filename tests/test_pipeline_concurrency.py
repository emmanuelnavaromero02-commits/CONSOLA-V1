from __future__ import annotations

import asyncio

import pytest

from app.domains.pipeline.concurrency import gather_by_entity


async def _value(value: str, delay: float = 0) -> str:
    if delay:
        await asyncio.sleep(delay)
    return value


async def _boom() -> str:
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_gather_by_entity_returns_results_pending_and_failed():
    results, pending, failed = await gather_by_entity(
        {
            "ready": _value("ok"),
            "slow": _value("late", delay=0.05),
            "failed": _boom(),
        },
        timeout=0.001,
    )

    assert results == {"ready": "ok"}
    assert pending == {"slow"}
    assert failed == {"failed"}


@pytest.mark.asyncio
async def test_gather_by_entity_empty_work_is_noop():
    assert await gather_by_entity({}, timeout=1) == ({}, set(), set())
