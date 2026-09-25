import sys
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

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
    application = FastAPI()

    @application.middleware("http")
    async def inject_user(request: Request, call_next):
        if user is not None:
            request.state.user = user
        return await call_next(request)

    application.include_router(pages_router)

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

DIRECT_VIEWER_SHELL_PATHS = [
    ("/viewer?type=jobs", VIEWER_USER, 200),
    ("/viewer?type=datasets", VIEWER_USER, 200),
    ("/viewer?type=lineage", VIEWER_USER, 200),
    ("/viewer?type=vault", SECURITY_ADMIN_USER, 200),
    ("/viewer?type=datasets", SECURITY_ADMIN_USER, 403),
    ("/viewer?type=vault", VIEWER_USER, 403),
]


@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
def test_admin_reaches_admin_only_pages(path):
    client = _build_app(ADMIN_USER)
    r = client.get(path)
    assert r.status_code == 200, f"admin blocked from {path}: {r.status_code} {r.text}"


@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
@pytest.mark.parametrize("user", [ANALYST_USER, VIEWER_USER, SECURITY_ADMIN_USER],
                          ids=["analyst", "viewer", "security_admin"])
def test_non_admin_rejected_from_admin_only_pages(path, user):
    client = _build_app(user)
    r = client.get(path)
    assert r.status_code in (401, 403), (
        f"non-admin {user['role']} unexpectedly reached {path}: "
        f"{r.status_code} {r.text}"
    )


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


@pytest.mark.parametrize("path,user,expected_status", DIRECT_VIEWER_SHELL_PATHS)
def test_direct_viewer_shell_enforces_type_permission(path, user, expected_status):
    client = _build_app(user)
    r = client.get(path)
    assert r.status_code == expected_status, (
        f"{user['role']} got {r.status_code} for {path}; expected {expected_status}: {r.text}"
    )


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
