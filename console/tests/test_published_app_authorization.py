from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)
os.environ.setdefault("JWT_SECRET_KEY", "y" * 64)
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
for _pair in (
    "AIRFLOW", "HUBSPOT", "MCP_INFRA", "REPLICON", "SALESFORCE",
    "SAP_HCM", "SAP_S4HANA", "SAP_SUCCESSFACTORS", "WORKSPACE",
):
    os.environ.setdefault(f"INTERNAL_API_KEY_{_pair}_TO_CONSOLE", "z" * 64)

import app.main as main  # noqa: E402
from app.routers.v1 import marketplace_apps  # noqa: E402

APP = "sap_successfactors_talent_health"
DATASET = "talent_operational_features"
UNDECLARED = "payroll_secrets"

SCOPES = {
    "tenantA/workspaceA": {"id": 1, "role": "viewer", "tenant_id": "tenant-a", "workspace_id": "ws-a"},
    "tenantA/workspaceB": {"id": 2, "role": "viewer", "tenant_id": "tenant-a", "workspace_id": "ws-b"},
    "tenantB/workspaceB": {"id": 3, "role": "viewer", "tenant_id": "tenant-b", "workspace_id": "ws-b"},
}
NO_APPS_READ = {"id": 9, "role": "auditor", "tenant_id": "tenant-a", "workspace_id": "ws-a"}
PUBLISHED_IN = {("tenant-a", "ws-a")}


def _scope(user: Mapping[str, object]) -> tuple[str, str]:
    return (str(user.get("tenant_id") or ""), str(user.get("workspace_id") or ""))


@pytest.fixture
def make_client(monkeypatch):
    async def _refinement_app_html(name, user):
        if _scope(user or {}) not in PUBLISHED_IN:
            raise HTTPException(404, "app not found")
        html = (
            "<html><head></head><body>"
            f'<script>fetch("/api/data/{DATASET}")</script>'
            "</body></html>"
        )
        return html, {
            "name": name,
            "cartridge": "sap_successfactors",
            "datasets_used": [DATASET],
        }

    async def _active(user, candidates):
        return set() if user.get("_not_installed") else set(candidates)

    def _visible(user, cartridge):
        if user.get("_cartridge_hidden"):
            raise HTTPException(403, "cartridge not visible")

    async def _apps_payload(user, include_unready=False):
        if _scope(user or {}) not in PUBLISHED_IN:
            return {"apps": []}
        return {
            "apps": [
                {"name": APP, "cartridge": "sap_successfactors", "datasets_used": [DATASET]}
            ]
        }

    monkeypatch.setattr(main, "_refinement_app_html", _refinement_app_html)
    monkeypatch.setattr(main, "_active_scoped_connection_cartridges", _active)
    monkeypatch.setattr(main, "_require_cartridge_visible", _visible)
    monkeypatch.setattr(main, "_apps_payload_visible_and_ready", _apps_payload)
    monkeypatch.setattr(main, "_inject_published_app_theme", lambda html: html)
    monkeypatch.setattr(main, "_app_cartridge_id", lambda app: "sap_successfactors")

    def _build(user: Mapping[str, object]) -> TestClient:
        api = FastAPI()

        @api.middleware("http")
        async def _inject_user(request: Request, call_next):
            request.state.user = dict(user)
            return await call_next(request)

        api.include_router(marketplace_apps.router)
        return TestClient(api, follow_redirects=False)

    return _build


def test_direct_route_redirects_and_never_returns_app_html(make_client):
    r = make_client(SCOPES["tenantA/workspaceA"]).get(f"/apps/{APP}")
    assert r.status_code == 303
    assert r.headers["location"] == f"/analytics/viewer?app={APP}"
    assert "<script" not in r.text


def test_direct_route_rejects_a_malformed_name(make_client):
    c = make_client(SCOPES["tenantA/workspaceA"])
    assert c.get("/apps/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404, 422)


@pytest.mark.parametrize("path", [f"/apps/{APP}", f"/apps/{APP}/embed", f"/apps/{APP}/content"])
def test_every_app_route_requires_apps_read(make_client, path):
    assert make_client(NO_APPS_READ).get(path).status_code == 403


def test_unauthenticated_callers_are_refused(make_client):
    assert make_client({}).get(f"/apps/{APP}/embed").status_code == 401


@pytest.mark.parametrize("scope", ["tenantA/workspaceB", "tenantB/workspaceB"])
@pytest.mark.parametrize("suffix", ["/embed", "/content"])
def test_other_scopes_cannot_reach_app_content(make_client, scope, suffix):
    r = make_client(SCOPES[scope]).get(f"/apps/{APP}{suffix}")
    assert r.status_code == (404 if suffix == "/embed" else 403)
    assert DATASET not in r.text


def test_the_owning_scope_does_get_the_wrapper(make_client):
    r = make_client(SCOPES["tenantA/workspaceA"]).get(f"/apps/{APP}/embed")
    assert r.status_code == 200
    assert 'sandbox="allow-scripts"' in r.text
    assert f"/apps/{APP}/content" in r.text


def test_hidden_cartridge_is_refused(make_client):
    user = {**SCOPES["tenantA/workspaceA"], "_cartridge_hidden": True}
    assert make_client(user).get(f"/apps/{APP}/content").status_code == 403


def test_visible_but_not_installed_cartridge_does_not_serve_content(make_client):
    user = {**SCOPES["tenantA/workspaceA"], "_not_installed": True}
    r = make_client(user).get(f"/apps/{APP}/content")
    assert r.status_code in (303, 403)
    assert "<script" not in r.text


def test_content_refuses_a_request_that_is_not_a_capability_bearing_frame_load(
    make_client,
):
    r = make_client(SCOPES["tenantA/workspaceA"]).get(f"/apps/{APP}/content")
    assert r.status_code == 403
    assert "<script" not in r.text
    assert "omegaBrokeredFetch" not in r.text


def test_content_refusal_does_not_distinguish_its_reason(make_client):
    missing = make_client(SCOPES["tenantA/workspaceA"]).get(f"/apps/{APP}/content")
    forged = make_client(SCOPES["tenantA/workspaceA"]).get(
        f"/apps/{APP}/content?cap=not.avalidcapability"
    )
    assert missing.status_code == forged.status_code == 403
    assert missing.json() == forged.json()


def test_wrapper_allowlist_is_empty_without_a_durable_grant(make_client):
    wrapper = make_client(SCOPES["tenantA/workspaceA"]).get(f"/apps/{APP}/embed").text
    assert "allowedDatasets = new Set([])" in wrapper
    assert f'"{DATASET}"' not in wrapper
    assert UNDECLARED not in wrapper


def test_data_endpoint_requires_its_own_permission_in_both_routers():
    from app.services.permissions import ROLE_PERMISSIONS

    assert "apps.read" in ROLE_PERMISSIONS["workspace_user"]
    assert "datasets.read" not in ROLE_PERMISSIONS["workspace_user"]

    for path in ("console/app/main.py", "console/app/routers/v1/data.py"):
        source = (REPO / path).read_text(encoding="utf-8")
        block = source.split('"/api/data/{dataset}"', 1)[1][:400]
        assert 'require_permission("datasets.read")' in block
