from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

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

@router.get("/monitoring/mcp/tools")
@_bind_to_main
async def monitoring_mcp_tools(user: dict = Depends(_internal_or_authenticated)):
    """MCP-compatible tools endpoint so the registry can discover monitoring tools.

    Sprint v1.22: added auth. Tool descriptors include parameter
    schemas — an anonymous reader could enumerate the platform's MCP
    surface and target downstream attack research at it."""
    t = await monitoring_tools()
    return t

@router.post("/monitoring/mcp/invoke")
@_bind_to_main
async def monitoring_mcp_invoke(body: dict, user: dict = Depends(_internal_or_authenticated)):
    """MCP-compatible invoke endpoint so the assistant can call monitoring tools.

    Sprint v1.21 (F1): added require_authenticated. This route is the
    MCP entry point for the monitoring toolset (view_job, view_schema,
    etc.) and was previously reachable without a session — an
    unauthenticated caller could enumerate jobs and read schema metadata.
    The underlying monitoring_invoke() handler did not check the cookie
    on its own, so the dependency is the single chokepoint.
    """
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "monitor.read")
    return await monitoring_invoke(body, user=user)

@router.get("/studio_ops/mcp/tools")
@_bind_to_main
async def studio_ops_tools(user: dict = Depends(_internal_or_authenticated)):
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    tools = build_studio_ops_tools()
    if _role_name(user) == ROLE_ANALYST:
        tools = [tool for tool in tools if tool["name"] not in STUDIO_OPS_WRITE_TOOLS]
    return {"tools": tools}

@router.post("/studio_ops/mcp/invoke", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def studio_ops_invoke(body: dict, user: dict = Depends(_internal_or_authenticated)):
    tool = body.get("tool")
    args = body.get("args", {})
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    cartridge_id_arg = str((args or {}).get("cartridge_id") or "").strip()
    if cartridge_id_arg:
        _require_cartridge_visible(user, cartridge_id_arg)

    if tool in STUDIO_OPS_WRITE_TOOLS:
        _require_studio_ops_write_role(user)

    return await _invoke_studio_ops_tool_impl(
        tool=tool,
        args=args or {},
        user=user,
        cartridge_service=cartridge_service,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=_pipeline_runs_read_conn,
        mcp_registry=mcp_registry,
        airflow_log_attempt=_airflow_log_attempt,
        airflow_log_task_ids=_airflow_log_task_ids,
        uuid_factory=uuid.uuid4,
        logger_debug=logger.debug,
        logger_exception=logger.exception,
    )

@router.get("/monitoring/tools", dependencies=[Depends(require_permission("monitor.read"))])
@_bind_to_main
async def monitoring_tools(user: dict = Depends(require_permission("monitor.read"))):
    return {"tools": build_monitoring_tools()}

@router.post(
    "/monitoring/invoke",
    dependencies=[Depends(require_csrf), Depends(require_permission("monitor.read"))],
)
@_bind_to_main
async def monitoring_invoke(body: dict, user: dict = Depends(require_permission("monitor.read"))):
    _require_effective_permission(user, "monitor.read")
    return await _invoke_monitoring_tool_impl(
        body=body,
        user=user,
        job_service=job_service,
        console_url=CONSOLE_URL,
    )

@router.post(
    "/api/dags/parse",
    dependencies=[Depends(require_csrf), Depends(require_permission("studio.read"))],
)
@_bind_to_main
async def api_dag_parse(body: dict, user: dict = Depends(require_permission("studio.read"))):
    source = body.get("source", "")
    if not source:
        raise HTTPException(400, "source is required")
    return _parse_dag_graph(source)
