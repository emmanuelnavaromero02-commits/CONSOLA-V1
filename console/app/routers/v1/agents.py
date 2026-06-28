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

# /agents
@router.get("/agents", dependencies=[Depends(require_permission("agents.read"))])
@_bind_to_main
async def viewer_agents(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "agents/index.html")

# /api/agents
@router.get("/api/agents", dependencies=[Depends(require_permission("agents.read"))])
@_bind_to_main
async def api_agents_list(
    request: Request,
    cartridge_id: str | None = None,
    include_inactive: bool = False,
    user: dict = Depends(require_permission("agents.read")),
):
    return {"agents": await _agents.list_agents(cartridge_id, include_inactive, user_context=user)}

# /api/agents/_tool-catalog
@router.get("/api/agents/_tool-catalog", dependencies=[Depends(require_permission("agents.read"))])
@_bind_to_main
async def api_agents_tool_catalog(request: Request):
    """Aggregate of tools exposed by every MCP server — used by the agent
    editor UI to populate the 'allowed_tools' multi-select."""
    out: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for srv_id, base in _agent_runtime.SERVER_URLS.items():
            try:
                server_key = "MCP_INFRA" if srv_id == "mcp-infra" else srv_id.upper()
                r = await c.get(f"{base}/mcp/tools", headers=_hdr_for(server_key))
                r.raise_for_status()
                out[srv_id] = [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        **tool_manifest.classify_tool(t["name"]),
                    }
                    for t in (r.json().get("tools") or [])
                ]
            except Exception:
                out[srv_id] = []
    return {"servers": out}

# /api/agents/{agent_id}
@router.get("/api/agents/{agent_id}", dependencies=[Depends(require_permission("agents.read"))])
@_bind_to_main
async def api_agents_get(request: Request, agent_id: str, user: dict = Depends(require_permission("agents.read"))):
    a = await _agents.get_agent(agent_id, user_context=user)
    if not a:
        raise HTTPException(404, "agent not found")
    return a

# /api/agents
@router.post("/api/agents", dependencies=[Depends(require_csrf), Depends(require_permission("agents.write"))])
@_bind_to_main
async def api_agents_create(request: Request, body: dict, user: dict = Depends(require_permission("agents.write"))):
    try:
        return await _agents.create_agent(body, owner_user_id=user.get("id"), user_context=user)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, "invalid agent payload") from exc

# /api/agents/{agent_id}
@router.patch("/api/agents/{agent_id}", dependencies=[Depends(require_csrf), Depends(require_permission("agents.write"))])
@_bind_to_main
async def api_agents_update(request: Request, agent_id: str, body: dict, user: dict = Depends(require_permission("agents.write"))):
    try:
        a = await _agents.update_agent(agent_id, body, user_context=user)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, "invalid agent update") from exc
    if not a:
        raise HTTPException(404, "agent not found")
    return a

# /api/agents/{agent_id}
@router.delete("/api/agents/{agent_id}", dependencies=[Depends(require_csrf), Depends(require_permission("agents.write"))])
@_bind_to_main
async def api_agents_delete(request: Request, agent_id: str, user: dict = Depends(require_permission("agents.write"))):
    try:
        ok = await _agents.delete_agent(agent_id, user_context=user)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "agent not found")
    return {"deleted": True}

# /api/agents/{agent_id}/invoke
@router.post("/api/agents/{agent_id}/invoke", dependencies=[Depends(require_csrf), Depends(require_permission("agents.execute"))])
@_bind_to_main
async def api_agents_invoke(request: Request, agent_id: str, body: dict, user: dict = Depends(require_permission("agents.execute"))):
    visible = await _agents.get_agent(agent_id, user_context=user)
    if not visible:
        raise HTTPException(404, "agent not found")
    agent = await _agent_runtime.load_agent(agent_id, user_context=user)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []
    result = await _agent_runtime.run(agent, message, history=history, user=user)
    return result

# /api/agents/{agent_id}/invoke/scheduled
@router.post("/api/agents/{agent_id}/invoke/scheduled", dependencies=[Depends(verify_internal_api_key)])
@_bind_to_main
async def api_agents_invoke_scheduled(request: Request, agent_id: str, body: dict):
    """Cron-driven invocation from the airflow `agent_runner` DAG. Uses a
    shared token so it can run without a user session. The agent_runs row
    is logged with user_id=NULL."""
    token = request.headers.get("X-Agent-Runner-Token", "")
    if not _AGENT_RUNNER_TOKEN or not secrets.compare_digest(token, _AGENT_RUNNER_TOKEN):
        raise HTTPException(401, "invalid runner token")
    scheduled_scope = {
        "tenant_id": str(body.get("tenant_id") or "").strip() or None,
        "workspace_id": str(body.get("workspace_id") or "").strip() or None,
    }
    scheduled_user_context = scheduled_scope if scheduled_scope.get("workspace_id") else None
    agent = await _agent_runtime.load_agent(agent_id, user_context=scheduled_user_context)
    if not agent:
        raise HTTPException(404, "agent not found")
    if not (
        str(getattr(agent, "tenant_id", None) or "").strip()
        and str(getattr(agent, "workspace_id", None) or "").strip()
    ):
        raise HTTPException(403, "scheduled agent requires tenant/workspace scope")
    extra = getattr(agent, "extra", None) or {}
    schedule = extra.get("schedule") if isinstance(extra, dict) else {}
    if not isinstance(schedule, dict) or schedule.get("enabled") is False:
        raise HTTPException(403, "agent schedule is not enabled")
    if not (str(schedule.get("cron") or schedule.get("cron_expression") or "").strip()):
        raise HTTPException(403, "agent schedule cron is required")
    scheduled_fire_at = _parse_agent_scheduled_fire_at(body.get("scheduled_fire_at"))
    if not _agent_schedule_due(schedule, scheduled_fire_at=scheduled_fire_at):
        raise HTTPException(403, "agent schedule is not due")
    if scheduled_fire_at is None:
        from datetime import datetime as _dt, timezone as _tz

        scheduled_fire_at = _dt.now(_tz.utc).replace(second=0, microsecond=0)
    schedule_key = str(body.get("schedule_key") or schedule.get("key") or "default").strip() or "default"
    extra_role = str((extra or {}).get("role") or "").strip().lower()
    monitor_contract = extra.get("monitor") if isinstance(extra, dict) else None
    if extra_role != "monitor" or not isinstance(monitor_contract, dict) or not monitor_contract:
        raise HTTPException(403, "scheduled agents require monitor role and monitor contract")
    airflow_dag_run_id = str(body.get("airflow_dag_run_id") or "").strip() or None
    reservation = await _agent_scheduler.reserve_scheduled_run(
        agent_id=str(agent.id),
        tenant_id=str(getattr(agent, "tenant_id", "")),
        workspace_id=str(getattr(agent, "workspace_id", "")),
        scheduled_fire_at=scheduled_fire_at,
        schedule_key=schedule_key,
        airflow_dag_run_id=airflow_dag_run_id,
        metadata={
            "agent_slug": agent.slug,
            "cartridge_id": agent.cartridge_id,
            "airflow_dag_run_id": airflow_dag_run_id,
        },
    )
    if reservation.get("duplicate"):
        return {
            "reply": "scheduled run already recorded",
            "viewer_urls": [],
            "messages": [],
            "agent_id": agent.id,
            "run_id": reservation.get("agent_run_id"),
            "duplicate": True,
            "schedule_run": reservation,
        }
    message = (body.get("message") or "").strip() or "Ejecuta tu tarea programada."
    try:
        result = await _agent_runtime.run_scheduled_monitor(
            agent,
            message,
            scheduled_fire_at=scheduled_fire_at.isoformat(),
        )
    except Exception as exc:
        await _agent_scheduler.finish_scheduled_run(
            schedule_run_id=reservation.get("id"),
            agent_run_id=None,
            status="error",
            tenant_id=str(getattr(agent, "tenant_id", "")),
            workspace_id=str(getattr(agent, "workspace_id", "")),
            error_message=f"{type(exc).__name__}: {exc}",
            metadata={"airflow_dag_run_id": airflow_dag_run_id},
        )
        raise
    await _agent_scheduler.finish_scheduled_run(
        schedule_run_id=reservation.get("id"),
        agent_run_id=result.get("run_id") if isinstance(result, dict) else None,
        status="ok",
        tenant_id=str(getattr(agent, "tenant_id", "")),
        workspace_id=str(getattr(agent, "workspace_id", "")),
        metadata={
            "airflow_dag_run_id": airflow_dag_run_id,
            "deterministic_monitor": bool(
                isinstance(result, dict) and result.get("deterministic_monitor")
            ),
        },
    )
    if isinstance(result, dict):
        result["schedule_run"] = reservation
    return result

# /api/agents/{agent_id}/invoke/stream
@router.post("/api/agents/{agent_id}/invoke/stream", dependencies=[Depends(require_csrf), Depends(require_permission("agents.execute"))])
@_bind_to_main
async def api_agents_invoke_stream(request: Request, agent_id: str, body: dict, user: dict = Depends(require_permission("agents.execute"))):
    """Server-Sent Events stream of tool_use / tool_result / text events."""
    visible = await _agents.get_agent(agent_id, user_context=user)
    if not visible:
        raise HTTPException(404, "agent not found")
    agent = await _agent_runtime.load_agent(agent_id, user_context=user)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev: dict):
        await queue.put(ev)

    async def runner():
        try:
            result = await _agent_runtime.run(agent, message, history=history,
                                              user=user, on_event=on_event)
            await queue.put({"type": "done", "run_id": result.get("run_id")})
        except Exception as exc:                                # noqa: BLE001
            request_id = _internal_error_request_id()
            logger.exception("agent runtime streaming failed request_id=%s", request_id)
            await queue.put({"type": "error", "message": "Internal Server Error", "request_id": request_id})
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen():
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")

# /api/agents/{agent_id}/runs
@router.get("/api/agents/{agent_id}/runs", dependencies=[Depends(require_permission("agents.read"))])
@_bind_to_main
async def api_agents_runs(request: Request, agent_id: str, limit: int = 20, user: dict = Depends(require_permission("agents.read"))):
    return {"runs": await _agents.list_runs(agent_id, limit=limit, user_context=user)}

# /api/agent-runs/{run_id}
@router.get("/api/agent-runs/{run_id}", dependencies=[Depends(require_permission("agents.read"))])
@_bind_to_main
async def api_agent_run_detail(request: Request, run_id: int, user: dict = Depends(require_permission("agents.read"))):
    run = await _agents.get_run(run_id, user_context=user)
    if not run:
        raise HTTPException(404, "run not found")
    return run
