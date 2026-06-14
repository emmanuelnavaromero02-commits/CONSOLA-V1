from __future__ import annotations

import base64
import hashlib
import os
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.dependencies import ROLE_ADMIN, require_admin, require_global_any_role
from app.dependencies import require_authenticated
from app.services import audit_service
from app.services.csrf import CSRF_COOKIE_NAME, require_csrf, set_csrf_cookie
from app.services.permissions import has_permission, require_permission


STATIC = Path(__file__).resolve().parents[1] / "static"
CONSOLE_NEXT_STATIC = STATIC / "console-next"
WORKSPACE_INTERNAL_URL = os.environ.get(
    "WORKSPACE_INTERNAL_URL", "http://workspace:8001"
).rstrip("/")
_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_DATA_VIEWERS = {
    "schema",
    "datasets",
    "dataset",
    "semantic",
    "semantic-layer",
    "lineage",
}
_VAULT_VIEWERS = {"vault"}

router = APIRouter(tags=["Pages"])
PLATFORM_ADMIN = require_global_any_role("owner", "super_admin", ROLE_ADMIN)


def _console_next_file(path: str = "index.html") -> Path:
    root = CONSOLE_NEXT_STATIC.resolve()
    if not root.is_dir():
        raise HTTPException(
            status_code=503, detail="console-next frontend is not built"
        )
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="console-next asset not found"
        ) from exc
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="console-next asset not found")
    return candidate


@lru_cache(maxsize=128)
def _console_next_csp_cached(
    path: str,
    mtime_ns: int,
    size: int,
    frame_ancestors: str = "'none'",
) -> str:
    html = Path(path).read_text(encoding="utf-8")
    hashes = []
    for body in _INLINE_SCRIPT_RE.findall(html):
        if not body.strip():
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        hashes.append(base64.b64encode(digest).decode("ascii"))
    script_src = "script-src 'self'" + "".join(f" 'sha256-{value}'" for value in hashes)
    return (
        "default-src 'self'; "
        f"{script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        f"frame-ancestors {frame_ancestors}; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


def _console_next_csp(path: str, frame_ancestors: str = "'none'") -> str:
    stat = Path(path).stat()
    return _console_next_csp_cached(
        path, stat.st_mtime_ns, stat.st_size, frame_ancestors
    )


def _console_next_response(
    request: Request, path: str = "index.html", *, frame_ancestors: str = "'none'"
) -> FileResponse:
    page = _console_next_file(path)
    response = FileResponse(
        page,
        headers={
            "Content-Security-Policy": _console_next_csp(str(page), frame_ancestors)
        },
    )
    set_csrf_cookie(response, request.cookies.get(CSRF_COOKIE_NAME))
    return response


def _viewer_permission(viewer_type: str | None) -> str:
    normalized = (viewer_type or "jobs").strip().lower()
    if normalized in _DATA_VIEWERS:
        return "datasets.read"
    if normalized in _VAULT_VIEWERS:
        return "vault.connections.read"
    return "monitor.read"


async def _require_viewer_permission(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    permission = _viewer_permission(request.query_params.get("type"))
    if not has_permission(user, permission):
        raise HTTPException(
            status_code=403, detail=f"permission required: {permission}"
        )
    return user


def _viewer_redirect(
    request: Request, viewer_type: str, **params: str
) -> RedirectResponse:
    query = dict(request.query_params)
    query["type"] = viewer_type
    for key, value in params.items():
        if value:
            query[key] = value
    return RedirectResponse(url=f"/viewer?{urlencode(query)}", status_code=307)


def _workspace_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in ("accept", "content-type", "cookie", "x-request-id", "x-csrf-token"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


async def _workspace_proxy(request: Request, path: str) -> Response:
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            upstream = await client.request(
                request.method,
                f"{WORKSPACE_INTERNAL_URL}{path}",
                params=request.query_params,
                content=body if body else None,
                headers=_workspace_headers(request),
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502, detail=f"Workspace service unavailable: {exc}"
        ) from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def _workspace_stream_proxy(
    request: Request, path: str
) -> StreamingResponse | Response:
    body = await request.body()
    client = httpx.AsyncClient(timeout=None)
    stream_cm = client.stream(
        request.method,
        f"{WORKSPACE_INTERNAL_URL}{path}",
        params=request.query_params,
        content=body if body else None,
        headers=_workspace_headers(request),
    )
    try:
        upstream = await stream_cm.__aenter__()
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=502, detail=f"Workspace service unavailable: {exc}"
        ) from exc

    if upstream.status_code >= 400:
        content = await upstream.aread()
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()

    return StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


@router.get("/")
async def index():
    return RedirectResponse(url="/dashboard", status_code=307)


@router.get("/monitor", dependencies=[Depends(require_permission("monitor.read"))])
async def monitor_page(request: Request):
    return _console_next_response(request, "monitor/index.html")


@router.get("/dashboard", dependencies=[Depends(require_authenticated)])
async def dashboard_page(request: Request):
    return _console_next_response(request, "dashboard/index.html")


@router.get(
    "/security",
    dependencies=[
        Depends(require_permission("security.audit.read")),
        Depends(require_admin),
    ],
)
async def security_page(request: Request):
    return _console_next_response(request, "security/index.html")


@router.get(
    "/control-room", dependencies=[Depends(require_permission("workspace.access"))]
)
async def control_room_page(request: Request):
    return _console_next_response(request, "control-room/index.html")


@router.get(
    "/control-room/", dependencies=[Depends(require_permission("workspace.access"))]
)
async def control_room_page_slash(request: Request):
    return _console_next_response(request, "control-room/index.html")


# Sprint Phase-0 SaaS controls — "Mis accesos" is the user-facing view of
# their own identity, role, workspace, effective permissions and cartridge
# entitlements. Available to any authenticated user. No admin powers
# implied; the page renders strictly what /api/me/access returns and the
# backend continues to enforce every action it offers as a link.
@router.get("/my-access", dependencies=[Depends(require_authenticated)])
async def my_access_page(request: Request):
    return _console_next_response(request, "my-access/index.html")


# Spanish alias for the same page so the navigation copy stays bilingual
# with the rest of the console.
@router.get("/mis-accesos", dependencies=[Depends(require_authenticated)])
async def mis_accesos_page():
    return RedirectResponse(url="/my-access", status_code=307)


# Sprint v1.5 — admin-only gate on the IAM / Settings / Operations panels
# in addition to the pre-existing permission check. Non-admin users with
# the permission (e.g. security_admin → iam.users.read) are now also
# rejected per the binary admin/non-admin policy the client demoed.
@router.get(
    "/iam",
    dependencies=[
        Depends(require_permission("iam.users.read")),
        Depends(require_admin),
    ],
)
async def iam_page():
    return RedirectResponse(url="/operations/users", status_code=307)


@router.get(
    "/settings",
    dependencies=[Depends(require_permission("settings.read")), Depends(require_admin)],
)
async def settings_page(request: Request):
    return _console_next_response(request, "settings/index.html")


@router.get(
    "/operations",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_admin),
    ],
)
async def operations_page(request: Request):
    return _console_next_response(request, "operations/index.html")


@router.get(
    "/operations/companies",
    dependencies=[Depends(PLATFORM_ADMIN)],
)
async def operations_companies_page(request: Request):
    return _console_next_response(request, "operations/companies/index.html")


@router.get(
    "/operations/users",
    dependencies=[Depends(require_permission("iam.users.read"))],
)
async def operations_users_page(request: Request):
    return _console_next_response(request, "operations/users/index.html")


@router.get(
    "/operations/audit",
    dependencies=[Depends(require_permission("security.audit.read"))],
)
async def operations_audit_page(request: Request):
    return _console_next_response(request, "operations/audit/index.html")


@router.get(
    "/operations/vault",
    dependencies=[Depends(require_permission("vault.connections.read"))],
)
async def operations_vault_page(request: Request):
    return _console_next_response(request, "operations/vault/index.html")


@router.get(
    "/operations/workflows",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_admin),
    ],
)
@router.get(
    "/operations/workflows/",
    dependencies=[
        Depends(require_permission("operations.read")),
        Depends(require_admin),
    ],
)
async def operations_workflows_page(request: Request):
    return _console_next_response(request, "operations/workflows/index.html")


@router.get(
    "/operations/metrics",
    dependencies=[Depends(require_permission("operations.read"))],
)
@router.get(
    "/operations/metrics/",
    dependencies=[Depends(require_permission("operations.read"))],
)
async def operations_metrics_page(request: Request):
    return _console_next_response(request, "operations/metrics/index.html")


# Viewer pages are operational read surfaces. They stay permission-gated so
# Monitor can deep-link into them without showing buttons the backend rejects.
@router.get("/viewer/jobs", dependencies=[Depends(require_permission("monitor.read"))])
async def viewer_jobs(request: Request):
    return _viewer_redirect(request, "jobs")


@router.get(
    "/viewer/jobs/{job_id}", dependencies=[Depends(require_permission("monitor.read"))]
)
async def viewer_job(job_id: str, request: Request):
    return _viewer_redirect(request, "job", id=job_id)


@router.get(
    "/viewer/schema", dependencies=[Depends(require_permission("datasets.read"))]
)
async def viewer_schema(request: Request):
    return _viewer_redirect(request, "schema")


@router.get(
    "/viewer/datasets", dependencies=[Depends(require_permission("datasets.read"))]
)
async def viewer_datasets(request: Request):
    return _viewer_redirect(request, "datasets")


@router.get(
    "/viewer/datasets/{name}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def viewer_dataset(name: str, request: Request):
    return _viewer_redirect(request, "dataset", name=name)


@router.get("/data", dependencies=[Depends(require_permission("datasets.read"))])
async def data_page():
    return RedirectResponse(url="/data/catalog", status_code=307)


@router.get("/data/", dependencies=[Depends(require_permission("datasets.read"))])
async def data_page_slash():
    return RedirectResponse(url="/data/catalog", status_code=307)


@router.get(
    "/data/catalog", dependencies=[Depends(require_permission("datasets.read"))]
)
@router.get(
    "/data/catalog/", dependencies=[Depends(require_permission("datasets.read"))]
)
async def data_catalog_page(request: Request):
    return _console_next_response(request, "data/catalog/index.html")


@router.get(
    "/data/lineage", dependencies=[Depends(require_permission("datasets.read"))]
)
@router.get(
    "/data/lineage/", dependencies=[Depends(require_permission("datasets.read"))]
)
async def data_lineage_page(request: Request):
    return _console_next_response(request, "data/lineage/index.html")


@router.get(
    "/data/bronze",
    dependencies=[
        Depends(require_permission("datasets.write")),
        Depends(require_admin),
    ],
)
@router.get(
    "/data/bronze/",
    dependencies=[
        Depends(require_permission("datasets.write")),
        Depends(require_admin),
    ],
)
async def data_bronze_page(request: Request):
    return _console_next_response(request, "data/bronze/index.html")


@router.get("/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def lineage_page(request: Request):
    return _viewer_redirect(request, "lineage")


@router.get("/linaje", dependencies=[Depends(require_permission("datasets.read"))])
async def linaje_page(request: Request):
    return _viewer_redirect(request, "lineage")


@router.get(
    "/viewer/semantic", dependencies=[Depends(require_permission("datasets.read"))]
)
async def viewer_semantic(request: Request):
    return _viewer_redirect(request, "semantic")


@router.get("/apps-gallery", dependencies=[Depends(require_permission("apps.read"))])
async def apps_gallery(request: Request):
    return _console_next_response(request, "apps-gallery/index.html")


# Sprint v1.41.0 — auditor P1 operativa: cartridge wizard page.
@router.get(
    "/cartridges",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def cartridges_page(request: Request):
    return _console_next_response(request, "cartridges/index.html")


@router.get(
    "/cartridges/viewer",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def cartridges_viewer_page(request: Request):
    return _console_next_response(request, "cartridges/viewer/index.html")


# Workspace shell: apps, decisions, datasets and assistant stay under the
# canonical console origin (:8000), while chat execution is proxied to the
# workspace service that already owns the consumer-assistant logic.
@router.get(
    "/workspace",
    dependencies=[Depends(require_permission("workspace.access"))],
)
async def workspace_page(request: Request):
    response = FileResponse(STATIC / "workspace.html")
    set_csrf_cookie(response, request.cookies.get(CSRF_COOKIE_NAME))
    return response


@router.post(
    "/workspace/chat",
    dependencies=[
        Depends(require_permission("workspace.access")),
        Depends(require_csrf),
    ],
)
async def workspace_chat_proxy(
    request: Request, user: dict = Depends(require_authenticated)
):
    body = await request.body()
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="workspace.chat.proxy",
        resource_type="workspace_chat",
        resource_id="sync",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        status="success",
        metadata={"body_len": len(body)},
    )
    return await _workspace_proxy(request, "/workspace/chat")


@router.post(
    "/workspace/chat/refresh-context",
    dependencies=[
        Depends(require_permission("workspace.access")),
        Depends(require_csrf),
    ],
)
async def workspace_refresh_proxy(
    request: Request, user: dict = Depends(require_authenticated)
):
    body = await request.body()
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="workspace.chat.refresh_context",
        resource_type="workspace_chat",
        resource_id="context",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        status="success",
        metadata={"body_len": len(body)},
    )
    return await _workspace_proxy(request, "/workspace/chat/refresh-context")


@router.post(
    "/workspace/chat/stream",
    dependencies=[
        Depends(require_permission("workspace.access")),
        Depends(require_csrf),
    ],
)
async def workspace_chat_stream_proxy(
    request: Request, user: dict = Depends(require_authenticated)
):
    body = await request.body()
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="workspace.chat.stream",
        resource_type="workspace_chat",
        resource_id="stream",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        status="success",
        metadata={"body_len": len(body)},
    )
    return await _workspace_stream_proxy(request, "/workspace/chat/stream")


@router.get(
    "/copilot",
    dependencies=[Depends(require_permission("copilot.use"))],
)
async def copilot_page(request: Request):
    return _console_next_response(request, "copilot/index.html")


@router.get(
    "/copilot/actions",
    dependencies=[Depends(require_permission("copilot.use"))],
)
@router.get(
    "/copilot/actions/",
    dependencies=[Depends(require_permission("copilot.use"))],
)
async def copilot_actions_page(request: Request):
    return _console_next_response(request, "copilot/actions/index.html")


@router.get(
    "/copilot/knowledge",
    dependencies=[
        Depends(require_permission("mcp.registry.read")),
        Depends(require_admin),
    ],
)
@router.get(
    "/copilot/knowledge/",
    dependencies=[
        Depends(require_permission("mcp.registry.read")),
        Depends(require_admin),
    ],
)
async def copilot_knowledge_page(request: Request):
    return _console_next_response(request, "copilot/knowledge/index.html")


@router.get(
    "/copilot/tokens",
    dependencies=[Depends(require_permission("copilot.use"))],
)
@router.get(
    "/copilot/tokens/",
    dependencies=[Depends(require_permission("copilot.use"))],
)
async def copilot_tokens_page(request: Request):
    return _console_next_response(request, "copilot/tokens/index.html")


@router.get("/viewer", dependencies=[Depends(_require_viewer_permission)])
async def viewer_page(request: Request):
    return _console_next_response(
        request, "viewer/index.html", frame_ancestors="'self'"
    )
