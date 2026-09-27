from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock

import pytest

CONSOLE_ROOT = Path(__file__).resolve().parents[1]
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "0")
os.environ.setdefault(
    "INTERNAL_API_KEY", "test_internal_api_key_with_more_than_32_chars"
)
os.environ.setdefault(
    "SECURITY_CONTEXT_SIGNING_KEY",
    "test_security_context_signing_key_with_more_than_32_chars",
)
os.environ.setdefault("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "test-current")
os.environ.setdefault(
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
    "test_control_room_evidence_signing_key_with_more_than_32_chars",
)


def _looks_like_auth_stub(module: object) -> bool:
    return (
        module is None
        or isinstance(module, Mock)
        or getattr(module, "__name__", "") == "stub"
        or not hasattr(module, "pool")
    )


sys.modules.pop("app.services.auth", None)
CANONICAL_AUTH = import_module("app.services.auth")
CANONICAL_DEPENDENCIES = import_module("app.dependencies")


def _restore_real_auth_module() -> None:
    current = sys.modules.get("app.services.auth")
    if current is not CANONICAL_AUTH or _looks_like_auth_stub(current):
        sys.modules["app.services.auth"] = CANONICAL_AUTH
    auth = CANONICAL_AUTH

    services_pkg = import_module("app.services")
    if getattr(services_pkg, "auth", None) is not auth:
        setattr(services_pkg, "auth", auth)

    for module_name in (
        "app.services.operations_service",
        "app.services.settings_service",
    ):
        module = sys.modules.get(module_name)
        if module is not None and _looks_like_auth_stub(getattr(module, "auth", None)):
            setattr(module, "auth", auth)


def _restore_real_dependencies_module() -> None:
    if sys.modules.get("app.dependencies") is not CANONICAL_DEPENDENCIES:
        sys.modules["app.dependencies"] = CANONICAL_DEPENDENCIES
    app_pkg = import_module("app")
    if getattr(app_pkg, "dependencies", None) is not CANONICAL_DEPENDENCIES:
        setattr(app_pkg, "dependencies", CANONICAL_DEPENDENCIES)


_restore_real_auth_module()
_restore_real_dependencies_module()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_pipeline_automation: run the real trigger preflight and "
        "pre-reservation recovery instead of the inert test stubs",
    )


@pytest.fixture(autouse=True)
def _inert_pipeline_trigger_automation(request, monkeypatch):
    """Keep Airflow preflight/recovery out of tests that pin exact MCP calls."""
    if request.node.get_closest_marker("real_pipeline_automation"):
        yield
        return
    preflight = import_module("app.domains.pipeline.trigger_preflight")
    recovery = import_module("app.domains.pipeline.stuck_run_recovery_service")

    async def inert_preflight(dag_id, _user, **_kwargs):
        return preflight.DagPreflight(str(dag_id), "unknown", None, False, checked=False)

    async def inert_recovery(_user, **_kwargs):
        return None

    monkeypatch.setattr(preflight, "run_manual_trigger_preflight", inert_preflight)
    monkeypatch.setattr(recovery, "recover_before_reservation", inert_recovery)
    yield


@pytest.fixture(autouse=True)
def _isolate_console_auth_stubs(monkeypatch):
    _restore_real_auth_module()
    _restore_real_dependencies_module()
    scoped_reads = import_module("app.domains.data_platform.scoped_reads")

    async def stable_publication_epoch(_user):
        return "test-publication-head"

    monkeypatch.setattr(scoped_reads, "publication_epoch", stable_publication_epoch)
    yield
    _restore_real_auth_module()
    _restore_real_dependencies_module()
