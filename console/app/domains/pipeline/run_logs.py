from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


RefreshDagRunStatus = Callable[[dict[str, Any], dict | None], Awaitable[dict[str, Any]]]
McpInvoke = Callable[..., Awaitable[dict[str, Any]]]


async def build_pipeline_run_logs_payload(
    *,
    cartridge: str,
    entity: str,
    dag_run_id: str,
    run_row: dict[str, Any],
    metadata: dict[str, Any],
    user: dict | None,
    refresh_dag_run_status: RefreshDagRunStatus,
    mcp_invoke: McpInvoke,
    normalize_airflow_state: Callable[[Any], str],
    airflow_log_task_ids: Callable[[str, list[dict[str, Any]]], list[str]],
    airflow_log_attempt: Callable[..., dict[str, Any]],
    error_id_factory: Callable[[], str],
    logger_exception: Callable[..., None] | None = None,
) -> dict[str, Any]:
    run = await refresh_dag_run_status(dict(run_row), user)
    dag_id = run.get("dag_id") or metadata.get("dag_id")
    resolved_dag_run_id = (
        run.get("airflow_dag_run_id") or run.get("run_id") or dag_run_id
    )
    response = {
        "cartridge": cartridge,
        "entity": entity,
        "dag_id": dag_id,
        "dag_run_id": resolved_dag_run_id,
        "status": normalize_airflow_state(run.get("status")),
        "tasks": [],
        "logs": [],
        "error": run.get("error_message"),
        "available": False,
        "attempted": airflow_log_attempt(dag_id, resolved_dag_run_id, []),
    }

    try:
        tasks_result = await mcp_invoke(
            "infra",
            "airflow_list_task_instances",
            {
                "dag_id": dag_id,
                "dag_run_id": resolved_dag_run_id,
            },
            user=user,
        )
        if tasks_result.get("error"):
            fallback_task_ids = airflow_log_task_ids(dag_id, [])
            response["attempted"] = airflow_log_attempt(
                dag_id, resolved_dag_run_id, fallback_task_ids
            )
            response["error"] = (
                f"Could not list Airflow tasks for dag_id={dag_id} "
                f"dag_run_id={resolved_dag_run_id}: {tasks_result['error']}"
            )
            return response

        tasks = tasks_result.get("tasks") or []
        response["tasks"] = tasks
        task_ids = airflow_log_task_ids(dag_id, tasks)
        response["attempted"] = airflow_log_attempt(
            dag_id, resolved_dag_run_id, task_ids, tasks
        )
        if not task_ids:
            response["error"] = (
                f"No Airflow log task found for dag_id={dag_id} dag_run_id={resolved_dag_run_id}; "
                f"available_task_ids={response['attempted']['available_task_ids']}"
            )
            return response

        logs = []
        for task_id in task_ids:
            log_result = await mcp_invoke(
                "infra",
                "airflow_get_task_logs",
                {
                    "dag_id": dag_id,
                    "dag_run_id": resolved_dag_run_id,
                    "task_id": task_id,
                },
                user=user,
            )
            if log_result.get("error"):
                logs.append(
                    {
                        "task_id": task_id,
                        "available": False,
                        "error": log_result["error"],
                    }
                )
            elif not log_result.get("logs"):
                logs.append(
                    {
                        "task_id": task_id,
                        "available": False,
                        "error": "No logs returned by Airflow",
                    }
                )
            else:
                logs.append(
                    {
                        "task_id": task_id,
                        "available": True,
                        "logs": log_result.get("logs", ""),
                    }
                )

        response["logs"] = logs
        response["available"] = any(item.get("available") for item in logs)
        if not response["available"]:
            response["error"] = (
                f"No Airflow logs found for dag_id={dag_id} "
                f"dag_run_id={resolved_dag_run_id} task_ids={task_ids}"
            )
        return response
    except Exception:
        error_id = error_id_factory()
        if logger_exception is not None:
            logger_exception("run logs Airflow fetch failed error_id=%s", error_id)
        response["error"] = f"Internal server error. error_id={error_id}"
        return response
