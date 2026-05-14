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
    # Remove any other cartridge dir from sys.path so we don't pick up the wrong one
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(cart_dir))

    os.environ["INTERNAL_API_KEY"] = "test-secret-key-not-default"
    main = import_module("app.main")

    # Most cartridge route tests exercise auth/routing in-process, not the
    # live catalog seed. Sprint v1.31 intentionally made seed failures fatal
    # in production startup, so the test harness disables only the startup
    # side effect while dedicated seed tests still call catalog_service
    # directly against the live Postgres stack.
    if hasattr(main, "catalog_service"):
        main.catalog_service._seed_if_empty = lambda: None
    return main


@pytest.fixture
def cartridges_root() -> Path:
    return CARTRIDGES_ROOT


@pytest.fixture(params=PRIORITY_CARTRIDGES)
def priority_cartridge(request) -> str:
    return request.param
