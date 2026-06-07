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

# /studio/cartridges/{cartridge_id}/connections
@router.get("/studio/cartridges/{cartridge_id}/connections", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def studio_cartridge_connections(cartridge_id: str, user: dict = Depends(require_authenticated)):
    """Proxy to Vault — returns masked connection config for the cartridge."""
    _require_cartridge_visible(user, cartridge_id)
    vault_url = _vault_url()
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        try:
            r = await c.get(f"{vault_url}/connections/{quote(cartridge_id, safe='')}")
            if r.status_code in (404, 204):
                return {"connections": []}
            if r.status_code >= 500:
                return {"connections": []}
            return r.json()
        except (httpx.HTTPError, ValueError):
            return {"connections": []}

# /api/pipeline
@router.get("/api/pipeline", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_pipeline(cartridge: str = "replicon", user: dict = Depends(require_authenticated)):
    """
    Ensambla el DAG completo: entidades × bronze status × silver datasets × gold deps.
    Fuentes: entity_config (entities), pipeline_runs + jobs (run history), refinement (datasets).
    """
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    import re as _re
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    def _freshness(dt_str: str | None, threshold_h: int = 24) -> str:
        if not dt_str:
            return "never"
        try:
            dt = _dt.fromisoformat(str(dt_str).replace("Z", "+00:00"))
            age = _dt.now(_tz.utc) - dt
            return "fresh" if age < _td(hours=threshold_h) else "stale"
        except Exception:
            return "unknown"

    # 1. Entities
    from app.services import cartridge_service as _cs
    entity_list: list[dict] = []
    manifest = await _cs.get_cartridge(cartridge)
    if manifest:
        for e in (manifest.get("entities") or []):
            entity_list.append({
                "entity":          e.get("id") or e.get("entity") or "",
                "mode":            e.get("mode", "full"),
                "watermark_field": e.get("watermark_field"),
                "description":     e.get("description", ""),
            })
    if not entity_list:
        entities_raw = await mcp_registry.invoke(cartridge, "list_entities", {}, user=user)
        if isinstance(entities_raw, dict):
            entity_list = entities_raw.get("entities", entities_raw.get("result", []))
        elif isinstance(entities_raw, list):
            entity_list = entities_raw

    # 2a. pipeline_runs — most recent run per entity (written by Airflow DAGs)
    dag_runs_by_entity: dict[str, dict] = {}
    try:
        _pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 2)
        rows_pg = await _pool.fetch(
            f"""SELECT DISTINCT ON (entity)
                   run_id, dag_id, entity, airflow_dag_run_id,
                   status, mode,
                   started_at, finished_at,
                   record_count, bytes_written, storage_uri,
                   duration_seconds, watermark_updated_to, error_message, extra
               FROM pipeline_runs
               WHERE cartridge_id = $1
                 {scope_sql}
               ORDER BY entity, started_at DESC""",
            cartridge,
            *scope_values,
        )
        for row in rows_pg:
            run = await _refresh_dag_run_status(dict(row), user)
            dag_runs_by_entity[row["entity"]] = run
    except Exception:
        logger.debug("Could not load pipeline_runs for %s", cartridge, exc_info=True)

    # 2b. jobs table — internal queue (legacy / console-triggered runs)
    all_jobs = await _call_with_optional_user(job_service.list_recent, 100, user=user)
    jobs_by_entity: dict[str, dict] = {}
    for j in all_jobs:
        entity = (j.get("args") or {}).get("entity") or ""
        if not entity or entity in jobs_by_entity:
            continue
        jobs_by_entity[entity] = j

    # 3. Silver datasets from refinement
    try:
        all_datasets = (await _refinement_invoke("list_datasets", {}, timeout=15, user=user)).get("datasets", [])
    except Exception:
        all_datasets = []
        # Some in-process tests replace ``httpx.AsyncClient`` with a minimal
        # get-only fake that predates the MCP invoke path. Keep that legacy
        # compatibility path working without changing production behavior.
        if not hasattr(httpx.AsyncClient, "post"):
            try:
                async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=15) as c:
                    r = await c.get(f"{REFINEMENT_URL}/datasets")
                if getattr(r, "status_code", 500) == 200:
                    all_datasets = (r.json() or {}).get("datasets", [])
            except Exception:
                all_datasets = []

    silver_ds = [d for d in all_datasets if d.get("layer") == "silver"]
    gold_ds   = [d for d in all_datasets if d.get("layer") == "gold"]

    silver_by_source: dict[str, list[dict]] = {}
    for ds in silver_ds:
        for src in (ds.get("sources") or []):
            silver_by_source.setdefault(src, []).append(ds)

    def _gold_deps_for_silver(silver_name: str) -> list[dict]:
        deps = []
        for gds in gold_ds:
            sql = gds.get("sql_def") or gds.get("sql") or ""
            if _re.search(rf"\bsilver_{_re.escape(silver_name)}\b", sql, _re.IGNORECASE):
                deps.append(gds)
        return deps

    # 4. Assemble pipeline rows
    rows = []
    for e in entity_list:
        entity = e.get("entity") or e.get("name") or ""
        source = f"raw/{cartridge}/{entity}"

        # Prefer pipeline_runs (Airflow DAGs); fall back to jobs table
        dag_run  = dag_runs_by_entity.get(entity)
        last_job = jobs_by_entity.get(entity)

        bronze_date  = None
        bronze_count = None
        last_run_info = None

        if dag_run:
            # Airflow DAG run is authoritative
            fin = dag_run.get("finished_at")
            bronze_date  = str(fin)[:10] if fin else None
            bronze_count = dag_run.get("record_count")
            dag_status   = _normalize_airflow_state(dag_run.get("status"))
            dag_run_id   = dag_run.get("airflow_dag_run_id") or dag_run.get("run_id")
            last_run_info = {
                "source":       "airflow",
                "dag_id":       dag_run.get("dag_id"),
                "dag_run_id":   dag_run_id,
                "run_id":       dag_run_id,
                "status":       dag_status,
                "mode":         dag_run.get("mode"),
                "triggered_at": str(dag_run.get("started_at", "")) if dag_run.get("started_at") else None,
                "started_at":   str(dag_run.get("started_at", "")) if dag_run.get("started_at") else None,
                "finished_at":  str(fin) if fin else None,
                "duration_sec": float(dag_run.get("duration_seconds")) if dag_run.get("duration_seconds") is not None else None,
                "error":        dag_run.get("error_message"),
            }
        elif last_job:
            if last_job.get("status") == "done":
                res = last_job.get("result") or {}
                bronze_date  = (last_job.get("finished_at") or last_job.get("created_at") or "")[:10]
                bronze_count = res.get("record_count") or res.get("total_records")
            last_run_info = {
                "source":      "jobs",
                "job_id":      last_job["job_id"],
                "status":      last_job["status"],
                "finished_at": last_job.get("finished_at") or last_job.get("created_at"),
                "message":     last_job.get("message"),
            }

        if not bronze_date or bronze_count is None:
            physical_bronze = await _call_with_optional_user(
                _bronze_physical_snapshot,
                cartridge,
                entity,
                user=user,
            )
            if physical_bronze:
                bronze_date = bronze_date or physical_bronze.get("latest_date")
                if bronze_count is None:
                    bronze_count = physical_bronze.get("record_count")

        # Bronze freshness
        if dag_run and dag_run["status"] == "failed" and not bronze_date:
            bronze_status = "error"
        elif bronze_date:
            bronze_status = _freshness(bronze_date + "T00:00:00+00:00")
        else:
            bronze_status = "never"

        # Silver/Gold nodes
        is_failed = (dag_run and dag_run["status"] == "failed") or (last_job and last_job.get("status") == "failed")
        silver_nodes = []
        gold_nodes   = []
        for ds in silver_by_source.get(source, []):
            s_status = "stale" if is_failed else _freshness(ds.get("last_refresh"), threshold_h=24)
            silver_nodes.append({
                "name":         ds["name"],
                "layer":        ds.get("layer", "silver"),
                "row_count":    ds.get("row_count"),
                "last_refresh": ds.get("last_refresh"),
                "status":       s_status,
            })
            for gds in _gold_deps_for_silver(ds["name"]):
                if not any(g["name"] == gds["name"] for g in gold_nodes):
                    gold_nodes.append({
                        "name":         gds["name"],
                        "layer":        "gold",
                        "row_count":    gds.get("row_count"),
                        "last_refresh": gds.get("last_refresh"),
                        "status":       _freshness(gds.get("last_refresh"), threshold_h=24),
                    })

        rows.append({
            "entity":    entity,
            "cartridge": cartridge,
            "modes":     e.get("modes") or ([e["mode"]] if e.get("mode") else ["full"]),
            "watermark": e.get("watermark_field") or "",
            "last_run":  last_run_info,
            # Keep last_job for backward compat with pipeline.html polling logic
            "last_job":  {
                "job_id":       last_run_info.get("job_id") if last_run_info else None,
                "dag_id":       last_run_info.get("dag_id") if last_run_info else None,
                "dag_run_id":   last_run_info.get("dag_run_id") if last_run_info else None,
                "status":       last_run_info.get("status") if last_run_info else None,
                "mode":         last_run_info.get("mode") if last_run_info else None,
                "triggered_at": last_run_info.get("triggered_at") if last_run_info else None,
                "finished_at":  last_run_info.get("finished_at") if last_run_info else None,
                "duration_sec": last_run_info.get("duration_sec") if last_run_info else None,
                "created_at":   last_run_info.get("finished_at") or last_run_info.get("triggered_at") if last_run_info else None,
                "message":      last_run_info.get("message") if last_run_info else None,
            } if last_run_info else None,
            "bronze": {
                "source":       source,
                "latest_date":  bronze_date,
                "record_count": bronze_count,
                "status":       bronze_status,
            },
            "silver": silver_nodes,
            "gold":   gold_nodes,
        })

    _order = {"running": 0, "error": 1, "stale": 2, "fresh": 3, "never": 4, "unknown": 5}
    rows.sort(key=lambda r: _order.get(r["bronze"]["status"], 5))
    return {"pipeline": rows}

# /api/dag_templates
@router.get("/api/dag_templates", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_dag_templates():
    from app.services import dag_templates
    return {"templates": dag_templates.get_all()}

# /api/dag_templates/{template_id}
@router.get("/api/dag_templates/{template_id}", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_dag_template_code(template_id: str,
                                cartridge: str = "my_cartridge",
                                entity: str = "MyEntity"):
    from app.services import dag_templates
    code = dag_templates.get_code(template_id, cartridge, entity)
    if code is None:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return {"id": template_id, "cartridge": cartridge, "entity": entity, "code": code}

# /api/pipeline_runs
@router.get("/api/pipeline_runs", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_pipeline_runs(cartridge: str = "replicon", entity: str = None, limit: int = 50, user: dict = Depends(require_authenticated)):
    """Recent DAG run history from pipeline_runs table."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    try:
        pool = await _get_db_pool()
        if entity:
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
            rows = await pool.fetch(
                "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $3",
                cartridge, entity, limit, *scope_values,
            )
        else:
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
            rows = await pool.fetch(
                "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 "
                f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $2",
                cartridge, limit, *scope_values,
            )
        return {"runs": [dict(r) for r in rows]}
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("pipeline runs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

# /api/pipeline/{cartridge}/{entity}/runs
@router.get("/api/pipeline/{cartridge}/{entity}/runs", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_pipeline_entity_runs(cartridge: str, entity: str, limit: int = 20, user: dict = Depends(require_authenticated)):
    """Recent DAG-based pipeline runs for one cartridge entity."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")

    safe_limit = max(1, min(int(limit or 20), 100))

    try:
        pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
        rows = await pool.fetch(
            f"""
            SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                   started_at, finished_at, duration_seconds, error_message
              FROM pipeline_runs
             WHERE cartridge_id=$1 AND entity=$2
               {scope_sql}
             ORDER BY started_at DESC NULLS LAST
             LIMIT $3
            """,
            cartridge,
            entity,
            safe_limit,
            *scope_values,
        )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("entity runs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    runs = []
    for row in rows:
        refreshed = await _refresh_dag_run_status(dict(row), user)
        runs.append(_format_pipeline_entity_run(refreshed))

    return {
        "cartridge": cartridge,
        "entity": entity,
        "runs": runs,
    }

# /api/pipeline/{cartridge}/{entity}/runs/{dag_run_id}/logs
@router.get("/api/pipeline/{cartridge}/{entity}/runs/{dag_run_id}/logs", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_pipeline_run_logs(cartridge: str, entity: str, dag_run_id: str, user: dict = Depends(require_authenticated)):
    """Basic DAG run logs summary for one cartridge entity run."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")

    try:
        pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
        row = await pool.fetchrow(
            f"""
            SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                   started_at, finished_at, duration_seconds, error_message
              FROM pipeline_runs
             WHERE cartridge_id=$1
               AND entity=$2
               AND (run_id=$3 OR airflow_dag_run_id=$3)
               {scope_sql}
             ORDER BY started_at DESC NULLS LAST
             LIMIT 1
            """,
            cartridge,
            entity,
            dag_run_id,
            *scope_values,
        )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("run logs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    if not row:
        raise HTTPException(404, f"Run '{dag_run_id}' not found for {cartridge}/{entity}")

    run = await _refresh_dag_run_status(dict(row), user)
    dag_id = run.get("dag_id") or metadata.get("dag_id")
    resolved_dag_run_id = run.get("airflow_dag_run_id") or run.get("run_id") or dag_run_id
    response = {
        "cartridge": cartridge,
        "entity": entity,
        "dag_id": dag_id,
        "dag_run_id": resolved_dag_run_id,
        "status": _normalize_airflow_state(run.get("status")),
        "tasks": [],
        "logs": [],
        "error": run.get("error_message"),
        "available": False,
    }

    try:
        tasks_result = await mcp_registry.invoke("infra", "airflow_list_task_instances", {
            "dag_id": dag_id,
            "dag_run_id": resolved_dag_run_id,
        }, user=user)
        if tasks_result.get("error"):
            response["error"] = tasks_result["error"]
            return response

        tasks = tasks_result.get("tasks") or []
        response["tasks"] = tasks
        logs = []
        for task in tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            log_result = await mcp_registry.invoke("infra", "airflow_get_task_logs", {
                "dag_id": dag_id,
                "dag_run_id": resolved_dag_run_id,
                "task_id": task_id,
            }, user=user)
            if log_result.get("error"):
                logs.append({"task_id": task_id, "available": False, "error": log_result["error"]})
            else:
                logs.append({"task_id": task_id, "available": True, "logs": log_result.get("logs", "")})

        response["logs"] = logs
        response["available"] = bool(tasks) and all(item.get("available") for item in logs)
        return response
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("run logs Airflow fetch failed error_id=%s", _eid)
        response["error"] = f"Internal server error. error_id={_eid}"
        return response

# /api/pipeline/{cartridge}/{entity}/extract
@router.post("/api/pipeline/{cartridge}/{entity}/extract", dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)])
@_bind_to_main
async def api_pipeline_extract(
    cartridge: str,
    entity: str,
    body: dict | None = None,
    user: dict = Depends(require_permission("pipelines.run")),
):
    """Trigger extraction for a single entity. Returns job_id for polling."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    body = body or {}
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if (metadata.get("pattern") or "").lower() == "dag-based":
        if not metadata.get("entity"):
            raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")
        if not metadata.get("enabled"):
            raise HTTPException(400, f"Entity '{entity}' is disabled")
        dag_id = metadata.get("dag_id")
        if not dag_id:
            raise HTTPException(400, f"No dag_id configured for {cartridge}.{entity}")

        extract_conf = _build_dag_extract_conf(cartridge, entity, metadata.get("mode"), body)
        if not extract_conf.get("conn_id") and metadata.get("connection_id"):
            extract_conf["conn_id"] = _normalize_pipeline_conn_id(metadata.get("connection_id"))
        conf = _apply_user_scope_to_dag_conf(extract_conf, user)
        requested_dag_run_id = _dag_run_id_from_idempotency_key(
            dag_id,
            body.get("idempotency_key") or body.get("request_id"),
        )
        result = await _trigger_airflow_extract_dag(dag_id, conf, user, requested_dag_run_id)
        if result.get("error"):
            raise HTTPException(502, f"Airflow trigger failed: {result['error']}")
        dag_run_id = result.get("dag_run_id") or result.get("run_id")
        await _record_dag_pipeline_trigger(
            cartridge=cartridge,
            entity=entity,
            dag_id=dag_id,
            dag_run_id=dag_run_id,
            mode=conf.get("mode", metadata.get("mode") or "incremental"),
            status=result.get("state") or "queued",
            conf=conf,
            tenant_id=conf.get("tenant_id"),
            workspace_id=conf.get("workspace_id"),
        )
        return {
            "triggered": True,
            "cartridge": cartridge,
            "entity": entity,
            "dag_id": dag_id,
            "run_id": dag_run_id,
            "dag_run_id": dag_run_id,
            "state": result.get("state"),
            "conf": conf,
        }

    mode = body.get("mode", "incremental")
    args = {
        "entity": entity,
        "mode": mode,
    }
    conn_id = _normalize_pipeline_conn_id(body.get("conn_id") or body.get("connection_id"))
    if conn_id:
        args["conn_id"] = conn_id
    result = await mcp_registry.invoke(cartridge, "extract", args, user=user)
    return result

# /api/pipeline/{cartridge}/extract_all
@router.post(
    "/api/pipeline/{cartridge}/extract_all",
    dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)],
)
@_bind_to_main
async def api_pipeline_extract_all(
    cartridge: str,
    body: dict | None = None,
    user: dict = Depends(require_permission("pipelines.run")),
):
    """Trigger extraction for every entity currently visible in the pipeline."""
    body = body or {}
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    pipeline = await _call_with_optional_user(api_pipeline, cartridge, user=user)
    rows = pipeline.get("pipeline") or []
    triggered: list[dict] = []
    errors: list[dict] = []

    for row in rows:
        entity = row.get("entity")
        if not entity:
            continue
        try:
            result = await _call_with_optional_user(api_pipeline_extract, cartridge, entity, body, user=user)
            triggered.append({
                "entity": entity,
                "job_id": result.get("job_id") or result.get("dag_run_id") or result.get("run_id"),
                "dag_run_id": result.get("dag_run_id") or result.get("run_id"),
                "dag_id": result.get("dag_id"),
                "state": result.get("state") or result.get("status"),
                "result": result,
            })
        except HTTPException as exc:
            errors.append({
                "entity": entity,
                "status_code": exc.status_code,
                "error": str(exc.detail),
            })
        except Exception:
            error_id = uuid.uuid4().hex
            logger.exception("pipeline extract_all failed for %s.%s error_id=%s", cartridge, entity, error_id)
            errors.append({
                "entity": entity,
                "status_code": 500,
                "error": f"Internal server error. error_id={error_id}",
            })

    return {
        "cartridge": cartridge,
        "triggered": triggered,
        "errors": errors,
        "count": len(triggered),
        "error_count": len(errors),
    }

# /studio/cartridges/{cartridge_id}/entities/{entity}/rename
@router.post("/studio/cartridges/{cartridge_id}/entities/{entity}/rename", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def studio_rename_entity(
    cartridge_id: str,
    entity: str,
    body: dict,
    _global_admin: dict = Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    user: dict = Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
):
    _require_cartridge_visible(user, cartridge_id)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")
    if new_name == entity:
        return {"renamed": False, "reason": "same name"}
    # Verify old entity exists
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    entities = [e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])]
    if entity not in entities:
        raise HTTPException(404, f"Entity '{entity}' not found in cartridge '{cartridge_id}'")
    if new_name in entities:
        raise HTTPException(409, f"Entity '{new_name}' already exists")
    await cartridge_service.rename_entity(cartridge_id, entity, new_name)
    return {"renamed": True, "old_name": entity, "new_name": new_name}

# /studio/cartridges/{cartridge_id}/entities/{entity}
@router.patch("/studio/cartridges/{cartridge_id}/entities/{entity}", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def studio_update_entity(
    cartridge_id: str,
    entity: str,
    body: dict,
    _global_admin: dict = Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    user: dict = Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
):
    """Update entity_config fields."""
    _require_cartridge_visible(user, cartridge_id)
    allowed = {"display_name", "mode", "primary_key", "dag_id",
               "trigger_type", "cron_expression", "description", "enabled",
               "dag_params", "connection_id"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    await cartridge_service.upsert_entity(cartridge_id, entity, **updates)
    return {"updated": True, "entity": entity, **updates}

# /studio/cartridges
@router.get("/studio/cartridges", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def studio_list_cartridges(user: dict = Depends(require_authenticated)):
    cartridges = await cartridge_service.list_cartridges()
    ctx = build_security_context(user)
    allowed = {str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()}
    if "*" not in allowed:
        cartridges = [c for c in cartridges if str(c.get("id") or c.get("cartridge") or "").strip() in allowed]
    return {"cartridges": cartridges}

# /studio/cartridges/{cartridge_id}/status
@router.get("/studio/cartridges/{cartridge_id}/status", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def studio_cartridge_status(cartridge_id: str, user: dict = Depends(require_authenticated)):
    """Lightweight status probe for the cartridge.

    For Replicon (and any cartridge not backed by a dedicated microservice in
    this deployment) we just report ``operational`` if it is registered.
    For SAP cartridges we probe the corresponding FastAPI service.
    """
    _require_cartridge_visible(user, cartridge_id)
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")

    base_url = _MICROSERVICE_CARTRIDGES.get(cartridge_id)
    if not base_url:
        return {"cartridge_id": cartridge_id, "status": "operational"}

    probe = await _probe_microservice(base_url, cartridge_id)
    return {"cartridge_id": cartridge_id, **probe}

# /studio/cartridges
@router.post("/studio/cartridges", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def studio_create_cartridge(body: dict):
    cid  = body.get("id", "").strip()
    name = body.get("name", "").strip()
    if not cid or not name:
        raise HTTPException(400, "id and name are required")
    existing = await cartridge_service.get_cartridge(cid)
    if existing:
        raise HTTPException(409, f"Cartridge '{cid}' already exists")
    try:
        manifest = await cartridge_service.create_cartridge(cid, name, body.get("description", ""))
    except ValueError as exc:
        raise HTTPException(400, "invalid cartridge payload") from exc
    return manifest

# /studio/cartridges/{cartridge_id}
@router.get("/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def studio_get_cartridge(cartridge_id: str, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge_id)
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    return manifest

# /studio/cartridges/{cartridge_id}
@router.patch("/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def studio_update_cartridge(cartridge_id: str, body: dict, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    try:
        return await cartridge_service.update_cartridge(cartridge_id, body)
    except ValueError as exc:
        raise HTTPException(400, "invalid cartridge update") from exc

# /studio/cartridges/{cartridge_id}/spec
@router.post("/studio/cartridges/{cartridge_id}/spec", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def studio_upload_spec(cartridge_id: str, file: UploadFile = File(...), user: dict = Depends(require_authenticated)):
    """Upload a spec file (OpenAPI YAML, WSDL, OData $metadata) for the cartridge."""
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    content = (await file.read()).decode("utf-8", errors="replace")
    try:
        key = cartridge_service.upload_spec(cartridge_id, file.filename or "spec.yaml", content)
    except ValueError as exc:
        raise HTTPException(400, "invalid cartridge specification") from exc
    return {"uploaded": key, "filename": file.filename, "size": len(content)}

# /studio/cartridges/{cartridge_id}/export
@router.get("/studio/cartridges/{cartridge_id}/export")
@_bind_to_main
async def studio_export_cartridge(
    cartridge_id: str,
    user: dict = Depends(require_permission("cartridges.read")),
):
    """Download the cartridge as a ZIP archive."""
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    try:
        zip_bytes = await cartridge_service.export_cartridge(cartridge_id)
    except ValueError as exc:
        raise HTTPException(400, "cartridge export failed") from exc
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{cartridge_id}.zip"'},
    )

# /studio/import
@router.post("/studio/import", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def studio_import_cartridge(file: UploadFile = File(...), user: dict = Depends(require_authenticated)):
    """Import a cartridge from a previously exported ZIP."""
    zip_bytes = await file.read()
    try:
        manifest = await cartridge_service.import_cartridge(zip_bytes, actor_user=user)
    except ValueError as e:
        raise HTTPException(400, "invalid cartridge archive") from e
    return manifest

# /studio/chat
@router.post("/studio/chat", dependencies=[Depends(require_csrf), Depends(require_permission("studio.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def studio_chat(body: dict, user: dict = Depends(require_authenticated)):
    cartridge_id = body.get("cartridge_id")
    manifest     = await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    return await studio_assistant.chat(
        message  = body.get("message", ""),
        history  = body.get("history", []),
        step     = body.get("step", 1),
        manifest = manifest,
        actor_role = user.get("role"),
        actor_user = user,
    )

# /studio/chat/stream
@router.post("/studio/chat/stream", dependencies=[Depends(require_csrf), Depends(require_permission("studio.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def studio_chat_stream(body: dict, user: dict = Depends(require_authenticated)):
    """SSE-style streaming chat: emits tool_use / tool_result / text / done / error
    events as the assistant runs, so the UI can show a live reasoning trail."""
    cartridge_id = body.get("cartridge_id")
    manifest     = await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    message      = (body.get("message") or "").strip()
    history      = body.get("history") or []
    step         = body.get("step", 1)
    if not message:
        raise HTTPException(400, "message is required")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            result = await studio_assistant.chat(
                message  = message,
                history  = history,
                step     = step,
                manifest = manifest,
                on_event = on_event,
                actor_role = user.get("role"),
                actor_user = user,
            )
            await queue.put({"type": "done", **result})
        except Exception:
            _eid = uuid.uuid4().hex
            logger.exception("studio assistant chat failed error_id=%s", _eid)
            await queue.put({"type": "error", "message": f"Internal server error. error_id={_eid}"})

    asyncio.create_task(run())

    async def event_stream():
        yield "event: open\ndata: {}\n\n"
        while True:
            evt = await queue.get()
            etype = evt.get("type", "message")
            yield f"event: {etype}\ndata: {json.dumps(evt, default=str)}\n\n"
            if etype in ("done", "error"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
