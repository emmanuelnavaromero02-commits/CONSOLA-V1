from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.services.mcp_gateway.errors import GatewayError


SAP_B1_VIEWS: dict[str, tuple[str, ...]] = {
    "finanzas": ("sap_b1_margin_kpis",),
    "ventas": ("sap_b1_sales_kpis", "sap_b1_expiry_kpis"),
    "compras": ("sap_b1_supply_kpis",),
    "aprendizaje": ("sap_b1_learning_kpis",),
    "semaforo": ("sap_b1_semaforo_kpis",),
}
RAG_DOCUMENT_KINDS = ["document"]
APP_NAME_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended($1, 0))"
APP_NAME_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended($1, 0))"


def require_gateway_user(user: object) -> dict[str, Any]:
    if (
        not isinstance(user, dict)
        or user.get("auth_method") != "pat"
        or not str(user.get("active_workspace_id") or "").strip()
        or not str(user.get("active_tenant_id") or "").strip()
        or not str(user.get("access_token_id") or "").strip()
        or not isinstance(user.get("allowed_cartridges"), list)
    ):
        raise GatewayError(401, "token_invalido")
    return user


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return jsonable_encoder(value)


def _mapping(value: Any) -> dict[str, Any]:
    plain = _plain(value)
    return plain if isinstance(plain, dict) else {}


async def pipeline_automations(user: dict[str, Any]) -> dict[str, Any]:
    from app.services.pipeline_automations import list_automations

    return _mapping(await list_automations(require_gateway_user(user)))


async def control_room_summary(user: dict[str, Any]) -> dict[str, Any]:
    from app.routers import control_room

    return _mapping(await control_room.control_room_summary(user=require_gateway_user(user)))


async def talent_nine_box(user: dict[str, Any]) -> dict[str, Any]:
    from app.routers import control_room

    return _mapping(
        await control_room.control_room_sap_successfactors_talent_9box(
            user=require_gateway_user(user)
        )
    )


async def sap_b1_views(user: dict[str, Any], *, area: str, top_n: int) -> dict[str, dict[str, Any]]:
    from app.routers import control_room

    checked = require_gateway_user(user)
    views = SAP_B1_VIEWS.get(area)
    if views is None:
        raise GatewayError(400, "argumentos_invalidos")
    results: dict[str, dict[str, Any]] = {}
    for view in views:
        results[view] = _mapping(
            await control_room.control_room_sap_b1_view(view=view, top_n=int(top_n), user=checked)
        )
    return results


async def document_search(user: dict[str, Any], *, query: str, top_k: int) -> dict[str, Any]:
    from app import main

    return _mapping(
        await main.api_rag_search(
            body={"query": query, "top_k": int(top_k), "kinds": list(RAG_DOCUMENT_KINDS)},
            user=require_gateway_user(user),
        )
    )


async def published_datasets(user: dict[str, Any]) -> dict[str, Any]:
    from app import main

    return _mapping(await main.list_datasets(user=require_gateway_user(user)))


async def start_sync(user: dict[str, Any], *, cartridge: str, request_id: str) -> dict[str, Any]:
    from app import main

    return _mapping(
        await main.api_cartridge_sync_now(
            cartridge_id=cartridge,
            body={"mode": "incremental", "target": "all", "request_id": request_id},
            user=require_gateway_user(user),
        )
    )


async def sync_run(user: dict[str, Any], *, cartridge: str, run_id: str) -> dict[str, Any]:
    from app import main

    return _mapping(
        await main.api_cartridge_sync_run(
            cartridge_id=cartridge, run_id=run_id, user=require_gateway_user(user)
        )
    )


async def sync_run_origin(
    user: dict[str, Any], *, cartridge: str, run_id: str
) -> dict[str, Any] | None:
    from app import main

    row = await main._fetch_sync_run(
        cartridge=cartridge, run_id=run_id, user=require_gateway_user(user)
    )
    if not row:
        return None
    request_id = main._sync_extra_from_row(row).get("request_id")
    return {
        "request_id": str(request_id) if request_id else None,
        "started_at": row.get("started_at"),
    }


async def app_name_in_use(user: dict[str, Any], *, name: str) -> bool:
    from app.services.db_pool import get_db_pool
    from app.services.db_scope import scoped_db

    checked = require_gateway_user(user)
    pool = await get_db_pool()
    async with scoped_db(pool, checked["active_tenant_id"], checked["active_workspace_id"]) as conn:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM analytic_apps WHERE name = $1)", name
            )
        )


def app_name_lock_key(user: dict[str, Any], name: str) -> str:
    checked = require_gateway_user(user)
    return f"omega_ia_app_name:{checked['active_workspace_id']}:{name.strip().casefold()}"


async def _lock_connection() -> Any:
    import asyncpg

    from app.services.db_pool import db_dsn

    dsn = db_dsn()
    if not dsn:
        raise GatewayError(503, "servicio_no_disponible")
    return await asyncpg.connect(dsn, timeout=5, command_timeout=10)


@contextlib.asynccontextmanager
async def app_name_lock(user: dict[str, Any], *, name: str) -> AsyncIterator[bool]:
    """Session advisory lock on a dedicated (non-pooled) connection held for the whole publish."""
    key = app_name_lock_key(user, name)
    conn = await _lock_connection()
    try:
        acquired = bool(await conn.fetchval(APP_NAME_LOCK_SQL, key))
        try:
            yield acquired
        finally:
            if acquired:
                with contextlib.suppress(Exception):
                    await conn.execute(APP_NAME_UNLOCK_SQL, key)
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def create_analytic_app(
    user: dict[str, Any],
    *,
    name: str,
    objective: str,
    datasets: list[str],
    description: str,
) -> dict[str, Any]:
    from app.services import app_forge

    return _mapping(
        await app_forge.generate_and_publish_app(
            require_gateway_user(user),
            name=name,
            title=None,
            description=description,
            objective=objective,
            datasets=list(datasets),
        )
    )


def public_base_url() -> str:
    from app.services.service_urls import public_url

    return public_url("APP_BASE_URL", fallback_env="CONSOLE_URL")


def app_viewer_path(name: str) -> str:
    from urllib.parse import quote

    return f"/analytics/viewer?app={quote(name, safe='')}"


def workspace_summary(user: Mapping[str, Any]) -> dict[str, Any]:
    checked = require_gateway_user(dict(user))
    return {
        "id": checked["active_workspace_id"],
        "nombre": checked.get("active_workspace_name") or "Sin información",
    }


__all__ = (
    "SAP_B1_VIEWS",
    "app_name_in_use",
    "app_name_lock",
    "app_name_lock_key",
    "app_viewer_path",
    "control_room_summary",
    "create_analytic_app",
    "document_search",
    "pipeline_automations",
    "public_base_url",
    "published_datasets",
    "require_gateway_user",
    "sap_b1_views",
    "start_sync",
    "sync_run",
    "sync_run_origin",
    "talent_nine_box",
    "workspace_summary",
)
