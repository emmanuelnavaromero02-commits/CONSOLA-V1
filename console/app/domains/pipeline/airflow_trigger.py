"""Airflow trigger orchestration helpers for pipeline DAGs."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


async def trigger_airflow_extract_dag(
    dag_id: str,
    conf: dict[str, Any],
    user: dict[str, Any] | None,
    dag_run_id: str | None = None,
    *,
    scoped_conf_builder: Callable[
        [dict[str, Any], dict[str, Any] | None], dict[str, Any]
    ],
    airflow_trigger: Callable[
        [dict[str, Any], dict[str, Any] | None], Awaitable[dict[str, Any]]
    ],
    transient_error_checker: Callable[[str], bool],
    sleep: Callable[[float], Awaitable[None]],
    max_attempts: int = 5,
) -> dict[str, Any]:
    scoped_conf = scoped_conf_builder(conf, user)
    result: dict[str, Any] = {}
    for attempt in range(max_attempts):
        args: dict[str, Any] = {
            "dag_id": dag_id,
            "conf": scoped_conf,
        }
        if dag_run_id:
            args["dag_run_id"] = dag_run_id
        result = await airflow_trigger(args, user)
        error = result.get("error")
        if not error:
            return result
        if not transient_error_checker(str(error)) or attempt == max_attempts - 1:
            return result
        await sleep(4)
    return result
