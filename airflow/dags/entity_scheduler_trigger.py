from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import requests

try:
    from cartridge_run_admission import strip_reserved
    from runtime_security_context import build_scheduled_run_context
except ModuleNotFoundError:
    from airflow.dags.cartridge_run_admission import strip_reserved
    from airflow.dags.runtime_security_context import build_scheduled_run_context


PLATFORM_CARTRIDGE = "platform"


class ScopeMissing(ValueError):
    pass


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


def scheduled_run_conf(it: Mapping[str, Any], *, run_id: str) -> dict[str, Any]:
    conf = strip_reserved(it.get("dag_params"))
    conf.update(
        {
            "entity": it["entity"],
            "mode": it["mode"],
            "cartridge_id": it["cartridge_id"],
            "triggered_by": "entity_scheduler",
        }
    )
    scoped = bool(it.get("tenant_id") and it.get("workspace_id"))
    if scoped:
        conf["tenant_id"] = it["tenant_id"]
        conf["workspace_id"] = it["workspace_id"]
    if it.get("conn_id"):
        conf["conn_id"] = it["conn_id"]
    if it["cartridge_id"] == PLATFORM_CARTRIDGE:
        return conf
    if not scoped:
        raise ScopeMissing(f"{it['cartridge_id']}.{it['entity']} has no tenant/workspace scope")
    conf["security_context"] = build_scheduled_run_context(
        cartridge_id=it["cartridge_id"],
        tenant_id=it["tenant_id"],
        workspace_id=it["workspace_id"],
        dag_id=it["dag_id"],
        dag_run_id=run_id,
    )
    return conf


def require_all_succeeded(results: Sequence[Mapping[str, Any]]) -> None:
    failed = sum(
        not bool(result.get("ok")) and result.get("status") != "scope_missing"
        for result in results
    )
    if failed:
        raise RuntimeError(f"{failed} of {len(results)} scheduled DAG trigger(s) failed")
