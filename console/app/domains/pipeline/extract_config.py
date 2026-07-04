"""Pipeline extraction configuration helpers."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException


def normalize_pipeline_conn_id(conn_id: object | None) -> str | None:
    if conn_id is None:
        return None
    value = str(conn_id).strip()
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise HTTPException(400, "invalid connection id")
    return value


def build_dag_extract_conf(
    cartridge: str, entity: str, configured_mode: str | None, body: dict[str, Any]
) -> dict[str, Any]:
    mode = body.get("mode") or configured_mode or "incremental"
    conf: dict[str, Any] = {
        "cartridge_id": cartridge,
        "entity": entity,
        "mode": mode,
    }
    conn_id = normalize_pipeline_conn_id(body.get("conn_id") or body.get("connection_id"))
    if conn_id:
        conf["conn_id"] = conn_id
    if body.get("from_date"):
        conf["from_date"] = body["from_date"]
    if body.get("to_date"):
        conf["to_date"] = body["to_date"]
    return conf


def build_mcp_extract_args(entity: str, body: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {
        "entity": entity,
        "mode": body.get("mode", "incremental"),
    }
    conn_id = normalize_pipeline_conn_id(body.get("conn_id") or body.get("connection_id"))
    if conn_id:
        args["conn_id"] = conn_id
    return args


def connection_id_from_vault_payload(payload: Any) -> str | None:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        raw_connections = payload.get("connections")
        if isinstance(raw_connections, list):
            candidates.extend(raw_connections)
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            candidates.extend(raw_items)
    elif isinstance(payload, list):
        candidates.extend(payload)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        raw = item.get("conn_id") or item.get("id") or item.get("key")
        conn_id = normalize_pipeline_conn_id(raw)
        if conn_id:
            return conn_id
    return None


async def resolve_pipeline_sync_conn_id(
    cartridge: str,
    requested_conn_id: object | None,
    user: dict[str, Any] | None,
    *,
    vault_payload_loader: Callable[[str, dict[str, Any] | None], Awaitable[Any]],
    entity_config_conn_loader: Callable[[str], Awaitable[Any]],
    logger_debug: Callable[..., None] | None = None,
) -> str | None:
    conn_id = normalize_pipeline_conn_id(requested_conn_id)
    if conn_id:
        return conn_id

    try:
        payload = await vault_payload_loader(cartridge, user)
        conn_id = connection_id_from_vault_payload(payload)
        if conn_id:
            return conn_id
    except Exception:
        if logger_debug:
            logger_debug(
                "Could not resolve pipeline connection from Vault for cartridge=%s",
                cartridge,
                exc_info=True,
            )

    try:
        conn_id = normalize_pipeline_conn_id(
            await entity_config_conn_loader(cartridge)
        )
        if conn_id:
            return conn_id
    except Exception:
        if logger_debug:
            logger_debug(
                "Could not resolve pipeline connection from entity_config for cartridge=%s",
                cartridge,
                exc_info=True,
            )
    return None


def dag_run_id_from_idempotency_key(
    dag_id: str, idempotency_key: object | None
) -> str | None:
    if idempotency_key is None:
        return None
    key = str(idempotency_key).strip()
    if not key:
        return None
    if len(key) > 160:
        raise HTTPException(400, "idempotency_key is too long")
    return f"console__{dag_id}__{uuid.uuid5(uuid.NAMESPACE_URL, f'{dag_id}:{key}').hex}"


def apply_user_scope_to_dag_conf(
    conf: dict[str, Any],
    user: dict[str, Any] | None,
    *,
    security_context_builder: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> dict[str, Any]:
    scoped = dict(conf or {})
    ctx = security_context_builder(user)
    tenant_id = ctx.get("tenant_id")
    workspace_id = ctx.get("workspace_id")
    if not tenant_id or not workspace_id:
        return scoped
    for key, value in (("tenant_id", tenant_id), ("workspace_id", workspace_id)):
        existing = scoped.get(key)
        if existing and str(existing) != str(value):
            raise HTTPException(403, detail=f"{key} scope mismatch")
        scoped[key] = str(value)
    scoped["security_context"] = ctx
    return scoped


def is_transient_airflow_trigger_error(error: str) -> bool:
    lowered = str(error).lower()
    return "connection" in lowered or "connect" in lowered


def entity_declared_in_static_catalog(
    cartridge: str,
    entity: str,
    *,
    registry_root: Path | None = None,
    repo_root: Path | None = None,
) -> bool:
    import yaml

    wanted = str(entity or "").strip()
    if not wanted:
        return False
    registry_root = registry_root or Path("/registry/cartridges")
    repo_root = repo_root or Path(__file__).resolve().parents[4]
    candidates = [
        registry_root / cartridge / "app" / "config" / "entities.yaml",
        repo_root / "cartridges" / cartridge / "app" / "config" / "entities.yaml",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        raw_entities = parsed.get("entities") if isinstance(parsed, dict) else []
        if not isinstance(raw_entities, list):
            continue
        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            name = str(
                item.get("entity") or item.get("name") or item.get("id") or ""
            ).strip()
            if name == wanted:
                return True
    return False
