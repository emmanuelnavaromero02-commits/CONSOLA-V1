"""
Pytest harness for the cartridge audit suite.

Each cartridge ships with its own ``app`` package (``cartridges/<id>/app``).
The packages have the same name across cartridges, so we can't import them
all in a single Python process. Each test that needs to import a cartridge
calls :func:`load_cartridge_app` which swaps ``sys.path`` and clears the
``app.*`` modules from ``sys.modules`` first.
"""
from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES_ROOT = REPO_ROOT / "cartridges"
CONSOLE_ROOT = REPO_ROOT / "console"
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))
_BASE_SYS_PATH = list(sys.path)

# v1.43.2 (Codex P1-2): the production code now defaults APP_ENV to
# ``production`` so unset envs fail closed. The test harness explicitly
# opts in to dev/test mode — mirroring the compose file pattern — so
# importing console/app/services/auth.py + vault/app/main.py at
# collection time doesn't trip the production pair-key check.
os.environ.setdefault("APP_ENV", "test")


PRIORITY_CARTRIDGES = ("sap_successfactors", "sap_hcm", "sap_s4hana")
ALL_SAP_CARTRIDGES = tuple(
    sorted(
        p.name for p in CARTRIDGES_ROOT.iterdir()
        if p.is_dir() and p.name.startswith("sap_")
    )
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def load_cartridge_app(cartridge_id: str) -> ModuleType:
    """Import the ``app.main`` of ``cartridge_id`` after isolating sys.path."""
    cart_dir = CARTRIDGES_ROOT / cartridge_id
    assert cart_dir.is_dir(), f"cartridge dir missing: {cart_dir}"

    _purge_app_modules()
    # Remove any other cartridge dir from sys.path so we don't pick up the wrong one.
    # Also strip console/refinement/vault/workspace, which previous tests may have
    # inserted to import their own ``app.*`` packages. Those regular packages
    # (with __init__.py) otherwise mask the cartridge's namespace package.
    _SIBLINGS = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(cart_dir))

    os.environ["INTERNAL_API_KEY"] = "test-secret-key-not-default"
    # Sprint v1.33 (audit B1): SAP cartridge protection_service refuses
    # to import without a valid Fernet FIELD_ENCRYPTION_KEY. The test
    # harness provides one so existing route/import tests keep working;
    # tests that exercise the missing/invalid branches use monkeypatch.
    if not os.environ.get("FIELD_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet as _Fernet
        os.environ["FIELD_ENCRYPTION_KEY"] = _Fernet.generate_key().decode()
    main = import_module("app.main")

    # Most cartridge route tests exercise auth/routing in-process, not the
    # live catalog seed. Sprint v1.31 intentionally made seed failures fatal
    # in production startup, so the test harness disables only the startup
    # side effect while dedicated seed tests still call catalog_service
    # directly against the live Postgres stack.
    if hasattr(main, "catalog_service"):
        main.catalog_service._seed_if_empty = lambda: None

    # v1.43.2 (Codex P1-5): /health now requires app.state.startup_ok=True
    # for a 200. Pre-seed a clean state for tests that build a TestClient
    # WITHOUT a ``with`` block (so lifespan never runs).
    main.app.state.startup_ok = True
    main.app.state.startup_errors = []

    # v1.43.2 (LLM R1 hardening): tests that DO enter the TestClient
    # ``with`` block run the lifespan end-to-end, which calls
    # job_runner.ensure_schema + cleanup_stale against a real Postgres.
    # Without a DB, the lifespan records errors and flips startup_ok
    # back to False — and /mcp/* now refuses traffic with 503. Stub
    # job_runner so the lifespan completes cleanly. The dedicated
    # fail-fast tests (tests/test_cartridge_startup_fail_fast.py) own
    # the failure-path coverage and apply their own monkeypatch.
    if hasattr(main, "job_runner"):
        async def _ok():
            return None
        main.job_runner.ensure_schema = _ok
        main.job_runner.cleanup_stale = _ok
    return main


@pytest.fixture(autouse=True)
def _restore_sys_path_after_test():
    """Root cartridge tests temporarily replace sys.path to import each
    cartridge's ``app`` namespace package. Restore the original import path
    after every test so a combined ``pytest tests/ console/tests/`` run does
    not leave console's own ``app`` package unreachable."""
    yield
    _purge_app_modules()
    sys.path[:] = list(_BASE_SYS_PATH)


@pytest.fixture
def cartridges_root() -> Path:
    return CARTRIDGES_ROOT


@pytest.fixture(params=PRIORITY_CARTRIDGES)
def priority_cartridge(request) -> str:
    return request.param
