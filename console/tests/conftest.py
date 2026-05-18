from __future__ import annotations

import os
import sys
from importlib import import_module
from unittest.mock import Mock

import pytest


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test_internal_api_key_with_more_than_32_chars")


def _looks_like_auth_stub(module: object) -> bool:
    return (
        module is None
        or isinstance(module, Mock)
        or getattr(module, "__name__", "") == "stub"
        or not hasattr(module, "pool")
    )


sys.modules.pop("app.services.auth", None)
CANONICAL_AUTH = import_module("app.services.auth")


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


_restore_real_auth_module()


@pytest.fixture(autouse=True)
def _isolate_console_auth_stubs():
    _restore_real_auth_module()
    yield
    _restore_real_auth_module()
