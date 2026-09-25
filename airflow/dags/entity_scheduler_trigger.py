from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import requests


class AirflowTriggerError(RuntimeError):

    def __init__(self, stage: str, dag_id: str, status_code: int | None = None):
        self.stage = stage
        self.dag_id = dag_id
        self.status_code = status_code
        detail = f"HTTP {status_code}" if status_code is not None else "request failed"
        super().__init__(f"Airflow {stage} failed for DAG {dag_id!r}: {detail}")


def trigger_dag_run(
    *,
    base_url: str,
    dag_id: str,
    run_id: str,
    conf: Mapping[str, Any],
    username: str,
    password: str,
    timeout: int = 30,
    http: Any = requests,
) -> int:
    dag_url = f"{base_url.rstrip('/')}/api/v1/dags/{quote(dag_id, safe='')}"
    auth = (username, password)

    try:
        unpause = http.patch(
            dag_url,
            auth=auth,
            json={"is_paused": False},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AirflowTriggerError("unpause", dag_id) from exc
    if unpause.status_code != 200:
        raise AirflowTriggerError("unpause", dag_id, unpause.status_code)

    try:
        triggered = http.post(
            f"{dag_url}/dagRuns",
            auth=auth,
            json={"dag_run_id": run_id, "conf": dict(conf)},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AirflowTriggerError("trigger", dag_id) from exc
    if triggered.status_code not in {200, 201, 409}:
        raise AirflowTriggerError("trigger", dag_id, triggered.status_code)
    return triggered.status_code


def require_all_succeeded(results: Sequence[Mapping[str, Any]]) -> None:
    failed = sum(not bool(result.get("ok")) for result in results)
    if failed:
        raise RuntimeError(f"{failed} of {len(results)} scheduled DAG trigger(s) failed")
