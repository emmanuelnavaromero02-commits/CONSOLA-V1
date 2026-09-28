from __future__ import annotations

import hashlib
import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.domains.apps.manifests import manifest_digest  # noqa: E402
from app.services import app_publication, db_pool  # noqa: E402


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-0000-0000-0000-000000000001"
HTML = "<html><body>panel</body></html>"


class _AsyncContext:
    def __init__(self, value, *, on_enter=None, on_exit=None):
        self.value = value
        self.on_enter = on_enter
        self.on_exit = on_exit

    async def __aenter__(self):
        if self.on_enter:
            self.on_enter()
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        if self.on_exit:
            self.on_exit()
        return False


class FakeConn:
    def __init__(self, digest: str):
        self.digest = digest
        self.in_transaction = False
        self.events: list[tuple] = []

    def transaction(self):
        return _AsyncContext(
            self,
            on_enter=lambda: setattr(self, "in_transaction", True),
            on_exit=lambda: setattr(self, "in_transaction", False),
        )

    async def execute(self, sql, *args):
        assert self.in_transaction is True
        assert "set_config('app.tenant_id', $1, true)" in sql
        assert "set_config('app.workspace_id', $2, true)" in sql
        self.events.append(("scope", args))

    async def fetchval(self, sql, *args):
        assert self.in_transaction is True
        assert "register_workspace_app_manifest" in sql
        self.events.append(("register", args))
        return self.digest

    async def fetch(self, sql, *args):
        assert self.in_transaction is True
        assert "reconcile_workspace_app_dataset_grants" in sql
        self.events.append(("reconcile", args))
        return []


@pytest.fixture
def fake_pool(monkeypatch):
    state: dict = {}

    def install(conn):
        class Pool:
            def acquire(self):
                return _AsyncContext(conn)

        async def get_pool():
            return Pool()

        monkeypatch.setattr(app_publication, "get_db_pool", get_pool)
        state["conn"] = conn
        return conn

    return install


@pytest.mark.asyncio
async def test_register_runs_both_definers_in_one_scoped_transaction(fake_pool):
    digest = manifest_digest(
        app_name="ventas_app",
        cartridge_id="workspace",
        datasets=["ventas_diarias"],
        html=HTML,
    )
    conn = fake_pool(FakeConn(digest))
    out = await app_publication.register_workspace_app(
        (TENANT, WORKSPACE), "ventas_app", HTML, ["ventas_diarias"]
    )
    assert out == digest
    assert [e[0] for e in conn.events] == ["scope", "register", "reconcile"]
    assert conn.events[0][1] == (TENANT, WORKSPACE)
    register_args = conn.events[1][1]
    assert register_args[0] == "ventas_app"
    assert register_args[1] == hashlib.sha256(HTML.encode("utf-8")).hexdigest()
    assert register_args[2] == ["ventas_diarias"]
    assert register_args[3] == digest
    assert conn.events[2][1] == ("ventas_app",)


@pytest.mark.asyncio
async def test_register_rejects_bad_inputs_before_touching_the_db(fake_pool):
    conn = fake_pool(FakeConn("x" * 64))
    with pytest.raises(app_publication.AppPublicationError):
        await app_publication.register_workspace_app(("", ""), "a", HTML, ["d"])
    with pytest.raises(app_publication.AppPublicationError):
        await app_publication.register_workspace_app(
            (TENANT, WORKSPACE), "bad-name", HTML, ["d"]
        )
    with pytest.raises(app_publication.AppPublicationError):
        await app_publication.register_workspace_app(
            (TENANT, WORKSPACE), "ok_name", HTML, ["bad-dataset"]
        )
    with pytest.raises(app_publication.AppPublicationError):
        await app_publication.register_workspace_app(
            (TENANT, WORKSPACE), "ok_name", "", ["ventas"]
        )
    with pytest.raises(app_publication.AppPublicationError, match="between 1 and 50"):
        await app_publication.register_workspace_app(
            (TENANT, WORKSPACE), "ok_name", HTML, []
        )
    with pytest.raises(app_publication.AppPublicationError, match="between 1 and 50"):
        await app_publication.register_workspace_app(
            (TENANT, WORKSPACE), "ok_name", HTML, [f"ds_{i}" for i in range(51)]
        )
    assert conn.events == []


@pytest.mark.asyncio
async def test_register_fails_closed_on_digest_mismatch(fake_pool):
    conn = fake_pool(FakeConn("f" * 64))
    with pytest.raises(app_publication.AppPublicationError, match="digest"):
        await app_publication.register_workspace_app(
            (TENANT, WORKSPACE), "ventas_app", HTML, ["ventas_diarias"]
        )
    assert [e[0] for e in conn.events] == ["scope", "register"]


def test_scope_from_user_prefers_active_scope():
    assert app_publication.scope_from_user(
        {
            "tenant_id": "t0",
            "workspace_id": "w0",
            "active_tenant_id": "t1",
            "active_workspace_id": "w1",
        }
    ) == ("t1", "w1")
    assert app_publication.scope_from_user({}) == ("", "")


def test_module_uses_the_shared_console_pool():
    assert app_publication.get_db_pool is db_pool.get_db_pool
