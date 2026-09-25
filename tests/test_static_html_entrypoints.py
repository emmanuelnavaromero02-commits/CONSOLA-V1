from __future__ import annotations

import sys


def _console_client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_direct_static_html_is_not_public_entrypoint(monkeypatch):
    client = _console_client(monkeypatch)

    for path in (
        "/static/cartridges.html",
        "/static/viewers/vault.html",
        "/static/console-next/index.html",
    ):
        response = client.get(path)
        assert response.status_code == 404, f"{path} must not be served as a raw static page"
        assert response.json() == {"detail": "not found"}


def test_static_assets_and_canonical_public_pages_still_load(monkeypatch):
    client = _console_client(monkeypatch)

    assert client.get("/static/js/theme-switch.js").status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200
    assert client.get("/forgot-password").status_code == 200
