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

# /monitoring/mcp/tools
@router.get("/monitoring/mcp/tools")
@_bind_to_main
async def monitoring_mcp_tools(user: dict = Depends(_internal_or_authenticated)):
    """MCP-compatible tools endpoint so the registry can discover monitoring tools.

    Sprint v1.22: added auth. Tool descriptors include parameter
    schemas — an anonymous reader could enumerate the platform's MCP
    surface and target downstream attack research at it."""
    t = await monitoring_tools()
    return t  # already returns {"tools": [...]}

# /monitoring/mcp/invoke
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

# /studio_ops/mcp/tools
@router.get("/studio_ops/mcp/tools")
@_bind_to_main
async def studio_ops_tools(user: dict = Depends(_internal_or_authenticated)):
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    tools = [
        {
            "name": "rename_entity",
            "description": (
                "Rename an entity within a cartridge. "
                "Updates entity_config, entity_watermarks, pipeline_runs and silver_lineage atomically. "
                "Bronze files in MinIO keep their original path (historical data). "
                "Use this when the user asks to rename an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string", "description": "Cartridge ID, e.g. 'replicon'"},
                    "old_name":     {"type": "string", "description": "Current entity name"},
                    "new_name":     {"type": "string", "description": "New entity name"},
                },
                "required": ["cartridge_id", "old_name", "new_name"],
            },
        },
        {
            "name": "list_entities",
            "description": (
                "List all entities registered in a cartridge, including their mode, dag_id, "
                "trigger_type, and last pipeline run status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                },
                "required": ["cartridge_id"],
            },
        },
        {
            "name": "get_entity_logs",
            "description": (
                "Fetch the Airflow task logs for the most recent run of a specific entity. "
                "Use this when a DAG run failed and the user wants to diagnose the error. "
                "Returns the error message from pipeline_runs plus the full Airflow task log."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity":       {"type": "string"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "delete_entity",
            "description": (
                "Delete an entity from a cartridge. "
                "Removes it from entity_config and clears its watermarks. "
                "Pipeline run history is preserved for auditing. "
                "Use this when the user explicitly asks to delete or remove an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string", "description": "Cartridge ID, e.g. 'replicon'"},
                    "entity":       {"type": "string", "description": "Entity name to delete"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "update_entity",
            "description": (
                "Update one or more fields of an entity: display_name, mode (full|incremental), "
                "dag_id, trigger_type (manual|scheduled), cron_expression, description, enabled."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity":       {"type": "string"},
                    "display_name": {"type": "string"},
                    "mode":         {"type": "string", "enum": ["full", "incremental"]},
                    "dag_id":       {"type": "string"},
                    "connection_id": {"type": "string"},
                    "trigger_type": {"type": "string", "enum": ["manual", "scheduled"]},
                    "cron_expression": {"type": "string"},
                    "description":  {"type": "string"},
                    "enabled":      {"type": "boolean"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
    ]
    if _role_name(user) == ROLE_ANALYST:
        tools = [tool for tool in tools if tool["name"] not in STUDIO_OPS_WRITE_TOOLS]
    return {"tools": tools}

# /studio_ops/mcp/invoke
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

    if tool == "delete_entity":
        cartridge_id = args["cartridge_id"]
        entity       = args["entity"]
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])]
        if entity not in entities:
            return {"error": f"Entity '{entity}' not found in cartridge '{cartridge_id}'"}
        await cartridge_service.delete_entity(cartridge_id, entity)
        return {"deleted": True, "cartridge_id": cartridge_id, "entity": entity,
                "note": "Pipeline run history preserved. Bronze files in MinIO not removed."}

    if tool == "rename_entity":
        cartridge_id = args["cartridge_id"]
        old_name     = args["old_name"]
        new_name     = args["new_name"].strip()
        if not new_name or new_name == old_name:
            return {"renamed": False, "reason": "same name or empty"}
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])]
        if old_name not in entities:
            return {"error": f"Entity '{old_name}' not found"}
        if new_name in entities:
            return {"error": f"Entity '{new_name}' already exists"}
        await cartridge_service.rename_entity(cartridge_id, old_name, new_name)
        return {"renamed": True, "old_name": old_name, "new_name": new_name,
                "note": "Bronze files in MinIO remain at the old path — new extractions will use the new name."}

    if tool == "list_entities":
        cartridge_id = args["cartridge_id"]
        manifest     = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        runs_map = {}
        try:
            _pool = await _get_db_pool()
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 2)
            rows = await _pool.fetch(
                "SELECT DISTINCT ON (entity) entity, status, started_at, finished_at, record_count, error_message "
                f"FROM pipeline_runs WHERE cartridge_id=$1 {scope_sql} ORDER BY entity, started_at DESC",
                cartridge_id,
                *scope_values,
            )
            for r in rows:
                runs_map[r["entity"]] = {"status": r["status"],
                                         "started_at":  str(r["started_at"])[:16]  if r["started_at"]  else None,
                                         "finished_at": str(r["finished_at"])[:10] if r["finished_at"] else None,
                                         "record_count": r["record_count"],
                                         "error": r["error_message"]}
        except Exception:
            logger.debug("Could not load pipeline_runs for list_entities %s", cartridge_id, exc_info=True)
        entities = []
        for e in (manifest.get("entities") or []):
            name = e.get("entity") or e.get("id") or ""
            entities.append({
                "entity":       name,
                "display_name": e.get("display_name"),
                "mode":         e.get("mode", "full"),
                "dag_id":       e.get("dag_id"),
                "trigger_type": e.get("trigger_type", "manual"),
                "last_run":     runs_map.get(name),
            })
        return {"cartridge_id": cartridge_id, "entities": entities, "count": len(entities)}

    if tool == "get_entity_logs":
        cartridge_id = args["cartridge_id"]
        entity       = args["entity"]

        # 1. Last pipeline_run for this entity — includes airflow_dag_run_id stored by the DAG
        last_run = None
        try:
            _pool = await _get_db_pool()
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
            row  = await _pool.fetchrow(
                "SELECT dag_id, airflow_dag_run_id, status, mode, "
                "       started_at, finished_at, record_count, error_message, extra "
                "FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                f"{scope_sql} ORDER BY started_at DESC LIMIT 1",
                cartridge_id, entity, *scope_values,
            )
            if row:
                last_run = dict(row)
        except Exception:
            _eid = uuid.uuid4().hex
            logger.exception("get_entity_logs DB query failed error_id=%s", _eid)
            return {"error": f"Internal server error. error_id={_eid}"}

        if not last_run:
            return {"error": f"No pipeline runs found for {cartridge_id}/{entity}"}

        dag_id = last_run.get("dag_id") or f"{cartridge_id}_extract"

        # 2. Resolve Airflow dag_run_id — prefer the stored value, fall back to list+match
        airflow_run_id = last_run.get("airflow_dag_run_id")
        airflow_logs   = None
        try:
            if not airflow_run_id:
                # Fallback for older runs that predate the airflow_dag_run_id column:
                # match by conf.entity against recent Airflow runs
                runs_r  = await mcp_registry.invoke("infra", "airflow_list_dag_runs",
                                                    {"dag_id": dag_id, "limit": 20},
                                                    user=user)
                af_runs = (runs_r or {}).get("runs", [])
                started_str = str(last_run.get("started_at", ""))[:10]
                for run in af_runs:
                    conf_entity = (run.get("conf") or {}).get("entity", "")
                    run_date    = (run.get("start_date") or "")[:10]
                    if conf_entity == entity and run_date == started_str:
                        airflow_run_id = run["dag_run_id"]
                        break
                # Last resort: most recent run of that DAG
                if not airflow_run_id and af_runs:
                    airflow_run_id = af_runs[0]["dag_run_id"]

            if airflow_run_id:
                logs_r = await mcp_registry.invoke("infra", "airflow_get_task_logs",
                                                   {"dag_id":     dag_id,
                                                    "dag_run_id": airflow_run_id,
                                                    "task_id":    "extract"},
                                                   user=user)
                airflow_logs = (logs_r or {}).get("logs", "")
        except Exception:
            logger.debug("Airflow log fetch failed for %s/%s", cartridge_id, entity, exc_info=True)
            airflow_logs = "(No se pudieron obtener logs de Airflow)"

        return {
            "entity":        entity,
            "cartridge_id":  cartridge_id,
            "dag_id":        dag_id,
            "dag_run_id":    airflow_run_id,
            "status":        last_run["status"],
            "started_at":    str(last_run.get("started_at",""))[:19],
            "error_message": last_run.get("error_message"),
            "airflow_logs":  airflow_logs,
        }

    if tool == "update_entity":
        cartridge_id = args.pop("cartridge_id")
        entity       = args.pop("entity")
        if not args:
            return {"error": "No fields to update"}
        await cartridge_service.upsert_entity(cartridge_id, entity, **args)
        return {"updated": True, "entity": entity, **args}

    raise HTTPException(400, f"Unknown tool: {tool}")

# /monitoring/tools
@router.get("/monitoring/tools", dependencies=[Depends(require_permission("monitor.read"))])
@_bind_to_main
async def monitoring_tools(user: dict = Depends(require_permission("monitor.read"))):
    # Sprint v1.22: same rationale as /monitoring/mcp/tools — tool
    # discovery should be authenticated.
    return {"tools": [
        {
            "name": "view_job",
            "description": (
                "Genera un deeplink para visualizar el detalle de un job: "
                "status, progreso, logs linea a linea por entidad. "
                "Retorna una URL que el usuario puede abrir directamente."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "ID del job"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "view_jobs",
            "description": "Genera un deeplink para ver todos los jobs recientes con su estado.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_schema",
            "description": (
                "Genera un deeplink para visualizar el schema de una fuente Bronze: "
                "columnas, tipos, particiones disponibles y preview de filas."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string",
                               "description": "Ruta de la fuente, e.g. 'raw/replicon/TimeEntry'"},
                },
                "required": ["source"],
            },
        },
        {
            "name": "view_dataset",
            "description": (
                "Genera un deeplink para visualizar un dataset Silver/Gold: "
                "SQL, column mapping (terminos de negocio), lineage e historial y preview."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del dataset"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "view_datasets",
            "description": "Genera un deeplink para ver todos los datasets Silver/Gold definidos.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_semantic",
            "description": (
                "Genera un deeplink para visualizar el modelo semantico de un cartucho: "
                "entidades, campos, modos de extraccion, watermarks y relaciones."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge": {"type": "string", "default": "replicon"},
                },
            },
        },
        {
            "name": "view_pipeline",
            "description": (
                "Genera un deeplink para el Pipeline Monitor DAG: vista completa del flujo "
                "Entidad → Bronze → Silver → Gold con estado de frescura y botones de extracción. "
                "Úsalo cuando el usuario pregunte por el estado del pipeline, quiera ver qué "
                "está desactualizado, o quiera extraer/refrescar datos."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
    ]}

# /monitoring/invoke
@router.post("/monitoring/invoke", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def monitoring_invoke(body: dict, user: dict = Depends(require_authenticated)):
    # Sprint v1.22: was reachable without any auth. monitoring tools
    # read job state and DAG metadata, which a session-less caller has
    # no business seeing. CSRF added because this is a state-shaped
    # POST and could be called from a cross-origin form otherwise.
    tool = body.get("tool")
    args = body.get("args", {})
    _require_effective_permission(user, "monitor.read")

    if tool == "view_job":
        job_id = args["job_id"]
        job = await job_service.get_scoped(job_id, user=user)
        entity = (job.get("args") or {}).get("entity", "")
        return {
            "url":     f"{CONSOLE_URL}/viewer?type=job&id={quote(str(job_id), safe='')}",
            "label":   f"Ver job {job_id}" + (f" — {entity}" if entity else ""),
            "status":  job.get("status", "unknown"),
            "message": job.get("message", ""),
        }

    if tool == "view_jobs":
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=jobs",
            "label": "Ver todos los jobs",
        }

    if tool == "view_schema":
        source = args["source"]
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=schema&source={quote(str(source), safe='')}",
            "label": f"Ver schema de {source}",
        }

    if tool == "view_dataset":
        name = args["name"]
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=dataset&name={quote(str(name), safe='')}",
            "label": f"Ver dataset {name}",
        }

    if tool == "view_datasets":
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=datasets",
            "label": "Ver todos los datasets",
        }

    if tool == "view_semantic":
        cartridge = args.get("cartridge", "replicon")
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=semantic&cartridge={quote(str(cartridge), safe='')}",
            "label": f"Ver modelo semantico de {cartridge}",
        }

    if tool == "view_pipeline":
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=pipeline",
            "label": "Pipeline Monitor — Bronze → Silver → Gold",
        }

    raise HTTPException(400, f"Unknown tool: {tool}")

# /api/dags/parse
@router.post("/api/dags/parse", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def api_dag_parse(body: dict, user: dict = Depends(require_authenticated)):
    # Sprint v1.22: parsing arbitrary Python source is non-trivial work
    # and an anonymous caller could DOS the parser. Auth + CSRF required.
    source = body.get("source", "")
    if not source:
        raise HTTPException(400, "source is required")
    return _parse_dag_graph(source)
