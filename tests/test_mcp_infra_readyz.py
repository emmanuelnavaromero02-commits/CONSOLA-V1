from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
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


def _load_mcp_main(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "mcp-infra"))
    env = {
        "APP_ENV": "test",
        "INTERNAL_API_KEY": "mcp_infra_test_internal_key_with_more_than_32_chars",
        "AIRFLOW_USER": "airflow",
        "AIRFLOW_PASSWORD": "airflow-pass",
        "PG_PASSWORD": "pg-pass",
        "PG_GOLD_USER": "gold",
        "PG_GOLD_PASSWORD": "gold-pass",
        "SUPERSET_USER": "superset",
        "SUPERSET_PASSWORD": "superset-pass",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.import_module("app.main")


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        return (1,)


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return FakeCursor()


def _body(response):
    return json.loads(response.body)


def test_mcp_infra_healthz_is_liveness_only(monkeypatch):
    main = _load_mcp_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(main.registry, "list_tools", lambda: (_ for _ in ()).throw(RuntimeError("registry down")))
    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))

    assert main.healthz() == {"ok": True, "service": "mcp-infra"}


def test_mcp_infra_readyz_checks_registry_and_databases(monkeypatch):
    main = _load_mcp_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(main.registry, "list_tools", lambda: [{"name": "ok"}])
    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: FakeConn())

    resp = main.readyz()
    assert resp.status_code == 200
    assert _body(resp) == {"ok": True, "service": "mcp-infra"}


def test_mcp_infra_readyz_returns_503_when_registry_empty(monkeypatch):
    main = _load_mcp_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(main.registry, "list_tools", lambda: [])
    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: FakeConn())

    resp = main.readyz()
    assert resp.status_code == 503
    assert _body(resp) == {"ok": False, "service": "mcp-infra"}


def test_mcp_infra_readyz_returns_503_when_postgres_down(monkeypatch):
    main = _load_mcp_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(main.registry, "list_tools", lambda: [{"name": "ok"}])
    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))

    resp = main.readyz()
    assert resp.status_code == 503
    assert _body(resp) == {"ok": False, "service": "mcp-infra"}
