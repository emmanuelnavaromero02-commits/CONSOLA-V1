from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.dependencies import require_admin
from app.services.permissions import require_permission


STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter(tags=["Pages"])


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
# keep the surface uniform. /viewer/schema is intentionally NOT in the
# explicit spec list, so it stays open to authenticated callers.
@router.get("/viewer/jobs", dependencies=[Depends(require_admin)])
async def viewer_jobs():
    return FileResponse(STATIC / "viewers" / "jobs.html")


@router.get("/viewer/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def viewer_job(job_id: str):
    return FileResponse(STATIC / "viewers" / "job.html")


@router.get("/viewer/schema")
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
    return FileResponse(STATIC / "cartridges.html")
