"""Sprint v1.31 — bootstrap_admin must not accept passwords via argv."""
from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def test_bootstrap_admin_rejects_password_in_argv():
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "console"),
        "BOOTSTRAP_ADMIN_PASSWORD": "StrongPassword12345",
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.bootstrap_admin",
            "admin@example.com",
            "PasswordShouldNotBeInArgv123",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "Refusing password via argv" in result.stderr


def _load_bootstrap_module():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not any(marker in p for marker in SERVICE_PATH_MARKERS)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    return importlib.import_module("app.bootstrap_admin")


def test_bootstrap_admin_uses_env_password(monkeypatch):
    module = _load_bootstrap_module()
    calls: list[dict] = []

    async def get_user_by_email(email):
        return None

    async def create_user(email, password, name=None, role="user"):
        calls.append({"email": email, "password": password, "name": name, "role": role})
        return {"id": 1, "email": email}

    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "StrongPassword12345")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_FULL_NAME", "Admin User")
    monkeypatch.setattr(
        module,
        "_auth",
        SimpleNamespace(get_user_by_email=get_user_by_email, create_user=create_user),
    )

    asyncio.run(module.main("admin@example.com", module._read_password(), os.environ["BOOTSTRAP_ADMIN_FULL_NAME"]))

    assert calls == [{
        "email": "admin@example.com",
        "password": "StrongPassword12345",
        "name": "Admin User",
        "role": "admin",
    }]


def test_env_example_matches_bootstrap_admin_name_contract():
    src = (REPO_ROOT / "infra/.env.example").read_text(encoding="utf-8")
    assert "BOOTSTRAP_ADMIN_FULL_NAME=" in src
    assert "BOOTSTRAP_ADMIN_NAME=" not in src
