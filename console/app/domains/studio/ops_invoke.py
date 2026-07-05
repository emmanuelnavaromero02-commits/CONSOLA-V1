from __future__ import annotations

from typing import Any

from fastapi import HTTPException


async def invoke_studio_ops_tool(
    *,
    tool: str | None,
    args: dict[str, Any],
    user: dict[str, Any],
    cartridge_service: Any,
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    pipeline_runs_read_conn: Any,
    mcp_registry: Any,
    airflow_log_attempt: Any,
    airflow_log_task_ids: Any,
    uuid_factory: Any,
    logger_debug: Any,
    logger_exception: Any,
) -> dict[str, Any]:
    if tool == "delete_entity":
        cartridge_id = args["cartridge_id"]
        entity = args["entity"]
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [
            e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])
        ]
        if entity not in entities:
            return {
                "error": f"Entity '{entity}' not found in cartridge '{cartridge_id}'"
            }
        await cartridge_service.delete_entity(cartridge_id, entity)
        return {
            "deleted": True,
            "cartridge_id": cartridge_id,
            "entity": entity,
            "note": "Pipeline run history preserved. Bronze files in MinIO not removed.",
        }

    if tool == "rename_entity":
        cartridge_id = args["cartridge_id"]
        old_name = args["old_name"]
        new_name = args["new_name"].strip()
        if not new_name or new_name == old_name:
            return {"renamed": False, "reason": "same name or empty"}
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [
            e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])
        ]
        if old_name not in entities:
            return {"error": f"Entity '{old_name}' not found"}
        if new_name in entities:
            return {"error": f"Entity '{new_name}' already exists"}
        await cartridge_service.rename_entity(cartridge_id, old_name, new_name)
        return {
            "renamed": True,
            "old_name": old_name,
            "new_name": new_name,
            "note": "Bronze files in MinIO remain at the old path — new extractions will use the new name.",
        }

    if tool == "list_entities":
        cartridge_id = args["cartridge_id"]
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        runs_map = await _latest_entity_runs(
            cartridge_id=cartridge_id,
            user=user,
            get_db_pool=get_db_pool,
            pipeline_runs_scope_predicate=pipeline_runs_scope_predicate,
            pipeline_runs_read_conn=pipeline_runs_read_conn,
            logger_debug=logger_debug,
        )
        entities = []
        for entity_spec in manifest.get("entities") or []:
            name = entity_spec.get("entity") or entity_spec.get("id") or ""
            entities.append(
                {
                    "entity": name,
                    "display_name": entity_spec.get("display_name"),
                    "mode": entity_spec.get("mode", "full"),
                    "dag_id": entity_spec.get("dag_id"),
                    "trigger_type": entity_spec.get("trigger_type", "manual"),
                    "last_run": runs_map.get(name),
                }
            )
        return {
            "cartridge_id": cartridge_id,
            "entities": entities,
            "count": len(entities),
        }

    if tool == "get_entity_logs":
        return await _entity_logs_payload(
            cartridge_id=args["cartridge_id"],
            entity=args["entity"],
            user=user,
            get_db_pool=get_db_pool,
            pipeline_runs_scope_predicate=pipeline_runs_scope_predicate,
            pipeline_runs_read_conn=pipeline_runs_read_conn,
            mcp_registry=mcp_registry,
            airflow_log_attempt=airflow_log_attempt,
            airflow_log_task_ids=airflow_log_task_ids,
            uuid_factory=uuid_factory,
            logger_debug=logger_debug,
            logger_exception=logger_exception,
        )

    if tool == "update_entity":
        cartridge_id = args.pop("cartridge_id")
        entity = args.pop("entity")
        if not args:
            return {"error": "No fields to update"}
        await cartridge_service.upsert_entity(cartridge_id, entity, **args)
        return {"updated": True, "entity": entity, **args}

    raise HTTPException(400, f"Unknown tool: {tool}")


async def _latest_entity_runs(
    *,
    cartridge_id: str,
    user: dict[str, Any],
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    pipeline_runs_read_conn: Any,
    logger_debug: Any,
) -> dict[str, dict[str, Any]]:
    runs_map: dict[str, dict[str, Any]] = {}
    try:
        pool = await get_db_pool()
        scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 2)
        async with pipeline_runs_read_conn(pool, user) as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT ON (entity) entity, status, started_at, finished_at, record_count, error_message "
                f"FROM pipeline_runs WHERE cartridge_id=$1 {scope_sql} ORDER BY entity, started_at DESC",
                cartridge_id,
                *scope_values,
            )
        for row in rows:
            runs_map[row["entity"]] = {
                "status": row["status"],
                "started_at": str(row["started_at"])[:16] if row["started_at"] else None,
                "finished_at": str(row["finished_at"])[:10] if row["finished_at"] else None,
                "record_count": row["record_count"],
                "error": row["error_message"],
            }
    except Exception:
        logger_debug(
            "Could not load pipeline_runs for list_entities %s",
            cartridge_id,
            exc_info=True,
        )
    return runs_map


async def _entity_logs_payload(
    *,
    cartridge_id: str,
    entity: str,
    user: dict[str, Any],
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    pipeline_runs_read_conn: Any,
    mcp_registry: Any,
    airflow_log_attempt: Any,
    airflow_log_task_ids: Any,
    uuid_factory: Any,
    logger_debug: Any,
    logger_exception: Any,
) -> dict[str, Any]:
    last_run = None
    try:
        pool = await get_db_pool()
        scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 3)
        async with pipeline_runs_read_conn(pool, user) as conn:
            row = await conn.fetchrow(
                "SELECT dag_id, airflow_dag_run_id, status, mode, "
                "       started_at, finished_at, record_count, error_message, extra "
                "FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                f"{scope_sql} ORDER BY started_at DESC LIMIT 1",
                cartridge_id,
                entity,
                *scope_values,
            )
        if row:
            last_run = dict(row)
    except Exception:
        error_id = uuid_factory().hex
        logger_exception("get_entity_logs DB query failed error_id=%s", error_id)
        return {"error": f"Internal server error. error_id={error_id}"}

    if not last_run:
        return {"error": f"No pipeline runs found for {cartridge_id}/{entity}"}

    dag_id = last_run.get("dag_id") or f"{cartridge_id}_extract"
    airflow_run_id = last_run.get("airflow_dag_run_id")
    airflow_logs = None
    airflow_log_error = None
    airflow_attempt = airflow_log_attempt(dag_id, airflow_run_id, [])
    try:
        if not airflow_run_id:
            runs_result = await mcp_registry.invoke(
                "infra",
                "airflow_list_dag_runs",
                {"dag_id": dag_id, "limit": 20},
                user=user,
            )
            airflow_runs = (runs_result or {}).get("runs", [])
            started_str = str(last_run.get("started_at", ""))[:10]
            for run in airflow_runs:
                conf_entity = (run.get("conf") or {}).get("entity", "")
                run_date = (run.get("start_date") or "")[:10]
                if conf_entity == entity and run_date == started_str:
                    airflow_run_id = run["dag_run_id"]
                    break
            if not airflow_run_id and airflow_runs:
                airflow_run_id = airflow_runs[0]["dag_run_id"]

        if airflow_run_id:
            logs_payload = await _fetch_airflow_logs(
                dag_id=dag_id,
                airflow_run_id=airflow_run_id,
                mcp_registry=mcp_registry,
                airflow_log_attempt=airflow_log_attempt,
                airflow_log_task_ids=airflow_log_task_ids,
                user=user,
            )
            airflow_logs = logs_payload["airflow_logs"]
            airflow_log_error = logs_payload["airflow_log_error"]
            airflow_attempt = logs_payload["airflow_attempt"]
        else:
            airflow_log_error = f"No Airflow dag_run_id found for dag_id={dag_id}"
    except Exception:
        logger_debug(
            "Airflow log fetch failed for %s/%s",
            cartridge_id,
            entity,
            exc_info=True,
        )
        airflow_log_error = (
            f"Airflow log fetch failed for dag_id={dag_id} dag_run_id={airflow_run_id}"
        )

    return {
        "entity": entity,
        "cartridge_id": cartridge_id,
        "dag_id": dag_id,
        "dag_run_id": airflow_run_id,
        "status": last_run["status"],
        "started_at": str(last_run.get("started_at", ""))[:19],
        "error_message": last_run.get("error_message"),
        "airflow_logs": airflow_logs or "",
        "airflow_log_error": airflow_log_error,
        "attempted": airflow_attempt,
    }


async def _fetch_airflow_logs(
    *,
    dag_id: str,
    airflow_run_id: str,
    mcp_registry: Any,
    airflow_log_attempt: Any,
    airflow_log_task_ids: Any,
    user: dict[str, Any],
) -> dict[str, Any]:
    tasks_result = await mcp_registry.invoke(
        "infra",
        "airflow_list_task_instances",
        {"dag_id": dag_id, "dag_run_id": airflow_run_id},
        user=user,
    )
    if (tasks_result or {}).get("error"):
        task_ids = airflow_log_task_ids(dag_id, [])
        return {
            "airflow_logs": None,
            "airflow_log_error": (
                f"Could not list Airflow tasks for dag_id={dag_id} "
                f"dag_run_id={airflow_run_id}: {tasks_result['error']}"
            ),
            "airflow_attempt": airflow_log_attempt(dag_id, airflow_run_id, task_ids),
        }

    tasks = (tasks_result or {}).get("tasks") or []
    task_ids = airflow_log_task_ids(dag_id, tasks)
    airflow_attempt = airflow_log_attempt(dag_id, airflow_run_id, task_ids, tasks)
    if not task_ids:
        return {
            "airflow_logs": None,
            "airflow_log_error": (
                f"No Airflow log task found for dag_id={dag_id} dag_run_id={airflow_run_id}; "
                f"available_task_ids={airflow_attempt['available_task_ids']}"
            ),
            "airflow_attempt": airflow_attempt,
        }

    for task_id in task_ids:
        logs_result = await mcp_registry.invoke(
            "infra",
            "airflow_get_task_logs",
            {
                "dag_id": dag_id,
                "dag_run_id": airflow_run_id,
                "task_id": task_id,
            },
            user=user,
        )
        if (logs_result or {}).get("logs"):
            return {
                "airflow_logs": logs_result.get("logs", ""),
                "airflow_log_error": None,
                "airflow_attempt": airflow_attempt,
            }
    return {
        "airflow_logs": None,
        "airflow_log_error": (
            f"No Airflow logs found for dag_id={dag_id} "
            f"dag_run_id={airflow_run_id} task_ids={task_ids}"
        ),
        "airflow_attempt": airflow_attempt,
    }
