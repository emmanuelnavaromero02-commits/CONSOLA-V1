from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from cartridge_run_admission import admit_run
except ModuleNotFoundError:
    from airflow.dags.cartridge_run_admission import admit_run


_MARKET_ENTITIES = {
    "banxico": ("series_metadata", "series_observations"),
    "inegi": ("series_metadata", "series_observations"),
    "sec_edgar": ("company_metadata", "company_facts"),
}


def _allowed_prefixes(
    cartridge_id: str, tenant_id: str, workspace_id: str
) -> list[str]:
    scope = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
    prefixes = [
        f"raw/{cartridge_id}/{entity}/{scope}"
        for entity in _MARKET_ENTITIES[cartridge_id]
    ]
    prefixes.extend(f"{layer}/{cartridge_id}/{scope}" for layer in ("silver", "gold"))
    return prefixes


def security_context_from_conf(
    conf: Mapping[str, Any], cartridge_id: str, *, dag_run: Any = None
) -> dict[str, Any]:
    if cartridge_id not in _MARKET_ENTITIES:
        raise ValueError("unsupported market cartridge")
    admitted = admit_run(conf, cartridge_id=cartridge_id, dag_run=dag_run)
    return admitted.context(
        user_id=f"airflow:{cartridge_id}_extract",
        prefixes=_allowed_prefixes(cartridge_id, admitted.tenant_id, admitted.workspace_id),
    )
