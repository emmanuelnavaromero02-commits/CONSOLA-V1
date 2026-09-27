from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from cartridge_run_admission import admission_time, admit_run, service_run
except ModuleNotFoundError:
    from airflow.dags.cartridge_run_admission import admission_time, admit_run, service_run


CARTRIDGE_ID = "sap_b1"


def sap_b1_security_context(tenant_id: Any, workspace_id: Any, *, user_id: str) -> dict[str, Any]:
    return service_run(CARTRIDGE_ID, tenant_id, workspace_id, principal=user_id).context(user_id=user_id)


def security_context_from_conf(
    conf: Mapping[str, Any],
    *,
    user_id: str,
    dag_run: Any = None,
    admitted_at: int | None = None,
) -> dict[str, Any]:
    admitted = admit_run(conf, cartridge_id=CARTRIDGE_ID, dag_run=dag_run, admitted_at=admitted_at)
    return admitted.context(user_id=user_id)


__all__ = ["CARTRIDGE_ID", "admission_time", "sap_b1_security_context", "security_context_from_conf"]
