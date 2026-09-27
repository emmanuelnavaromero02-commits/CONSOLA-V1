from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import db_scope, seed_packaged_apps


class _Tx:
    def __init__(self, log: list):
        self.log = log

    async def __aenter__(self):
        self.log.append(("begin",))
        return self

    async def __aexit__(self, exc_type, *_):
        self.log.append(("rollback",) if exc_type else ("commit",))
        return False


class _Conn:
    def __init__(self, log: list):
        self.log = log

    def transaction(self):
        return _Tx(self.log)

    async def execute(self, sql, *args):
        self.log.append(("execute", sql, args))


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        return False


class _Pool:
    def __init__(self):
        self.log: list = []
        self.conn = _Conn(self.log)

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "super_admin", "admin"])
async def test_platform_admin_db_marks_the_transaction_as_platform(role):
    pool = _Pool()
    async with db_scope.platform_admin_db(pool, {"id": 1, "role": role}) as conn:
        assert conn is pool.conn
    assert pool.log[0] == ("begin",)
    assert pool.log[1] == ("execute", db_scope.SET_PLATFORM_SCOPE_SQL, ())
    assert pool.log[-1] == ("commit",)
    sql = db_scope.SET_PLATFORM_SCOPE_SQL
    assert "set_config('app.workspace_id', '', true)" in sql
    assert "set_config('app.tenant_id', '', true)" in sql
    assert "set_config('app.platform_admin', 'true', true)" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        {},
        {"role": "security_admin"},
        {"role": "auditor"},
        {"role": "tenant_admin"},
        {"role": "workspace_admin"},
        {"role": "user", "workspace_role": "admin"},
        {"role": "Super_Admin"},
    ],
)
async def test_platform_admin_db_refuses_everyone_else(user):
    pool = _Pool()
    with pytest.raises(HTTPException) as error:
        async with db_scope.platform_admin_db(pool, user):
            pytest.fail("no platform connection expected")
    assert error.value.status_code == 403
    assert pool.log == []


@pytest.mark.asyncio
async def test_system_platform_db_requires_a_named_purpose():
    pool = _Pool()
    async with db_scope.system_platform_db(pool, purpose="seed_packaged_apps") as conn:
        assert conn is pool.conn
    assert pool.log[1] == ("execute", db_scope.SET_PLATFORM_SCOPE_SQL, ())
    for purpose in ("", "x", "Seed Apps", "a" * 80, None):
        with pytest.raises(ValueError):
            async with db_scope.system_platform_db(_Pool(), purpose=purpose):
                pass


@pytest.mark.asyncio
async def test_workspace_scope_never_carries_the_platform_marker():
    pool = _Pool()
    user = {"role": "super_admin", "active_tenant_id": "t-1", "active_workspace_id": "w-1"}
    async with db_scope.scoped_db_for_user(pool, user):
        pass
    executed = [entry for entry in pool.log if entry[0] == "execute"]
    assert executed == [("execute", db_scope.SET_SCOPE_SQL, ("t-1", "w-1"))]
    assert "platform_admin" not in db_scope.SET_SCOPE_SQL


@pytest.mark.asyncio
async def test_packaged_app_seeder_writes_under_the_system_platform_scope(monkeypatch, tmp_path):
    apps = tmp_path / "hubspot" / "apps"
    apps.mkdir(parents=True)
    (apps / "pipeline.html").write_text("<html>/api/data/deals_pipeline</html>", encoding="utf-8")
    monkeypatch.setattr(seed_packaged_apps, "_REGISTRY", tmp_path)
    pool = _Pool()

    await seed_packaged_apps.seed_packaged_apps(pool)

    assert pool.log[0] == ("begin",)
    assert pool.log[1] == ("execute", db_scope.SET_PLATFORM_SCOPE_SQL, ())
    writes = [entry for entry in pool.log if entry[0] == "execute"][1:]
    assert writes and all("analytic_apps" in entry[1] for entry in writes)
    assert pool.log[-1] == ("commit",)
