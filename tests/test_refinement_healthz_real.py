"""Sprint v1.32 — refinement /healthz must check real dependencies."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


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


def _load_refinement_main(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "refinement"))
    monkeypatch.setenv("INTERNAL_API_KEY", "refinement_test_internal_key_with_more_than_32_chars")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minio-secret")
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


class FakeDuck:
    def execute(self, sql):
        self.sql = sql
        return self

    def fetchone(self):
        return (1,)


@pytest.mark.asyncio
async def test_refinement_healthz_checks_postgres_and_duckdb(monkeypatch):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda dsn: FakeConn())
    monkeypatch.setattr(main.engine, "_conn", lambda: FakeDuck())

    assert await main.healthz() == {
        "ok": True,
        "service": "refinement",
        "postgres": "ok",
        "duckdb": "ok",
    }


@pytest.mark.asyncio
async def test_refinement_healthz_returns_503_on_dependency_failure(monkeypatch):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda dsn: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(HTTPException) as exc:
        await main.healthz()

    assert exc.value.status_code == 503
