"""Sprint v1.44.3.3 Task A — rate-limit env-var bypass contract.

The R-Mac-4 same-origin proxy made every E2E request surface to
FastAPI as the same docker-internal IP, so concurrent test
logins shared one per-IP bucket and exhausted the 8/300s
``/auth/login`` quota long before a real user could. This file
pins the bypass that lets test harnesses skip the limiter
without weakening production.

The bypass fires in exactly two scenarios — both
test-harness-only:

  1. ``RATE_LIMIT_ENABLED=false`` (any case) — explicit opt-out.
  2. ``APP_ENV`` ∈ {``test``, ``testing``}             — automatic.

Production deployments default ``APP_ENV`` to ``production`` and
leave ``RATE_LIMIT_ENABLED`` unset, so the limiter stays on.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def _import_main():
    """Import console/app/main with a clean env so the module's
    import-time security checks pass.

    The cartridge harness in conftest.py mutates sys.path on every
    cartridge test — evicting the console dir and inserting a
    cartridge dir at position 0 — and a cartridge's own
    ``app.main`` may already be cached in sys.modules. We:

      1. force ``APP_ENV=test`` + the security-check env vars
         (defensive against any prior monkeypatch leak),
      2. strip every cartridge/sibling dir out of sys.path,
      3. (re-)insert the console dir at position 0,
      4. evict every cached ``app.*`` so the next import is fresh,
      5. import ``app.main`` and assert it came from the console
         tree (so a stale cartridge import doesn't silently
         shadow us).

    Function-scoped so each test gets a clean import — cheap
    because main.py is already on disk; the import takes a few
    hundred ms."""
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    os.environ.setdefault(
        "FIELD_ENCRYPTION_KEY",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
    )

    console_dir = str(REPO / "console")
    _SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS) and p != console_dir]
    sys.path.insert(0, console_dir)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    import app.main as main_mod  # noqa: E402

    # Sanity check — if a cartridge's app.main got loaded by
    # accident, fail loud with the file path instead of producing
    # a cryptic AttributeError later.
    assert console_dir in (main_mod.__file__ or ""), (
        f"app.main resolved to {main_mod.__file__!r}, not the console "
        f"tree. sys.path[0]={sys.path[0]!r}. Cartridge harness contamination?"
    )

    return main_mod


@pytest.fixture
def _restore_env():
    """Snapshot env on entry, restore on exit so tests can mutate
    APP_ENV / RATE_LIMIT_ENABLED without polluting siblings."""
    snapshot = {
        k: os.environ.get(k)
        for k in ("APP_ENV", "RATE_LIMIT_ENABLED")
    }
    try:
        yield
    finally:
        for k, v in snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_bypass_when_app_env_is_test(_import_main, _restore_env):
    """The default test harness uses APP_ENV=test — bypass must
    fire automatically so the suite doesn't have to bother with
    RATE_LIMIT_ENABLED."""
    os.environ["APP_ENV"] = "test"
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is True


def test_bypass_when_app_env_is_testing(_import_main, _restore_env):
    """``testing`` is the django/pytest-django convention; we
    honour it too so nobody trips on the alias."""
    os.environ["APP_ENV"] = "testing"
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is True


def test_enabled_in_production(_import_main, _restore_env):
    """Prod deployments must NOT bypass — that's the whole point
    of the brute-force protection."""
    os.environ["APP_ENV"] = "production"
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is False


def test_enabled_when_app_env_missing(_import_main, _restore_env):
    """Default for an unset APP_ENV is ``production`` (see
    console/app/security.py). Limiter must stay ON."""
    os.environ.pop("APP_ENV", None)
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is False


@pytest.mark.parametrize("falsy", ["false", "FALSE", "False", "0", "no", "off", "OFF"])
def test_explicit_disable_via_env_var(_import_main, _restore_env, falsy):
    """RATE_LIMIT_ENABLED=false overrides everything — even in
    APP_ENV=production. That's the on-call escape hatch for
    incident response when the limiter itself is in the way."""
    os.environ["APP_ENV"] = "production"
    os.environ["RATE_LIMIT_ENABLED"] = falsy
    assert _import_main._rate_limit_disabled() is True


@pytest.mark.parametrize("truthy", ["true", "TRUE", "1", "yes", "on", ""])
def test_truthy_or_empty_env_var_keeps_limiter_on_in_production(
    _import_main, _restore_env, truthy,
):
    """Anything other than the explicit falsy set must NOT
    bypass. An accidental ``RATE_LIMIT_ENABLED=1`` should keep
    the limiter ON."""
    os.environ["APP_ENV"] = "production"
    os.environ["RATE_LIMIT_ENABLED"] = truthy
    assert _import_main._rate_limit_disabled() is False


def test_explicit_disable_supersedes_app_env_production(_import_main, _restore_env):
    """Belt-and-braces: production + explicit disable → bypass.
    Documented escape hatch, not the default."""
    os.environ["APP_ENV"] = "production"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    assert _import_main._rate_limit_disabled() is True


def test_rate_limit_function_short_circuits_when_disabled(_import_main, _restore_env):
    """End-to-end: ``_rate_limit()`` itself must return without
    hitting the limiter backend when the bypass fires. Otherwise
    we'd still pay the Redis round-trip on every request."""
    import asyncio
    from unittest.mock import MagicMock

    os.environ["APP_ENV"] = "test"
    request = MagicMock()
    # No limiter mock — if the bypass fails the call will try to
    # reach the real limiter backend (and might pass anyway), so
    # we patch get_rate_limiter to raise to make a regression
    # loud.
    sentinel_called = {"hit": False}

    def _explode():
        sentinel_called["hit"] = True
        raise AssertionError(
            "get_rate_limiter() called despite bypass — _rate_limit "
            "didn't short-circuit on APP_ENV=test"
        )

    original = _import_main.get_rate_limiter
    _import_main.get_rate_limiter = _explode
    try:
        asyncio.run(_import_main._rate_limit(request, "/auth/login", "x@y.z"))
    finally:
        _import_main.get_rate_limiter = original
    assert sentinel_called["hit"] is False
