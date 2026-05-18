from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.dependencies import require_admin
from app.dependencies import require_authenticated
from app.services import audit_service
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


STATIC = Path(__file__).resolve().parents[1] / "static"
WORKSPACE_INTERNAL_URL = os.environ.get("WORKSPACE_INTERNAL_URL", "http://workspace:8001").rstrip("/")

router = APIRouter(tags=["Pages"])


def _workspace_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in ("accept", "content-type", "cookie", "x-request-id"):
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
        raise HTTPException(status_code=502, detail=f"Workspace service unavailable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def _workspace_stream_proxy(request: Request, path: str) -> StreamingResponse | Response:
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
        raise HTTPException(status_code=502, detail=f"Workspace service unavailable: {exc}") from exc

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
    return FileResponse(STATIC / "index.html")


@router.get("/monitor", dependencies=[Depends(require_permission("monitor.read"))])
async def monitor_page():
    return FileResponse(STATIC / "monitor.html")


@router.get("/security", dependencies=[Depends(require_permission("security.audit.read"))])
async def security_page():
    return FileResponse(STATIC / "security.html")


# Sprint v1.5 — admin-only gate on the IAM / Settings / Operations panels
# in addition to the pre-existing permission check. Non-admin users with
# the permission (e.g. security_admin → iam.users.read) are now also
# rejected per the binary admin/non-admin policy the client demoed.
@router.get(
    "/iam",
    dependencies=[Depends(require_permission("iam.users.read")), Depends(require_admin)],
)
async def iam_page():
    return FileResponse(STATIC / "iam.html")


@router.get(
    "/settings",
    dependencies=[Depends(require_permission("settings.read")), Depends(require_admin)],
)
async def settings_page():
    return FileResponse(STATIC / "settings.html")


@router.get(
    "/operations",
    dependencies=[Depends(require_permission("operations.read")), Depends(require_admin)],
)
async def operations_page():
    return FileResponse(STATIC / "operations.html")


# Sprint v1.5 — viewer pages listed by the spec (jobs / datasets / semantic)
# go admin-only. The /{job_id} and /{name} variants follow their parents to
# keep the surface uniform. /viewer/schema exposes dataset schema and follows
# the same admin-only viewer policy.
@router.get("/viewer/jobs", dependencies=[Depends(require_admin)])
async def viewer_jobs():
    return FileResponse(STATIC / "viewers" / "jobs.html")


@router.get("/viewer/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def viewer_job(job_id: str):
    return FileResponse(STATIC / "viewers" / "job.html")


@router.get("/viewer/schema", dependencies=[Depends(require_admin)])
async def viewer_schema():
    return FileResponse(STATIC / "viewers" / "schema.html")


@router.get("/viewer/datasets", dependencies=[Depends(require_admin)])
async def viewer_datasets():
    return FileResponse(STATIC / "viewers" / "datasets.html")


@router.get("/viewer/datasets/{name}", dependencies=[Depends(require_admin)])
async def viewer_dataset(name: str):
    return FileResponse(STATIC / "viewers" / "dataset.html")


@router.get("/viewer/semantic", dependencies=[Depends(require_admin)])
async def viewer_semantic():
    return FileResponse(STATIC / "viewers" / "semantic.html")


@router.get("/apps-gallery")
async def apps_gallery():
    return FileResponse(STATIC / "apps_gallery.html")


# Sprint v1.41.0 — auditor P1 operativa: cartridge wizard page.
@router.get(
    "/cartridges",
    dependencies=[Depends(require_permission("cartridges.read")), Depends(require_admin)],
)
async def cartridges_page():
    # The standalone /cartridges credentials UI duplicated the better
    # Vault flow. Keep the URL as a compatibility alias, but make the
    # canonical credential editor /viewer/vault.
    return RedirectResponse(url="/viewer/vault", status_code=307)


# Workspace shell: apps, decisions, datasets and assistant stay under the
# canonical console origin (:8000), while chat execution is proxied to the
# workspace service that already owns the consumer-assistant logic.
@router.get(
    "/workspace",
    dependencies=[Depends(require_permission("workspace.access"))],
)
async def workspace_page():
    # Canonical :8000 workspace surface: apps, decisions, datasets and the
    # consumer assistant. It is served by console so users stay in the base UI.
    return FileResponse(STATIC / "workspace.html")


@router.post(
    "/workspace/chat",
    dependencies=[Depends(require_permission("workspace.access")), Depends(require_csrf)],
)
async def workspace_chat_proxy(request: Request, user: dict = Depends(require_authenticated)):
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
    dependencies=[Depends(require_permission("workspace.access")), Depends(require_csrf)],
)
async def workspace_refresh_proxy(request: Request, user: dict = Depends(require_authenticated)):
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
    dependencies=[Depends(require_permission("workspace.access")), Depends(require_csrf)],
)
async def workspace_chat_stream_proxy(request: Request, user: dict = Depends(require_authenticated)):
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
async def copilot_page():
    return FileResponse(STATIC / "copilot.html")
