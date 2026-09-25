from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_bootstrap_admin():
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "console"))
    return importlib.import_module("app.bootstrap_admin")


def test_bootstrap_rejects_env_example_placeholder(monkeypatch):
    ba = _load_bootstrap_admin()
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "ChangeMeFirstBoot123!")
    with pytest.raises(RuntimeError) as exc:
        ba._read_password()
    assert "placeholder" in str(exc.value).lower()


def test_bootstrap_rejects_common_placeholders(monkeypatch):
    ba = _load_bootstrap_admin()
    for placeholder in ("change-me", "changeme", "password", "admin"):
        monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", placeholder)
        with pytest.raises(RuntimeError) as exc:
            ba._read_password()
        assert "placeholder" in str(exc.value).lower(), (
            f"placeholder {placeholder!r} not refused"
        )


def test_bootstrap_rejects_password_shorter_than_12_chars(monkeypatch):
    ba = _load_bootstrap_admin()
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "short1!")
    with pytest.raises(RuntimeError) as exc:
        ba._read_password()
    assert "12 characters" in str(exc.value)


def test_bootstrap_accepts_strong_non_placeholder_password(monkeypatch):
    ba = _load_bootstrap_admin()
    strong = "uNiCorN-rAndOm-pa$$w0rd-yes"
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", strong)
    assert ba._read_password() == strong


def test_bootstrap_rejects_empty_password(monkeypatch):
    ba = _load_bootstrap_admin()
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    with pytest.raises(RuntimeError) as exc:
        ba._read_password()
    assert "empty" in str(exc.value).lower()


def test_bootstrap_trims_whitespace_before_placeholder_check(monkeypatch):
    ba = _load_bootstrap_admin()
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "ChangeMeFirstBoot123!  ")
    with pytest.raises(RuntimeError) as exc:
        ba._read_password()
    assert "placeholder" in str(exc.value).lower()
