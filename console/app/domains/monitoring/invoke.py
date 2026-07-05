from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import HTTPException


async def invoke_monitoring_tool(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    job_service: Any,
    console_url: str,
) -> dict[str, Any]:
    tool = body.get("tool")
    args = body.get("args") or {}
    if not isinstance(args, dict):
        raise HTTPException(400, "args must be an object")

    if tool == "view_job":
        job_id = args.get("job_id")
        if not job_id:
            raise HTTPException(400, "job_id is required")
        job = await job_service.get_scoped(job_id, user=user)
        entity = (job.get("args") or {}).get("entity", "")
        return {
            "url": f"{console_url}/viewer?type=job&id={quote(str(job_id), safe='')}",
            "label": f"Ver job {job_id}" + (f" — {entity}" if entity else ""),
            "status": job.get("status", "unknown"),
            "message": job.get("message", ""),
        }

    if tool == "view_jobs":
        return {
            "url": f"{console_url}/viewer?type=jobs",
            "label": "Ver todos los jobs",
        }

    if tool == "view_schema":
        source = args.get("source")
        if not source:
            raise HTTPException(400, "source is required")
        return {
            "url": f"{console_url}/viewer?type=schema&source={quote(str(source), safe='')}",
            "label": f"Ver schema de {source}",
        }

    if tool == "view_dataset":
        name = args.get("name")
        if not name:
            raise HTTPException(400, "name is required")
        return {
            "url": f"{console_url}/viewer?type=dataset&name={quote(str(name), safe='')}",
            "label": f"Ver dataset {name}",
        }

    if tool == "view_datasets":
        return {
            "url": f"{console_url}/viewer?type=datasets",
            "label": "Ver todos los datasets",
        }

    if tool == "view_semantic":
        cartridge = args.get("cartridge", "replicon")
        return {
            "url": f"{console_url}/viewer?type=semantic&cartridge={quote(str(cartridge), safe='')}",
            "label": f"Ver modelo semantico de {cartridge}",
        }

    if tool == "view_pipeline":
        return {
            "url": f"{console_url}/viewer?type=pipeline",
            "label": "Pipeline Monitor — Bronze → Silver → Gold",
        }

    raise HTTPException(400, f"Unknown tool: {tool}")
