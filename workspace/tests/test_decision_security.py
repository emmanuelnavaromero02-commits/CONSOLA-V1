"""F-SEG focal guards for Workspace Decision mutations."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
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


@pytest.fixture
def workspace_main(monkeypatch):
    _purge_app_modules()
    saved_path = list(sys.path)
    sys.path[:] = [
        value
        for value in sys.path
        if not any(marker in value for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(WORKSPACE_ROOT))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "INTERNAL_API_KEY",
        "workspace_fseg_internal_key_with_more_than_32_chars",
    )
    main = importlib.import_module("app.main")
    yield main
    sys.path[:] = saved_path
    _purge_app_modules()


MUTATING_DECISION_ROUTES = {
    ("POST", "/api/decisions"),
    ("PATCH", "/api/decisions/{decision_id}"),
    ("DELETE", "/api/decisions/{decision_id}"),
    ("POST", "/api/decisions/{decision_id}/actions"),
}


def test_all_decision_mutations_require_control_room_write(workspace_main):
    found: dict[tuple[str, str], bool] = {}
    for route in workspace_main.app.routes:
        for method in getattr(route, "methods", None) or set():
            key = (method, getattr(route, "path", ""))
            if key not in MUTATING_DECISION_ROUTES:
                continue
            found[key] = any(
                getattr(getattr(dep, "call", None), "required_permission", None)
                == "control_room.write"
                for dep in route.dependant.dependencies
            )

    assert set(found) == MUTATING_DECISION_ROUTES
    assert all(found.values())


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/decisions", {"title": "synthetic-canary"}),
        ("PATCH", "/api/decisions/900001", {"title": "synthetic-canary"}),
        ("DELETE", "/api/decisions/900001", None),
        (
            "POST",
            "/api/decisions/900001/actions",
            {"action_text": "synthetic-canary"},
        ),
    ],
)
def test_viewer_gets_403_before_decision_db_access(
    workspace_main, monkeypatch, method, path, body
):
    viewer = {
        "id": 900001,
        "email": "viewer.invalid",
        "role": "viewer",
        "workspace_role": "viewer",
        "active_tenant_id": "00000000-0000-0000-0000-000000000901",
        "active_workspace_id": "00000000-0000-0000-0000-000000000902",
    }

    async def session_user(_token, _requested_workspace_id=None):
        return viewer

    async def forbidden_db():
        raise AssertionError("viewer request reached the database")

    monkeypatch.setattr(workspace_main._session, "get_session_user", session_user)
    monkeypatch.setattr(workspace_main, "pg", forbidden_db)

    csrf = "synthetic-csrf-canary"
    with TestClient(workspace_main.app) as client:
        response = client.request(
            method,
            path,
            json=body,
            cookies={"mod_session": "synthetic-session", "csrf_token": csrf},
            headers={"X-CSRF-Token": csrf},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "permission required: control_room.write"}


@pytest.mark.asyncio
async def test_db_permission_denied_is_translated_to_403(workspace_main, monkeypatch):
    class PermissionDenied(Exception):
        sqlstate = "42501"

    class DeniedPool:
        async def fetchrow(self, _sql, *_params):
            raise PermissionDenied("synthetic database detail must not leak")

    async def pool():
        return DeniedPool()

    monkeypatch.setattr(workspace_main, "pg", pool)
    request = SimpleNamespace(
        state=SimpleNamespace(
            user={
                "id": 900002,
                "role": "workspace_admin",
                "active_workspace_id": "00000000-0000-0000-0000-000000000903",
            }
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_create(
            request, {"title": "synthetic-canary"}
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "decision operation forbidden"
    assert "database detail" not in str(exc_info.value.detail)


def _writer_request(*, tenant: bool = True):
    user = {
        "id": 900010,
        "email": "writer.invalid",
        "role": "workspace_admin",
        "workspace_role": "workspace_admin",
        "active_workspace_id": "00000000-0000-0000-0000-000000000911",
    }
    if tenant:
        user["active_tenant_id"] = "00000000-0000-0000-0000-000000000912"
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.mark.asyncio
async def test_create_rejects_cross_workspace_assignee_before_insert(
    workspace_main, monkeypatch
):
    class CrossWorkspacePool:
        def __init__(self):
            self.fetchval_calls = []

        async def fetchval(self, sql, *params):
            self.fetchval_calls.append((sql, params))
            return None

        async def fetchrow(self, *_args):
            raise AssertionError("cross-workspace assignee reached INSERT")

    fake = CrossWorkspacePool()

    async def pool():
        return fake

    monkeypatch.setattr(workspace_main, "pg", pool)
    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_create(
            _writer_request(),
            {"title": "synthetic-canary", "assignee_id": 900099},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "assignee_id is not valid for the active workspace"
    sql, params = fake.fetchval_calls[0]
    assert "uwr.workspace_id = $2" in sql
    assert "w.tenant_id = $3" in sql
    assert "u.tenant_id = $3" in sql
    assert "u.is_active = TRUE" in sql
    assert params == (
        900099,
        "00000000-0000-0000-0000-000000000911",
        "00000000-0000-0000-0000-000000000912",
    )


@pytest.mark.asyncio
async def test_create_accepts_assignee_in_active_tenant_workspace(
    workspace_main, monkeypatch
):
    inserted = {
        "id": 900020,
        "title": "synthetic-canary",
        "description": "",
        "created_at": None,
        "commitment_date": None,
        "closed_at": None,
        "kpis": [],
        "status": "open",
        "outcome": None,
        "follow_up_decision_id": None,
        "created_by_id": 900010,
        "assignee_id": 900011,
        "visibility": "private",
        "workspace_id": "00000000-0000-0000-0000-000000000911",
    }

    class SameWorkspacePool:
        async def fetchval(self, _sql, *_params):
            return 900011

        async def fetchrow(self, sql, *params):
            assert "INSERT INTO decisions" in sql
            assert params[5] == 900011
            return inserted

    async def pool():
        return SameWorkspacePool()

    monkeypatch.setattr(workspace_main, "pg", pool)
    result = await workspace_main.api_decisions_create(
        _writer_request(),
        {"title": "synthetic-canary", "assignee_id": "900011"},
    )

    assert result["id"] == 900020
    assert result["assignee_id"] == 900011


@pytest.mark.asyncio
async def test_update_rejects_cross_workspace_follow_up_before_update(
    workspace_main, monkeypatch
):
    existing = {
        "id": 900030,
        "title": "synthetic-canary",
        "description": "",
        "created_at": None,
        "commitment_date": None,
        "closed_at": None,
        "kpis": [],
        "status": "open",
        "outcome": None,
        "follow_up_decision_id": None,
        "created_by_id": 900010,
        "assignee_id": None,
        "visibility": "private",
        "workspace_id": "00000000-0000-0000-0000-000000000911",
    }

    class CrossWorkspacePool:
        async def fetchrow(self, sql, *_params):
            if sql.startswith("SELECT * FROM decisions"):
                return existing
            raise AssertionError("cross-workspace follow-up reached UPDATE")

        async def fetchval(self, sql, *_params):
            assert "d.workspace_id = $2" in sql
            assert "w.tenant_id = $3" in sql
            return None

    async def pool():
        return CrossWorkspacePool()

    monkeypatch.setattr(workspace_main, "pg", pool)
    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_update(
            _writer_request(),
            900030,
            {"follow_up_decision_id": 900031},
        )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "follow_up_decision_id is not valid for the active workspace"
    )


@pytest.mark.asyncio
async def test_reference_validation_fails_closed_without_active_tenant(
    workspace_main, monkeypatch
):
    class NoWritePool:
        async def fetchrow(self, *_args):
            raise AssertionError("missing tenant reached INSERT")

    async def pool():
        return NoWritePool()

    monkeypatch.setattr(workspace_main, "pg", pool)
    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_create(
            _writer_request(tenant=False),
            {"title": "synthetic-canary", "assignee_id": 900011},
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "active tenant and workspace are required"


def _action_request(idempotency_key: str | None = "synthetic-idempotency-canary"):
    request = _writer_request()
    request.headers = (
        {"Idempotency-Key": idempotency_key} if idempotency_key is not None else {}
    )
    return request


@pytest.mark.asyncio
async def test_decision_action_requires_idempotency_key_before_db(workspace_main):
    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_add_action(
            _action_request(None),
            900030,
            {"action_text": "synthetic-canary"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Idempotency-Key header is required"


class _IdempotencyPool:
    def __init__(self):
        self.reservation = None
        self.action_insert_count = 0
        self.all_params = []
        self.existing = {
            "id": 900030,
            "title": "synthetic-canary",
            "description": "",
            "created_at": None,
            "commitment_date": None,
            "closed_at": None,
            "kpis": [],
            "status": "open",
            "outcome": None,
            "follow_up_decision_id": None,
            "created_by_id": 900010,
            "assignee_id": None,
            "visibility": "private",
            "workspace_id": "00000000-0000-0000-0000-000000000911",
        }
        self.action = {
            "id": 900040,
            "decision_id": 900030,
            "action_text": "synthetic-canary",
            "note": None,
            "actor": "writer.invalid",
            "ts": None,
        }

    async def execute(self, _sql, *params):
        self.all_params.extend(params)

    async def fetchval(self, sql, *params):
        self.all_params.extend(params)
        if sql.startswith("INSERT INTO workspace_decision_idempotency"):
            if self.reservation is not None:
                return None
            self.reservation = {
                "id": 900050,
                "request_fingerprint": params[5],
                "status": "in_progress",
                "action_id": None,
            }
            return 900050
        if sql.startswith("UPDATE workspace_decision_idempotency"):
            self.reservation["status"] = "completed"
            self.reservation["action_id"] = params[1]
            return self.reservation["id"]
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *params):
        self.all_params.extend(params)
        if sql.startswith("SELECT * FROM decisions"):
            return self.existing
        if sql.startswith("INSERT INTO decision_actions"):
            self.action_insert_count += 1
            return self.action
        if sql.startswith("SELECT request_fingerprint"):
            return self.reservation
        if sql.startswith("SELECT * FROM decision_actions"):
            return self.action
        raise AssertionError(f"unexpected fetchrow: {sql}")


@pytest.mark.asyncio
async def test_same_idempotency_key_replays_exactly_one_action(
    workspace_main, monkeypatch
):
    fake = _IdempotencyPool()

    async def pool():
        return fake

    monkeypatch.setattr(workspace_main, "pg", pool)
    request = _action_request()
    first = await workspace_main.api_decisions_add_action(
        request,
        900030,
        {"action_text": "synthetic-canary"},
    )
    replay = await workspace_main.api_decisions_add_action(
        request,
        900030,
        {"action_text": "synthetic-canary"},
    )

    assert first == replay
    assert first["id"] == 900040
    assert fake.action_insert_count == 1
    assert "synthetic-idempotency-canary" not in fake.all_params
    assert len(fake.reservation["request_fingerprint"]) == 64


@pytest.mark.asyncio
async def test_same_key_with_changed_payload_is_409(workspace_main, monkeypatch):
    fake = _IdempotencyPool()

    async def pool():
        return fake

    monkeypatch.setattr(workspace_main, "pg", pool)
    request = _action_request()
    await workspace_main.api_decisions_add_action(
        request,
        900030,
        {"action_text": "synthetic-canary"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_add_action(
            request,
            900030,
            {"action_text": "changed-canary"},
        )

    assert exc_info.value.status_code == 409
    assert fake.action_insert_count == 1


def test_workspace_ui_sends_fresh_decision_action_idempotency_key():
    source = (WORKSPACE_ROOT / "app" / "static" / "js" / "workspace.js").read_text(
        encoding="utf-8"
    )
    assert "globalThis.crypto?.randomUUID?.()" in source
    assert "'Idempotency-Key': idempotencyKey" in source


def test_decision_idempotency_migration_is_durable_and_actor_scoped():
    migration = (
        WORKSPACE_ROOT.parent
        / "infra"
        / "init"
        / "99zzz_workspace_decision_idempotency.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS workspace_decision_idempotency" in migration
    assert "UNIQUE (actor_user_id, workspace_id, operation, idempotency_key_hash)" in migration
    assert "request_fingerprint" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "current_setting('app.user_id', TRUE)" in migration
    assert "idempotency_key TEXT" not in migration
