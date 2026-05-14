"""Sprint v1.32 — production console must require Redis rate limiting."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _load_rate_limiter(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "console"))
    return importlib.import_module("app.services.rate_limiter")


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def test_redis_url_required_in_production(monkeypatch):
    module = _load_rate_limiter(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    module.reset_rate_limiter()

    with pytest.raises(RuntimeError, match="REDIS_URL is required in production"):
        module.get_rate_limiter()


def test_in_memory_still_allowed_in_development(monkeypatch):
    module = _load_rate_limiter(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("REDIS_URL", raising=False)
    module.reset_rate_limiter()

    assert isinstance(module.get_rate_limiter(), module.InMemoryRateLimiter)


def test_console_lifespan_initializes_rate_limiter():
    source = (Path(__file__).resolve().parents[1] / "console/app/main.py").read_text()

    assert "async def lifespan" in source
    assert "get_rate_limiter()" in source.split("async def lifespan", 1)[1].split("await mcp_registry.startup()", 1)[0]
