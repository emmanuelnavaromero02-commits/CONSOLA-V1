from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException


ENTITY_DAG_PARAMS_MAX_BYTES = 16 * 1024
# Mirrors airflow/dags/cartridge_run_admission.RESERVED_CONF_KEYS: the scheduler owns
# these keys, so a stored dag_params row may never carry them.
RESERVED_DAG_PARAM_KEYS = frozenset(
    {
        "security_context",
        "tenant_id",
        "workspace_id",
        "cartridge_id",
        "conn_id",
        "connection_id",
        "triggered_by",
    }
)
ENTITY_UPDATE_FIELDS = {
    "display_name",
    "mode",
    "primary_key",
    "dag_id",
    "trigger_type",
    "cron_expression",
    "cron_timezone",
    "description",
    "enabled",
    "dag_params",
    "connection_id",
}


def validate_entity_dag_params(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except json.JSONDecodeError:
            raise HTTPException(400, "dag_params must be a JSON object") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "dag_params must be a JSON object")
    if any(
        not isinstance(key, str) or key in RESERVED_DAG_PARAM_KEYS or key.startswith("_")
        for key in value
    ):
        raise HTTPException(400, "dag_params cannot set reserved keys")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise HTTPException(400, "dag_params must be a JSON object") from None
    if len(encoded.encode("utf-8")) > ENTITY_DAG_PARAMS_MAX_BYTES:
        raise HTTPException(400, "dag_params exceeds 16 KiB")
    return value


_TIMEZONE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}")
_INVALID_TIMEZONE = "Zona horaria no reconocida. Usa un identificador como America/Mexico_City."
_INVALID_CRON = "La programación personalizada no es válida."
_MISSING_CRON = "Una frecuencia programada necesita una programación."


def _valid_timezone(value: Any) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= 64 or not _TIMEZONE_RE.fullmatch(value):
        return False
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
    except Exception:
        return False
    return True


def validate_entity_schedule_patch(patch: dict[str, Any]) -> None:
    if "cron_timezone" in patch and not _valid_timezone(patch["cron_timezone"]):
        raise HTTPException(400, _INVALID_TIMEZONE)
    cron = patch.get("cron_expression")
    if cron is not None and not isinstance(cron, str):
        raise HTTPException(400, _INVALID_CRON)
    cron = (cron or "").strip()
    if cron:
        from croniter import croniter

        fields = cron.split()
        shape_ok = len(fields) == 5 or (len(fields) == 1 and cron.startswith("@"))
        if not shape_ok or not croniter.is_valid(cron):
            raise HTTPException(400, _INVALID_CRON)
    if (
        patch.get("trigger_type") == "scheduled"
        and "cron_expression" in patch
        and not cron
    ):
        raise HTTPException(400, _MISSING_CRON)


def manifest_entity_names(manifest: dict[str, Any] | None) -> list[str]:
    return [
        e.get("entity") or e.get("id")
        for e in ((manifest or {}).get("entities") or [])
    ]


async def rename_studio_entity_payload(
    *,
    cartridge_id: str,
    entity: str,
    body: dict[str, Any],
    cartridge_service: Any,
) -> dict[str, Any]:
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")
    if new_name == entity:
        return {"renamed": False, "reason": "same name"}

    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")

    entities = manifest_entity_names(manifest)
    if entity not in entities:
        raise HTTPException(
            404, f"Entity '{entity}' not found in cartridge '{cartridge_id}'"
        )
    if new_name in entities:
        raise HTTPException(409, f"Entity '{new_name}' already exists")

    await cartridge_service.rename_entity(cartridge_id, entity, new_name)
    return {"renamed": True, "old_name": entity, "new_name": new_name}


async def update_studio_entity_payload(
    *,
    cartridge_id: str,
    entity: str,
    body: dict[str, Any],
    cartridge_service: Any,
) -> dict[str, Any]:
    updates = {k: v for k, v in body.items() if k in ENTITY_UPDATE_FIELDS}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    validate_entity_schedule_patch(updates)

    await cartridge_service.upsert_entity(cartridge_id, entity, **updates)
    return {"updated": True, "entity": entity, **updates}
