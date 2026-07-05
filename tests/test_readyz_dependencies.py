from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import readyz_dependencies


class _ReadyzSuccessClient:
    calls: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        return SimpleNamespace(status_code=200, url=url)


class _ReadyzErrorClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        raise httpx.ReadTimeout("slow dependency")


@pytest.fixture(autouse=True)
def _clear_readyz_cache(monkeypatch):
    readyz_dependencies.READYZ_DEPENDENCY_CACHE.clear()
    _ReadyzSuccessClient.calls.clear()
    monkeypatch.setenv("READYZ_DEPENDENCY_CACHE_TTL_SECONDS", "0.5")
    monkeypatch.setenv("READYZ_DEPENDENCY_STALE_TTL_SECONDS", "60")


@pytest.mark.asyncio
async def test_dependency_health_marks_missing_url_down():
    assert await readyz_dependencies.dependency_health("refinement", "") == {
        "status": "down",
        "error": "missing_url",
    }


@pytest.mark.asyncio
async def test_dependency_health_uses_headers_and_recent_cache():
    now = {"value": 100.0}

    def _headers(server: str) -> dict[str, str]:
        return {"x-server": server}

    first = await readyz_dependencies.dependency_health(
        "refinement",
        "http://refinement/healthz",
        "console",
        header_factory=_headers,
        http_client_factory=_ReadyzSuccessClient,
        monotonic=lambda: now["value"],
    )
    now["value"] = 100.2
    second = await readyz_dependencies.dependency_health(
        "refinement",
        "http://refinement/healthz",
        "console",
        header_factory=_headers,
        http_client_factory=_ReadyzSuccessClient,
        monotonic=lambda: now["value"],
    )

    assert first == {"status": "up", "code": 200}
    assert second == {"status": "up", "code": 200}
    assert len(_ReadyzSuccessClient.calls) == 1
    assert _ReadyzSuccessClient.calls[0]["kwargs"]["headers"] == {"x-server": "console"}


@pytest.mark.asyncio
async def test_dependency_health_serves_stale_up_signal_for_transient_error():
    now = {"value": 100.0}
    assert await readyz_dependencies.dependency_health(
        "refinement",
        "http://refinement/healthz",
        http_client_factory=_ReadyzSuccessClient,
        monotonic=lambda: now["value"],
    ) == {"status": "up", "code": 200}

    now["value"] = 102.0
    result = await readyz_dependencies.dependency_health(
        "refinement",
        "http://refinement/healthz",
        http_client_factory=_ReadyzErrorClient,
        monotonic=lambda: now["value"],
    )

    assert result == {
        "status": "up",
        "code": 200,
        "cached": True,
        "stale": True,
        "last_error": "ReadTimeout",
    }


@pytest.mark.asyncio
async def test_dependency_health_expires_stale_signal(monkeypatch):
    monkeypatch.setenv("READYZ_DEPENDENCY_STALE_TTL_SECONDS", "10")
    now = {"value": 100.0}
    await readyz_dependencies.dependency_health(
        "refinement",
        "http://refinement/healthz",
        http_client_factory=_ReadyzSuccessClient,
        monotonic=lambda: now["value"],
    )

    now["value"] = 120.0

    assert await readyz_dependencies.dependency_health(
        "refinement",
        "http://refinement/healthz",
        http_client_factory=_ReadyzErrorClient,
        monotonic=lambda: now["value"],
    ) == {
        "status": "down",
        "error": "ReadTimeout",
    }
