"""
Sprint v1.37 — audit B7 (P0).

Before v1.37, ``console/app/main.py`` had a ``_dec_visible_clause``
that filtered ``decisions`` only by ``created_by_id`` / ``assignee_id``
/ ``visibility``. A user with membership in multiple workspaces saw
every ``visibility='shared'`` decision across all of those workspaces
even when their active session was scoped to a single one. POST also
did not write ``workspace_id``, so new rows inherited the legacy
backfill assignment in ``infra/init/33_decisions_workspace_id.sql``
and then leaked as "shared" across tenants.

This suite enforces, at the source level, that the six decision
endpoints (``GET/POST /api/decisions``, ``GET/PATCH/DELETE
/api/decisions/{id}`` and ``POST /api/decisions/{id}/actions``) all
scope by the active workspace.
"""
from __future__ import annotations

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


# ── Static guards on console/app/main.py ────────────────────────────────────

def test_decision_select_queries_are_workspace_scoped():
    """Every direct SELECT on the ``decisions`` table must include the
    ``workspace_id`` filter so a cross-workspace read can't slip past
    the visibility clause."""
    src = console_route_source()

    # _dec_load_with_visibility built the per-row lookup.
    assert "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2" in src, (
        "_dec_load_with_visibility must include workspace_id in the WHERE"
    )

    # The list endpoint builds the WHERE through _dec_visible_clause;
    # confirm that clause references workspace_id at least once.
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
    """v1.32 owned the column; v1.37 just makes the code use it."""
    src = (REPO_ROOT / "infra/init/33_decisions_workspace_id.sql").read_text()
    assert "ALTER TABLE decisions" in src
    assert "ADD COLUMN IF NOT EXISTS workspace_id" in src
    assert "idx_decisions_workspace_id" in src


# ── Behavioral tests on console.app.main ────────────────────────────────────

# Minimal env so importing console.app.main doesn't blow up. We use
# setenv inside the fixture (not module-level setdefault) so an earlier
# test that calls ``monkeypatch.delenv("INTERNAL_API_KEY")`` to
# exercise the fail-fast path doesn't leave us with an empty value.
_CONSOLE_TEST_ENV = {
    "INTERNAL_API_KEY": "test-internal-api-key-not-default-aaaaaaaaaaaaaaaaa",
    "JWT_SECRET_KEY": "test-jwt-key-not-default-bbbbbbbbbbbbbbbbbbbbbbbbb",
    "MINIO_SECRET_KEY": "minioadmin",
    "POSTGRES_PASSWORD": "ci",
    "ADMIN_EMAIL": "admin@example.com",
    "ADMIN_PASSWORD": "ci-admin-password",
}


class _FakePool:
    """Records every fetch / fetchrow / execute call so the test can
    assert which SQL ran and which params were bound."""

    def __init__(self, fetchrow_result=None, fetch_result=None):
        self.calls: list[tuple[str, str, tuple]] = []
        self._fetchrow_result = fetchrow_result
        self._fetch_result = fetch_result or []

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        return self._fetchrow_result

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", sql, params))
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
    """``api_decisions_list`` must emit ``workspace_id = $N`` and bind
    the user's active workspace as the first parameter."""
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
    """A user with no active workspace must see zero decisions — the
    route must fail closed before hitting the database."""
    fake = _FakePool(fetch_result=[])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user"}  # no active_workspace_id
    result = await console_main.api_decisions_list(status="", overdue="", user=user)
    assert result == {"decisions": []}
    assert fake.calls == [], "missing-workspace path must short-circuit before DB"


@pytest.mark.asyncio
async def test_load_with_visibility_returns_none_without_active_workspace(console_main):
    """``_dec_load_with_visibility`` must short-circuit to None when the
    user has no active workspace, BEFORE hitting the database."""
    user = {"id": 7, "role": "user"}  # no active_workspace_id
    # No monkeypatch on _dec_pool — if the code tried to use it, the
    # test would fail with an import / connection error.
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
    """Admins are global, but only inside their active workspace. They
    don't get to read tenant A's decisions while their session is on
    tenant B."""
    fake = _FakePool(fetchrow_result=None)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "admin", "active_workspace_id": "workspace-A"}
    result = await console_main._dec_load_with_visibility(42, user)
    assert result is None

    _assert_db_scope(fake, "workspace-A")
    op, sql, params = _non_scope_calls(fake)[0]
    # Admin path skips the visibility/owner subclause but MUST still
    # bind workspace_id.
    assert "workspace_id = $2" in sql
    assert "visibility" not in sql, "admin path must not add the user-scoped subclause"
    assert params == (42, "workspace-A")


@pytest.mark.asyncio
async def test_create_decision_rejects_no_active_workspace(console_main):
    from fastapi import HTTPException

    user = {"id": 7, "role": "user"}  # no active_workspace_id
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
    # Reviewer #2 finding: tighten the param assertion. The INSERT
    # signature is
    #   (title, description, commitment_date, kpis, created_by_id,
    #    assignee_id, visibility, workspace_id)
    # so workspace_id MUST be the 8th positional param. Asserting
    # "workspace-A" in params catches presence but not order, so a
    # future refactor that swapped two positional fields would slip
    # through. Pin the position too.
    assert len(params) == 8, f"INSERT must bind 8 params, got {len(params)}"
    assert params[7] == "workspace-A"


# ── Happy paths (reviewer #2): cross-workspace -> 404 was already covered;
# verify same-workspace -> 200 so a regression that returns None for every
# row would also fail the suite, not just the negative tests. ──────────────

@pytest.mark.asyncio
async def test_get_decision_happy_path_same_workspace(console_main, monkeypatch):
    fake = _FakePool(
        fetchrow_result=_fake_decision_row("workspace-A"),
        fetch_result=[],  # no decision_actions yet
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
    # fetchrow #1 loads "existing" with workspace=A; #2 returns the updated row.
    existing = _fake_decision_row("workspace-A")
    existing["created_by_id"] = 7  # caller is the creator, so can_edit=True
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

    # The business-source read precedes the UPDATE; verify the write itself.
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
    existing["created_by_id"] = 7  # caller is the creator, so can_delete=True
    fake = _FakePool(fetchrow_result=existing)

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_delete(decision_id=1, user=user)
    assert out == {"deleted": True, "id": 1}

    # The business-source read precedes the DELETE; verify the write itself.
    _assert_db_scope(fake, "workspace-A")
    op2, sql2, params2 = next(
        call
        for call in _non_scope_calls(fake)
        if call[0] == "execute" and "DELETE FROM decisions" in call[1]
    )
    assert op2 == "execute"
    assert "workspace_id = $2" in sql2
    assert params2 == (1, "workspace-A")


# ── POST /api/decisions/{id}/actions — reviewer #1 + #2 finding: this
# endpoint also goes through _dec_load_with_visibility, so it's
# workspace-scoped by construction. The tests below pin that contract
# so a future refactor that bypasses the helper fails CI. ───────────────

@pytest.mark.asyncio
async def test_add_action_404_when_cross_workspace(console_main, monkeypatch):
    from fastapi import HTTPException

    fake = _FakePool(fetchrow_result=None)  # decision not visible

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "active_workspace_id": "workspace-A"}

    with pytest.raises(HTTPException) as exc_info:
        await console_main.api_decisions_add_action(
            decision_id=42,
            body={"action_text": "tried to leak across workspaces"},
            user=user,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_action_happy_path_same_workspace(console_main, monkeypatch):
    existing = _fake_decision_row("workspace-A")
    existing["created_by_id"] = 7  # caller can edit
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

        async def fetchrow(self, sql, *params):
            self.calls.append(("fetchrow", sql, params))
            return self._results.pop(0)

    fake = _MultiResultPool([existing, action_row])

    async def _factory():
        return fake

    monkeypatch.setattr(console_main, "_dec_pool", _factory)
    user = {"id": 7, "role": "user", "email": "u@test", "active_workspace_id": "workspace-A"}

    out = await console_main.api_decisions_add_action(
        decision_id=1, body={"action_text": "note"}, user=user
    )
    assert out["action_text"] == "note"

    # First call loaded the parent decision with workspace filter.
    _assert_db_scope(fake, "workspace-A")
    _, load_sql, load_params = _non_scope_calls(fake)[0]
    assert "workspace_id = $2" in load_sql
    assert load_params[1] == "workspace-A"


# ── Edge case: active_workspace_id="" (empty string, not None). The
# helper treats "" as falsy via `or`, so an empty workspace must
# behave like a missing one. ──────────────────────────────────────────

def test_current_workspace_id_treats_empty_string_as_missing(console_main):
    user = {"id": 7, "role": "user", "active_workspace_id": ""}
    assert console_main._current_workspace_id(user) is None or (
        console_main._current_workspace_id(user) == ""
        # Either None or "" is fine — what matters is that downstream
        # short-circuits both. The next assertion proves the
        # _dec_load_with_visibility path treats it as no-workspace.
    )


@pytest.mark.asyncio
async def test_load_with_visibility_treats_empty_workspace_as_missing(console_main):
    """An empty-string active_workspace_id must short-circuit to None
    so a malformed session can't read any decision."""
    user = {"id": 7, "role": "user", "active_workspace_id": ""}
    result = await console_main._dec_load_with_visibility(42, user)
    assert result is None


@pytest.mark.asyncio
async def test_get_decision_404_when_cross_workspace(console_main, monkeypatch):
    """A decision that lives in a different workspace must look exactly
    like a missing row to the caller — same 404, same message, no
    leaked existence."""
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
