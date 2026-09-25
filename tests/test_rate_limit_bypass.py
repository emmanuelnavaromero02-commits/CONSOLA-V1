from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def _import_main():
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

    assert console_dir in (main_mod.__file__ or ""), (
        f"app.main resolved to {main_mod.__file__!r}, not the console "
        f"tree. sys.path[0]={sys.path[0]!r}. Cartridge harness contamination?"
    )

    return main_mod


@pytest.fixture
def _restore_env():
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
    os.environ["APP_ENV"] = "test"
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is True


def test_bypass_when_app_env_is_testing(_import_main, _restore_env):
    os.environ["APP_ENV"] = "testing"
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is True


def test_enabled_in_production(_import_main, _restore_env):
    os.environ["APP_ENV"] = "production"
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is False


def test_enabled_when_app_env_missing(_import_main, _restore_env):
    os.environ.pop("APP_ENV", None)
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    assert _import_main._rate_limit_disabled() is False


@pytest.mark.parametrize("falsy", ["false", "FALSE", "False", "0", "no", "off", "OFF"])
def test_explicit_disable_via_env_var(_import_main, _restore_env, falsy):
    os.environ["APP_ENV"] = "production"
    os.environ["RATE_LIMIT_ENABLED"] = falsy
    assert _import_main._rate_limit_disabled() is True


@pytest.mark.parametrize("truthy", ["true", "TRUE", "1", "yes", "on", ""])
def test_truthy_or_empty_env_var_keeps_limiter_on_in_production(
    _import_main, _restore_env, truthy,
):
    os.environ["APP_ENV"] = "production"
    os.environ["RATE_LIMIT_ENABLED"] = truthy
    assert _import_main._rate_limit_disabled() is False


def test_explicit_disable_supersedes_app_env_production(_import_main, _restore_env):
    os.environ["APP_ENV"] = "production"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    assert _import_main._rate_limit_disabled() is True


def test_rate_limit_function_short_circuits_when_disabled(_import_main, _restore_env):
    import asyncio
    from unittest.mock import MagicMock

    os.environ["APP_ENV"] = "test"
    request = MagicMock()
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


def test_base_compose_does_not_disable_rate_limit():
    compose = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    import re
    console_block = re.search(
        r"\n  console:\n[\s\S]*?(?=\n  [a-z_-]+:\n|\Z)", compose,
    )
    assert console_block, "console service block not found in dev compose"
    body = console_block.group(0)
    assert 'RATE_LIMIT_ENABLED: "false"' not in body
    assert "RATE_LIMIT_ENABLED: false" not in body


def test_dev_override_hardcodes_rate_limit_disabled():
    dev_compose = REPO / "infra/docker-compose.dev.yml"
    assert dev_compose.exists(), "infra/docker-compose.dev.yml must exist for local E2E"
    src = dev_compose.read_text(encoding="utf-8")
    assert 'RATE_LIMIT_ENABLED: "false"' in src, (
        "infra/docker-compose.dev.yml must carry the explicit local/E2E "
        '``RATE_LIMIT_ENABLED: "false"`` bypass.'
    )


def test_aws_compose_does_not_disable_rate_limit():
    aws_compose = REPO / "infra/terraform/deploy/docker-compose.aws.yml"
    if not aws_compose.exists():
        return
    src = aws_compose.read_text(encoding="utf-8")
    assert 'RATE_LIMIT_ENABLED: "false"' not in src, (
        "AWS prod compose must NOT carry RATE_LIMIT_ENABLED=false. "
        "Removing the line is the prod posture — the helper "
        "defaults to enabled when the var is unset."
    )
    assert "RATE_LIMIT_ENABLED: false" not in src
