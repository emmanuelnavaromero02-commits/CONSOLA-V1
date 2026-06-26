from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

# Import the current console runtime namespace, including private helper
# functions used by legacy handlers. Handlers are rebound to app.main's
# namespace before registration so existing tests and monkeypatches that
# patch app.main.<helper> continue to affect the handler at runtime.
globals().update(_console_main.__dict__)
router = APIRouter()


def _bind_to_main(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _console_main.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _console_main.__name__
    _console_main.__dict__[fn.__name__] = rebound
    return rebound

# /jobs
@router.get("/jobs", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def list_jobs(limit: int = 20, user: dict = Depends(require_authenticated)):
    jobs = await _call_with_optional_user(job_service.list_recent, limit, user=user)
    return {"jobs": await _refresh_pipeline_job_payloads(jobs, user)}

# /jobs/{job_id}
@router.get("/jobs/{job_id}", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def get_job(job_id: str, user: dict = Depends(require_authenticated)):
    job = await job_service.get_scoped(job_id, user=user)
    return await _refresh_pipeline_job_payload(job, user)

# /assistant/chat
@router.post("/assistant/chat", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def chat(body: dict, user: dict = Depends(require_authenticated)):
    return await _call_with_optional_user(
        assistant.chat,
        body.get("message", ""),
        body.get("history", []),
        user=user,
    )

# /api/jobs
@router.get("/api/jobs", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_jobs(limit: int = 50, user: dict = Depends(require_authenticated)):
    jobs = await _call_with_optional_user(job_service.list_recent, limit, user=user)
    return {"jobs": await _refresh_pipeline_job_payloads(jobs, user)}

# /api/jobs/{job_id}
@router.get("/api/jobs/{job_id}", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_job(job_id: str, user: dict = Depends(require_authenticated)):
    job = await job_service.get_scoped(job_id, user=user)
    return await _refresh_pipeline_job_payload(job, user)

# /api/jobs/{job_id}/logs
@router.get("/api/jobs/{job_id}/logs", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_job_logs(job_id: str, limit: int = 200, user: dict = Depends(require_authenticated)):
    import json as _json
    scoped = await job_service.get_scoped(job_id, user=user)
    if scoped.get("error"):
        raise HTTPException(404, "job not found")
    job_args = scoped.get("args") if isinstance(scoped.get("args"), dict) else {}
    job_result = scoped.get("result") if isinstance(scoped.get("result"), dict) else {}
    cartridge = str(
        job_args.get("cartridge_id")
        or job_args.get("cartridge")
        or job_result.get("cartridge_id")
        or job_result.get("cartridge")
        or ""
    ).strip()
    if not cartridge:
        raise HTTPException(422, "job cartridge is unavailable; cannot resolve scoped logs")
    pool = await _get_db_pool()
    rows = await pool.fetch(
        "SELECT entity, level, message, detail, ts FROM run_logs "
        "WHERE run_id=$1 AND cartridge=$2 ORDER BY ts ASC LIMIT $3",
        job_id, cartridge, limit
    )
    result = []
    for row in rows:
        detail = row["detail"]
        if isinstance(detail, str):
            try:
                detail = _json.loads(detail)
            except (json.JSONDecodeError, ValueError):
                pass
        result.append({
            "ts": row["ts"].isoformat(),
            "entity": row["entity"],
            "level": row["level"],
            "message": row["message"],
            "detail": detail,
        })
    return {"logs": result}

# /api/tools/manifest
@router.get("/api/tools/manifest", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_tools_manifest():
    """Sprint v1.41.0 (tornillo copilot): unified tool catalog with risk_level
    + requires_approval, sourced from every registered MCP server. The copilot
    router (v1.42+) consumes this to decide auto-execution vs approval prompts."""
    from app.services.tool_manifest import build_manifest
    return await build_manifest()
