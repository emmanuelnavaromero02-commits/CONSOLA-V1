from __future__ import annotations

import importlib
import sys
import types


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_refresh_token_hash_is_sha256(monkeypatch):
    monkeypatch.setitem(sys.modules, "asyncpg", _module())
    monkeypatch.setitem(sys.modules, "bcrypt", _module())
    sys.modules.pop("app.services.auth", None)
    auth = importlib.import_module("app.services.auth")

    token_hash = auth.hash_refresh_token("refresh-token")

    assert token_hash == auth.hash_refresh_token("refresh-token")
    assert token_hash != "refresh-token"
    assert len(token_hash) == 64
