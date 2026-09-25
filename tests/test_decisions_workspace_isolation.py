from __future__ import annotations

# fmt: off

import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.console_route_source import console_route_source


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_DIR = REPO_ROOT / "console"
CONSOLE_MAIN = CONSOLE_DIR / "app" / "main.py"

SERVICE_PATH_MARKERS = (
    "/cartridges/",
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
def _clean_app_modules():
    _purge_app_modules()
    saved_path = list(sys.path)
    yield
    sys.path[:] = saved_path
    _purge_app_modules()


def test_decision_select_queries_are_workspace_scoped():
    src = console_route_source()

    assert "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2" in src, (
        "_dec_load_with_visibility must include workspace_id in the WHERE"
    )

    assert "workspace_clause = f\"workspace_id = {ws_param}\"" in src or (
        "workspace_id = " in src and "_dec_visible_clause" in src
    ), "_dec_visible_clause must emit workspace_id = $N"


def test_decision_insert_writes_workspace_id():
    src = console_route_source()
    assert (
        "(title, description, commitment_date, kpis, created_by_id, "
        "assignee_id, visibility, workspace_id)"
    ) in src, "POST /api/decisions must insert workspace_id"


def test_decision_update_is_workspace_scoped():
    src = console_route_source()
    assert (
        "WHERE id = {decision_ref} AND workspace_id = {workspace_ref} "
        "RETURNING *"
    ) in src, "PATCH /api/decisions/{id} must include workspace_id"


def test_decision_delete_is_workspace_scoped():
    src = console_route_source()
    assert (
        'DELETE FROM decisions WHERE id = $1 AND workspace_id = $2'
    ) in src, "DELETE /api/decisions/{id} must include workspace_id"


def test_current_workspace_id_helper_exists():
    src = CONSOLE_MAIN.read_text(encoding="utf-8")
    assert "def _current_workspace_id(" in src, (
        "console must expose a _current_workspace_id helper that "
        "mirrors workspace/app/main.py:_current_workspace_id"
    )


def test_decisions_workspace_id_migration_still_exists():
    src = (REPO_ROOT / "infra/init/33_decisions_workspace_id.sql").read_text()
    assert "ALTER TABLE decisions" in src
    assert "ADD COLUMN IF NOT EXISTS workspace_id" in src
    assert "idx_decisions_workspace_id" in src


def test_all_console_decision_action_surfaces_use_durable_idempotency():
    main_source = CONSOLE_MAIN.read_text(encoding="utf-8")
    router_source = (
        CONSOLE_DIR / "app" / "routers" / "v1" / "admin_decisions.py"
    ).read_text(encoding="utf-8")
    for source in (main_source, router_source):
        action_route = source.split(
            '"/api/decisions/{decision_id}/actions"', 1
        )[1]
        assert "_decision_idempotency_key(request)" in action_route
        assert "_create_decision_action_idempotently(" in action_route

    migration = (
        REPO_ROOT / "infra" / "init" / "99zzz_workspace_decision_idempotency.sql"
    ).read_text(encoding="utf-8")
    assert "FOR ALL TO omega_workspace, omega_console" in migration
    assert "GRANT SELECT, INSERT, UPDATE ON workspace_decision_idempotency" in migration
    assert "TO omega_workspace, omega_console" in migration


def test_all_console_decision_clients_send_fresh_idempotency_key():
    client_sources = (
        CONSOLE_DIR / "app" / "static" / "js" / "decisions.js",
        CONSOLE_DIR / "app" / "static" / "js" / "workspace.js",
        REPO_ROOT / "console-next" / "src" / "lib" / "admin-surfaces.ts",
    )
    for path in client_sources:
        source = path.read_text(encoding="utf-8")
        assert "randomUUID" in source, path
        assert "Idempotency-Key" in source, path


_CONSOLE_TEST_ENV = {
    "INTERNAL_API_KEY": "test-internal-api-key-not-default-aaaaaaaaaaaaaaaaa",
    "JWT_SECRET_KEY": "test-jwt-key-not-default-bbbbbbbbbbbbbbbbbbbbbbbbb",
    "MINIO_SECRET_KEY": "minioadmin",
    "POSTGRES_PASSWORD": "ci",
    "ADMIN_EMAIL": "admin@example.com",
    "ADMIN_PASSWORD": "ci-admin-password",
}


class _FakePool:

    def __init__(self, fetchrow_result=None, fetch_result=None):
        self.calls: list[tuple[str, str, tuple]] = []
        self._fetchrow_result = fetchrow_result
        self._fetch_result = fetch_result or []

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        return self._fetchrow_result

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", sql, params))
        if "FROM control_room_items" in sql or "FROM decision_actions" in sql:
            return []
        return self._fetch_result

    async def execute(self, sql, *params):
        self.calls.append(("execute", sql, params))


def _non_scope_calls(fake: _FakePool) -> list[tuple[str, str, tuple]]:
    return [
        call
        for call in fake.calls
        if "set_config('app.tenant_id'" not in call[1]
    ]


def _assert_db_scope(fake: _FakePool, workspace_id: str) -> None:
    scope_calls = [
        call
        for call in fake.calls
        if "set_config('app.tenant_id'" in call[1]
        and "set_config('app.workspace_id'" in call[1]
    ]
    assert scope_calls, "scoped endpoints must set Postgres app.tenant_id/app.workspace_id"
    assert scope_calls[0][2][1] == workspace_id


@pytest.fixture
def console_main(monkeypatch):
    for k, v in _CONSOLE_TEST_ENV.items():
        monkeypatch.setenv(k, v)
    saved_path = list(sys.path)
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(CONSOLE_DIR))
    _purge_app_modules()
    main = importlib.import_module("app.main")
    yield main
    sys.path[:] = saved_path


@pytest.mark.asyncio
async def test_list_decisions_filters_by_active_workspace(console_main, monkeypatch):
    fake = _FakePool(fetch_result=[])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}
    result = await console_main.api_decisions_list(status="", overdue="", user=user)
    assert result == {"decisions": []}

    _assert_db_scope(fake, "workspace-A")
    op, sql, params = _non_scope_calls(fake)[0]
    assert op == "fetch"
    assert "workspace_id = $1" in sql
    assert params[0] == "workspace-A"


@pytest.mark.asyncio
async def test_list_decisions_returns_empty_without_active_workspace(console_main, monkeypatch):
    fake = _FakePool(fetch_result=[])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user"}
    result = await console_main.api_decisions_list(status="", overdue="", user=user)
    assert result == {"decisions": []}
    assert fake.calls == [], "missing-workspace path must short-circuit before DB"


@pytest.mark.asyncio
async def test_load_with_visibility_returns_none_without_active_workspace(console_main):
    user = {"id": 7, "role": "user"}
    result = await console_main._dec_load_with_visibility(42, user)
    assert result is None


@pytest.mark.asyncio
async def test_load_with_visibility_scopes_query_to_workspace(console_main, monkeypatch):
    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}
    result = await console_main._dec_load_with_visibility(42, user)
    assert result is None

    _assert_db_scope(fake, "workspace-A")
    op, sql, params = _non_scope_calls(fake)[0]
    assert op == "fetchrow"
    assert "id = $1 AND workspace_id = $2" in sql
    assert params[:2] == (42, "workspace-A")


@pytest.mark.asyncio
async def test_load_with_visibility_admin_still_scoped_to_workspace(console_main, monkeypatch):
    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "admin", "active_workspace_id": "workspace-A"}
    result = await console_main._dec_load_with_visibility(42, user)
    assert result is None

    _assert_db_scope(fake, "workspace-A")
    op, sql, params = _non_scope_calls(fake)[0]
    assert "workspace_id = $2" in sql
    assert "visibility" not in sql, "admin path must not add the user-scoped subclause"
    assert params == (42, "workspace-A")


@pytest.mark.asyncio
async def test_create_decision_rejects_no_active_workspace(console_main):
    from fastapi import HTTPException

    user = {"id": 7, "role": "user"}
    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_create(
            body={"title": "test"}, user=user
        )
    assert exc_info.value.status_code == 400
    assert "workspace" in str(exc_info.value.detail).lower()


def _fake_decision_row(workspace_id: str = "workspace-A") -> dict:
    return {
        "id": 1,
        "title": "t",
        "description": "",
        "commitment_date": None,
        "kpis": [],
        "created_by_id": 7,
        "assignee_id": None,
        "visibility": "private",
        "status": "open",
        "outcome": None,
        "closed_at": None,
        "follow_up_decision_id": None,
        "workspace_id": workspace_id,
        "created_at": None,
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_list_decisions_returns_same_workspace_secret_with_db_scope(
    console_main, monkeypatch
):
    secret = _fake_decision_row("workspace-A")
    secret["title"] = "wsA-secret"
    fake = _FakePool(fetch_result=[secret])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_list(status="", overdue="", user=user)

    _assert_db_scope(fake, "workspace-A")
    assert [row["title"] for row in out["decisions"]] == ["wsA-secret"]


@pytest.mark.asyncio
async def test_decision_routes_hide_historical_source_state_rows(
    console_main, monkeypatch
):
    decision = _fake_decision_row("workspace-A")

    class _HistoricalPool(_FakePool):
        async def fetch(self, sql, *params):
            self.calls.append(("fetch", sql, params))
            if "FROM control_room_items" in sql:
                return [
                    {
                        "decision_id": 1,
                        "item_id": "source-state-1",
                        "item_kind": "source_state",
                        "source_dataset": "gold_source",
                        "metadata": {"data_status": "missing"},
                    }
                ]
            if "FROM decision_actions" in sql:
                return []
            return [decision]

    fake = _HistoricalPool(fetchrow_result=decision)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    assert await console_main.api_decisions_list(
        status="", overdue="", user=user
    ) == {"decisions": []}

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_get(decision_id=1, user=user)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_decision_inserts_active_workspace_id(console_main, monkeypatch):
    fake = _FakePool(fetchrow_result=_fake_decision_row())

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_create(
        body={"title": "test"}, user=user
    )
    assert out["workspace_id"] == "workspace-A"

    _assert_db_scope(fake, "workspace-A")
    op, sql, params = _non_scope_calls(fake)[0]
    assert op == "fetchrow"
    assert "workspace_id" in sql
    assert len(params) == 8, f"INSERT must bind 8 params, got {len(params)}"
    assert params[7] == "workspace-A"


@pytest.mark.asyncio
async def test_console_create_rejects_cross_workspace_assignee_before_insert(
    console_main, monkeypatch
):
    from fastapi import HTTPException

    class _CrossScopePool(_FakePool):
        async def fetchval(self, sql, *params):
            self.calls.append(("fetchval", sql, params))
            return None

        async def fetchrow(self, sql, *params):
            if "INSERT INTO decisions" in sql:
                raise AssertionError("cross-workspace assignee reached INSERT")
            return await super().fetchrow(sql, *params)

    fake = _CrossScopePool()

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {
        "id": 7,
        "role": "workspace_admin",
        "workspace_role": "workspace_admin",
        "active_tenant_id": "00000000-0000-0000-0000-0000000000a1",
        "active_workspace_id": "00000000-0000-0000-0000-0000000000a2",
    }

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_create(
            body={"title": "cross-scope-canary", "assignee_id": 8001},
            user=user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "assignee_id is not valid for the active workspace"
    validation = next(call for call in fake.calls if call[0] == "fetchval")
    assert "u.tenant_id = $3" in validation[1]
    assert "uwr.workspace_id = $2" in validation[1]


def test_console_main_and_v1_router_validate_decision_references():
    main_source = CONSOLE_MAIN.read_text(encoding="utf-8")
    router_source = (
        REPO_ROOT / "console" / "app" / "routers" / "v1" / "admin_decisions.py"
    ).read_text(encoding="utf-8")

    for source in (main_source, router_source):
        assert "assignee_id = _decision_reference_id" in source
        assert "await _validate_decision_references(" in source
        assert 'follow_up_decision_id=normalized_body.get("follow_up_decision_id")' in source
        assert "for_update=True" in source


@pytest.mark.asyncio
async def test_get_decision_happy_path_same_workspace(console_main, monkeypatch):
    fake = _FakePool(
        fetchrow_result=_fake_decision_row("workspace-A"),
        fetch_result=[],
    )

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_get(decision_id=1, user=user)
    assert out["id"] == 1
    assert out["workspace_id"] == "workspace-A"
    assert out["actions"] == []


@pytest.mark.asyncio
async def test_update_decision_happy_path_same_workspace(console_main, monkeypatch):
    existing = _fake_decision_row("workspace-A")
    existing["created_by_id"] = 7
    updated = dict(existing, title="new-title")

    class _MultiResultPool(_FakePool):
        def __init__(self, results):
            super().__init__()
            self._results = list(results)

        async def fetchrow(self, sql, *params):
            self.calls.append(("fetchrow", sql, params))
            return self._results.pop(0)

    fake = _MultiResultPool([existing, updated])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_update(
        decision_id=1, body={"title": "new-title"}, user=user
    )
    assert out["title"] == "new-title"

    _assert_db_scope(fake, "workspace-A")
    op2, sql2, params2 = next(
        call
        for call in _non_scope_calls(fake)
        if call[0] == "fetchrow" and "UPDATE decisions" in call[1]
    )
    assert op2 == "fetchrow"
    assert "workspace_id" in sql2
    assert "workspace-A" in params2


@pytest.mark.asyncio
async def test_delete_decision_happy_path_same_workspace(console_main, monkeypatch):
    existing = _fake_decision_row("workspace-A")
    existing["created_by_id"] = 7
    fake = _FakePool(fetchrow_result=existing)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_delete(decision_id=1, user=user)
    assert out == {"deleted": True, "id": 1}

    _assert_db_scope(fake, "workspace-A")
    op2, sql2, params2 = next(
        call
        for call in _non_scope_calls(fake)
        if call[0] == "execute" and "DELETE FROM decisions" in call[1]
    )
    assert op2 == "execute"
    assert "workspace_id = $2" in sql2
    assert params2 == (1, "workspace-A")


@pytest.mark.asyncio
async def test_add_action_404_when_cross_workspace(console_main, monkeypatch):
    from fastapi import HTTPException

    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {
        "id": 7,
        "role": "user",
        "active_tenant_id": "tenant-A",
        "active_workspace_id": "workspace-A",
    }

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_add_action(
            decision_id=42,
            body={"action_text": "tried to leak across workspaces"},
            request=SimpleNamespace(headers={"Idempotency-Key": "cross-scope-canary"}),
            user=user,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_action_happy_path_same_workspace(console_main, monkeypatch):
    existing = _fake_decision_row("workspace-A")
    existing["created_by_id"] = 7
    action_row = {
        "id": 100,
        "decision_id": 1,
        "action_text": "note",
        "note": None,
        "actor": "u@test",
        "ts": None,
    }

    class _MultiResultPool(_FakePool):
        def __init__(self, results):
            super().__init__()
            self._results = list(results)
            self._reservation_id = 101

        async def fetchrow(self, sql, *params):
            self.calls.append(("fetchrow", sql, params))
            return self._results.pop(0)

        async def fetchval(self, sql, *params):
            self.calls.append(("fetchval", sql, params))
            if sql.startswith("INSERT INTO workspace_decision_idempotency"):
                return self._reservation_id
            if sql.startswith("UPDATE workspace_decision_idempotency"):
                return self._reservation_id
            raise AssertionError(f"unexpected fetchval: {sql}")

    fake = _MultiResultPool([existing, action_row])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {
        "id": 7,
        "role": "user",
        "email": "u@test",
        "active_tenant_id": "tenant-A",
        "active_workspace_id": "workspace-A",
    }

    out = await console_main.api_decisions_add_action(
        decision_id=1,
        body={"action_text": "note"},
        request=SimpleNamespace(headers={"Idempotency-Key": "same-scope-canary"}),
        user=user,
    )
    assert out["action_text"] == "note"

    _assert_db_scope(fake, "workspace-A")
    _, load_sql, load_params = _non_scope_calls(fake)[0]
    assert "workspace_id = $2" in load_sql
    assert load_params[1] == "workspace-A"


@pytest.mark.asyncio
async def test_add_action_requires_idempotency_key_before_db(console_main, monkeypatch):
    from fastapi import HTTPException

    async def _forbidden_pool():
        raise AssertionError("missing idempotency key reached database")

    monkeypatch.setattr(console_main, "_dec_pool", _forbidden_pool)
    user = {
        "id": 7,
        "role": "user",
        "active_tenant_id": "tenant-A",
        "active_workspace_id": "workspace-A",
    }

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_add_action(
            decision_id=1,
            body={"action_text": "note"},
            request=SimpleNamespace(headers={}),
            user=user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Idempotency-Key header is required"


def test_current_workspace_id_treats_empty_string_as_missing(console_main):
    user = {"id": 7, "role": "user", "active_workspace_id": ""}
    assert console_main._current_workspace_id(user) is None or (
        console_main._current_workspace_id(user) == ""
    )


@pytest.mark.asyncio
async def test_load_with_visibility_treats_empty_workspace_as_missing(console_main):
    user = {"id": 7, "role": "user", "active_workspace_id": ""}
    result = await console_main._dec_load_with_visibility(42, user)
    assert result is None


@pytest.mark.asyncio
async def test_get_decision_404_when_cross_workspace(console_main, monkeypatch):
    from fastapi import HTTPException

    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_get(decision_id=42, user=user)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_decision_404_when_cross_workspace(console_main, monkeypatch):
    from fastapi import HTTPException

    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_update(
            decision_id=42, body={"title": "x"}, user=user
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_decision_404_when_cross_workspace(console_main, monkeypatch):
    from fastapi import HTTPException

    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_delete(decision_id=42, user=user)
    assert exc_info.value.status_code == 404
