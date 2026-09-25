from __future__ import annotations

import importlib
import json
import sys
import threading
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


def _load_refinement_main(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "refinement"))
    monkeypatch.setenv(
        "INTERNAL_API_KEY", "refinement_test_internal_key_with_more_than_32_chars"
    )
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

    def fetchall(self):
        return [("httpfs", True, True), ("postgres_scanner", True, True)]


class MissingExtensionDuck(FakeDuck):
    def fetchall(self):
        return [("httpfs", True, True)]


@pytest.mark.asyncio
async def test_refinement_healthz_is_liveness_only(monkeypatch):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(
        psycopg2, "connect", lambda dsn: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    monkeypatch.setattr(
        main.engine, "_conn", lambda: (_ for _ in ()).throw(RuntimeError("duck down"))
    )

    assert await main.healthz() == {"ok": True, "service": "refinement"}


@pytest.mark.asyncio
async def test_refinement_readyz_checks_all_dependencies(monkeypatch):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda dsn: FakeConn())
    monkeypatch.setattr(main.engine, "_conn", lambda: FakeDuck())
    monkeypatch.setattr(main.engine._publication_verifier, "ready", lambda: True)

    resp = await main.readyz()
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"ok": True, "service": "refinement"}


@pytest.mark.asyncio
async def test_refinement_readyz_offloads_blocking_dependency_checks(monkeypatch):
    main = _load_refinement_main(monkeypatch)
    request_thread = threading.get_ident()
    observed = {}

    def readiness_checks():
        observed["thread"] = threading.get_ident()
        return {
            "postgres": "up",
            "duckdb": "up",
            "publication_verifier": "up",
        }

    monkeypatch.setattr(main, "_readiness_checks", readiness_checks)

    resp = await main.readyz()

    assert resp.status_code == 200
    assert observed["thread"] != request_thread


@pytest.mark.asyncio
async def test_refinement_readyz_returns_503_on_postgres_failure(monkeypatch):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(
        psycopg2, "connect", lambda dsn: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    monkeypatch.setattr(main.engine, "_conn", lambda: FakeDuck())
    monkeypatch.setattr(main.engine._publication_verifier, "ready", lambda: True)

    resp = await main.readyz()
    assert resp.status_code == 503
    assert json.loads(resp.body) == {"ok": False, "service": "refinement"}


@pytest.mark.asyncio
async def test_refinement_readyz_fails_closed_when_built_extension_is_missing(
    monkeypatch,
):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda dsn: FakeConn())
    monkeypatch.setattr(main.engine, "_conn", lambda: MissingExtensionDuck())
    monkeypatch.setattr(main.engine._publication_verifier, "ready", lambda: True)
    monkeypatch.setattr(
        main.engine._publication_store,
        "publish",
        lambda *_args: (_ for _ in ()).throw(AssertionError("head must not advance")),
    )
    resp = await main.readyz()
    assert resp.status_code == 503
    assert json.loads(resp.body) == {"ok": False, "service": "refinement"}


@pytest.mark.asyncio
async def test_refinement_readyz_returns_503_when_publication_verifier_is_down(
    monkeypatch,
):
    main = _load_refinement_main(monkeypatch)
    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda dsn: FakeConn())
    monkeypatch.setattr(main.engine, "_conn", lambda: FakeDuck())
    monkeypatch.setattr(main.engine._publication_verifier, "ready", lambda: False)

    resp = await main.readyz()
    assert resp.status_code == 503
    assert json.loads(resp.body) == {"ok": False, "service": "refinement"}
