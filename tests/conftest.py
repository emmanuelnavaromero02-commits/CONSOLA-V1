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

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "SECURITY_CONTEXT_SIGNING_KEY",
    "test_security_context_signing_key_with_more_than_32_chars",
)
os.environ.setdefault("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "test-current")
os.environ.setdefault(
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
    "test_control_room_evidence_signing_key_with_more_than_32_chars",
)


PRIORITY_CARTRIDGES = ("sap_successfactors", "sap_hcm", "sap_s4hana")
ALL_SAP_CARTRIDGES = tuple(
    sorted(
        p.name
        for p in CARTRIDGES_ROOT.iterdir()
        if p.is_dir() and p.name.startswith("sap_")
    )
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def load_cartridge_app(cartridge_id: str) -> ModuleType:
    cart_dir = CARTRIDGES_ROOT / cartridge_id
    assert cart_dir.is_dir(), f"cartridge dir missing: {cart_dir}"

    _purge_app_modules()
    _SIBLINGS = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(cart_dir))

    os.environ["INTERNAL_API_KEY"] = "test-secret-key-not-default"
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg2://test:test@postgres:5432/modecissions"
    )
    os.environ.setdefault(
        "GOLD_DATABASE_URL",
        "postgresql+psycopg2://test:test@postgres_gold:5433/modecissions_gold",
    )
    os.environ.setdefault("MINIO_ACCESS_KEY", "test-minio-access")
    os.environ.setdefault("MINIO_SECRET_KEY", "test-minio-secret")
    if not os.environ.get("FIELD_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet as _Fernet

        os.environ["FIELD_ENCRYPTION_KEY"] = _Fernet.generate_key().decode()
    main = import_module("app.main")

    if hasattr(main, "catalog_service"):
        main.catalog_service._seed_if_empty = lambda: None

    main.app.state.startup_ok = True
    main.app.state.startup_errors = []

    if hasattr(main, "job_runner"):

        async def _ok():
            return None

        main.job_runner.ensure_schema = _ok
        main.job_runner.cleanup_stale = _ok
    return main


@pytest.fixture(autouse=True)
def _restore_sys_path_after_test():
    yield
    _purge_app_modules()
    sys.path[:] = list(_BASE_SYS_PATH)


@pytest.fixture
def cartridges_root() -> Path:
    return CARTRIDGES_ROOT


@pytest.fixture(params=PRIORITY_CARTRIDGES)
def priority_cartridge(request) -> str:
    return request.param
