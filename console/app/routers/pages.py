from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter(tags=["Pages"])


@router.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@router.get("/monitor")
async def monitor_page():
    return FileResponse(STATIC / "monitor.html")


@router.get("/viewer/jobs")
async def viewer_jobs():
    return FileResponse(STATIC / "viewers" / "jobs.html")


@router.get("/viewer/jobs/{job_id}")
async def viewer_job(job_id: str):
    return FileResponse(STATIC / "viewers" / "job.html")


@router.get("/viewer/schema")
async def viewer_schema():
    return FileResponse(STATIC / "viewers" / "schema.html")


@router.get("/viewer/datasets")
async def viewer_datasets():
    return FileResponse(STATIC / "viewers" / "datasets.html")


@router.get("/viewer/datasets/{name}")
async def viewer_dataset(name: str):
    return FileResponse(STATIC / "viewers" / "dataset.html")


@router.get("/viewer/semantic")
async def viewer_semantic():
    return FileResponse(STATIC / "viewers" / "semantic.html")


@router.get("/apps-gallery")
async def apps_gallery():
    return FileResponse(STATIC / "apps_gallery.html")
