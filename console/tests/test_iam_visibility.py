"""Sprint v1.5 — IAM visibility tests.

Mounts a mini FastAPI app that combines:
  - The real `app.routers.pages` router (so the protections live exactly
    where they live in production).
  - Tiny stub routes for the four pages defined in console/app/main.py
    that this sprint locked down (/decisions, /admin/users,
    /viewer/pipeline, /viewer/vault). Each stub wires the same
    `Depends(require_admin)` chain so the test verifies the gate, not
    the handler body.

A request-scoped middleware injects `request.state.user` so the
`require_user / require_admin / require_permission` chain can read the
role without any session / cookie machinery.
"""
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

# Some peer test files (test_security_router) register MagicMock stubs
# for app.dependencies/auth at module load via sys.modules.setdefault. If
# that ran first, the real require_admin we need would be a MagicMock —
# pop the stale stubs so the real symbol can be imported here.
for _k in ("app.dependencies", "app.services.auth"):
    if isinstance(sys.modules.get(_k), MagicMock):
        sys.modules.pop(_k, None)

from app.dependencies import require_admin
from app.routers.pages import router as pages_router
from app.services.permissions import require_permission

ADMIN_USER = {"id": 1, "role": "admin", "email": "admin@example.com"}
ANALYST_USER = {"id": 2, "role": "analyst", "email": "analyst@example.com"}
VIEWER_USER = {"id": 3, "role": "viewer", "email": "viewer@example.com"}
SECURITY_ADMIN_USER = {"id": 4, "role": "security_admin", "email": "sec@example.com"}


def _build_app(user):
    """Return a TestClient bound to a mini FastAPI that injects `user`."""
    application = FastAPI()

    @application.middleware("http")
    async def inject_user(request: Request, call_next):
        if user is not None:
            request.state.user = user
        return await call_next(request)

    application.include_router(pages_router)

    # Stubs that mirror the production main.py declarations so the test
    # exercises the dependency chain, not the FileResponse body.
    @application.get("/decisions", dependencies=[Depends(require_admin)])
    async def _decisions():
        return {"ok": True}

    @application.get("/admin/users", dependencies=[Depends(require_admin)])
    async def _admin_users(_=Depends(require_permission("iam.users.read"))):
        return {"ok": True}

    @application.get("/viewer/pipeline", dependencies=[Depends(require_admin)])
    async def _viewer_pipeline():
        return {"ok": True}

    @application.get("/viewer/vault", dependencies=[Depends(require_admin)])
    async def _viewer_vault():
        return {"ok": True}

    # Routes that MUST stay open to authenticated non-admin callers (we
    # mount thin stubs here just so the negative tests have something to
    # hit — pages.py doesn't own these).
    @application.get("/workspace")
    async def _workspace():
        return {"ok": True}

    @application.get("/healthz")
    async def _healthz():
        return {"ok": True}

    @application.get("/me")
    async def _me():
        return {"ok": True}

    return TestClient(application, raise_server_exceptions=False)


ADMIN_ONLY_PATHS = [
    "/iam",
    "/settings",
    "/operations",
    "/decisions",
    "/admin/users",
    "/viewer/pipeline",
    "/viewer/vault",
]

MONITOR_VIEWER_PATHS = [
    "/viewer/jobs",
    "/viewer/jobs/run-123",
]

DATA_VIEWER_PATHS = [
    "/viewer/datasets",
    "/viewer/datasets/gold_sales",
    "/viewer/semantic",
]


# ─── Admin can reach every protected page ────────────────────────────

@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
def test_admin_reaches_admin_only_pages(path):
    client = _build_app(ADMIN_USER)
    r = client.get(path)
    assert r.status_code == 200, f"admin blocked from {path}: {r.status_code} {r.text}"


# ─── Non-admin roles are rejected from every protected page ──────────

@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
@pytest.mark.parametrize("user", [ANALYST_USER, VIEWER_USER, SECURITY_ADMIN_USER],
                          ids=["analyst", "viewer", "security_admin"])
def test_non_admin_rejected_from_admin_only_pages(path, user):
    client = _build_app(user)
    r = client.get(path)
    # 401 if the route also needs an unrelated permission they lack,
    # 403 if they only fail the admin gate. Either is a hard reject.
    assert r.status_code in (401, 403), (
        f"non-admin {user['role']} unexpectedly reached {path}: "
        f"{r.status_code} {r.text}"
    )


# ─── Viewer pages are permission-gated, not binary admin-only ────────

@pytest.mark.parametrize("path", MONITOR_VIEWER_PATHS)
@pytest.mark.parametrize("user", [ANALYST_USER, VIEWER_USER, SECURITY_ADMIN_USER],
                          ids=["analyst", "viewer", "security_admin"])
def test_monitor_viewer_pages_follow_monitor_permission(path, user):
    client = _build_app(user)
    r = client.get(path)
    assert r.status_code == 200, (
        f"{user['role']} with monitor.read unexpectedly blocked from {path}: "
        f"{r.status_code} {r.text}"
    )


@pytest.mark.parametrize("path", DATA_VIEWER_PATHS)
@pytest.mark.parametrize("user", [ANALYST_USER, VIEWER_USER],
                          ids=["analyst", "viewer"])
def test_data_viewer_pages_follow_dataset_permission(path, user):
    client = _build_app(user)
    r = client.get(path)
    assert r.status_code == 200, (
        f"{user['role']} with datasets.read unexpectedly blocked from {path}: "
        f"{r.status_code} {r.text}"
    )


@pytest.mark.parametrize("path", DATA_VIEWER_PATHS)
def test_security_admin_without_dataset_permission_cannot_open_data_viewers(path):
    client = _build_app(SECURITY_ADMIN_USER)
    r = client.get(path)
    assert r.status_code in (401, 403), (
        f"security_admin unexpectedly reached data viewer {path}: "
        f"{r.status_code} {r.text}"
    )


# ─── Workspace, healthz and /me stay reachable for everyone ──────────

@pytest.mark.parametrize("path", ["/workspace", "/healthz", "/me"])
@pytest.mark.parametrize("user", [ANALYST_USER, VIEWER_USER],
                          ids=["analyst", "viewer"])
def test_non_admin_open_routes_still_work(path, user):
    client = _build_app(user)
    r = client.get(path)
    assert r.status_code == 200, (
        f"non-admin {user['role']} unexpectedly rejected from {path}: "
        f"{r.status_code} {r.text}"
    )


# ─── Anonymous (no user) gets 401 on admin routes (auth required) ────

@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
def test_anonymous_rejected_with_401(path):
    client = _build_app(user=None)
    r = client.get(path)
    assert r.status_code == 401, (
        f"anonymous unexpectedly reached {path}: {r.status_code} {r.text}"
    )


@pytest.mark.parametrize("path", MONITOR_VIEWER_PATHS + DATA_VIEWER_PATHS)
def test_anonymous_rejected_from_permission_viewers_with_401(path):
    client = _build_app(user=None)
    r = client.get(path)
    assert r.status_code == 401, (
        f"anonymous unexpectedly reached viewer route {path}: {r.status_code} {r.text}"
    )


# ─── Sanity: the require_admin dependency keys off role == 'admin' ──

def test_require_admin_accepts_admin():
    request = MagicMock()
    request.state.user = ADMIN_USER
    result = require_admin(request)
    assert result["role"] == "admin"


def test_require_admin_rejects_analyst():
    from fastapi import HTTPException

    request = MagicMock()
    request.state.user = ANALYST_USER
    with pytest.raises(HTTPException) as excinfo:
        require_admin(request)
    assert excinfo.value.status_code == 403


def test_require_admin_rejects_anonymous():
    from fastapi import HTTPException

    request = MagicMock()
    request.state.user = None
    with pytest.raises(HTTPException) as excinfo:
        require_admin(request)
    assert excinfo.value.status_code == 401
