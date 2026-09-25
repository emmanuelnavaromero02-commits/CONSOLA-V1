from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def vault_main(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    monkeypatch.setitem(sys.modules, "psycopg2", _module())
    monkeypatch.setitem(sys.modules, "yaml", _module(safe_load=lambda *a, **kw: {}))
    monkeypatch.setitem(
        sys.modules, "app.security",
        _module(get_internal_api_key=lambda: "x" * 64),
    )
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY",
                       "8sXi-0kBYU5DJ5dY7CCRkW7XHJsXxLPmO6r9OYx-3a4=")
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main")


def test_healthz_route_is_declared(vault_main):
    paths = {r.path for r in vault_main.app.router.routes if hasattr(r, "path")}
    assert "/healthz" in paths, (
        f"/healthz missing from vault app routes: {sorted(paths)}"
    )


def test_verify_api_key_short_circuits_on_healthz_path(vault_main):
    class FakeURL:
        path = "/healthz"
    class FakeRequest:
        url = FakeURL()

    vault_main.verify_api_key(
        request=FakeRequest(),
        x_api_key=None,
        x_internal_service=None,
    )


def test_verify_api_key_still_blocks_other_paths(vault_main):
    from fastapi import HTTPException

    class FakeURL:
        path = "/connections/replicon"
    class FakeRequest:
        url = FakeURL()

    with pytest.raises(HTTPException) as exc:
        vault_main.verify_api_key(
            request=FakeRequest(),
            x_api_key=None,
            x_internal_service=None,
        )
    assert exc.value.status_code == 403


def test_verify_api_key_unit_test_path_still_works_without_request(vault_main):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        vault_main.verify_api_key(x_api_key="garbage", x_internal_service="console")
