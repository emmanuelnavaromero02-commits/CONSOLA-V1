from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.security_context import build_security_context
from app.services.gold_publication_relation import published_relation_column_types
from app.services.gold_publication_relation import resolve_published_gold_relation

logger = logging.getLogger(__name__)

DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

HIDDEN_GOLD_SOURCE_CARTRIDGES = {
    item.strip()
    for item in os.environ.get(
        "OMEGA_HIDDEN_GOLD_SOURCE_CARTRIDGES", "simulation"
    ).split(",")
    if item.strip()
}


def gold_dataset_from_source(source: str) -> str:
    raw = str(source or "").strip()
    if raw.startswith("gold/"):
        dataset = raw.split("/", 1)[1].strip()
        if DATASET_NAME_RE.fullmatch(dataset):
            return dataset
    return ""


def gold_schema_dsn() -> str:
    return (os.environ.get("GOLD_DATABASE_URL") or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def empty_catalog_payload() -> dict[str, Any]:
    return {"datasets": {}, "relationships": []}


def gold_source_visible_in_viewers(cartridge: str, dataset: str) -> bool:
    cartridge = (cartridge or "").strip()
    dataset = (dataset or "").strip()
    if cartridge in HIDDEN_GOLD_SOURCE_CARTRIDGES:
        return False
    if dataset.startswith("sim_dataset_"):
        return False
    return True


async def _published_gold_heads(tenant_id: str, workspace_id: str) -> dict[str, int]:
    dsn = gold_schema_dsn()
    if not dsn or not tenant_id or not workspace_id:
        return {}
    import asyncpg as _asyncpg

    conn = await _asyncpg.connect(dsn, command_timeout=10)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            rows = await conn.fetch(
                """SELECT h.dataset,COALESCE(r.row_count,0) AS row_count
                     FROM omega_publication.dataset_publication_heads h
                     JOIN omega_publication.materialization_runs r
                       ON r.materialization_run_id=h.materialization_run_id
                    WHERE h.tenant_id=$1 AND h.workspace_id=$2 AND h.layer='gold'
                      AND r.status IN ('published','legacy_unverified')""",
                tenant_id,
                workspace_id,
            )
            return {str(row["dataset"]): int(row["row_count"] or 0) for row in rows}
    finally:
        await conn.close()


@dataclass(frozen=True)
class GoldCatalogRuntime:
    get_db_pool: Callable[[], Awaitable[Any]]
    workspace_scope_for_apps_filter: Callable[[dict | None], Awaitable[tuple[str, str]]]
    allowed_cartridges_for_user: Callable[[dict | None], set[str] | None]

    async def catalog_scope_for_dataset(
        self, dataset: str, user: dict | None
    ) -> tuple[str, str]:
        ctx = build_security_context(user)
        tenant_id = str(
            ctx.get("tenant_id")
            or (user or {}).get("active_tenant_id")
            or (user or {}).get("tenant_id")
            or ""
        ).strip()
        workspace_id = str(
            ctx.get("workspace_id")
            or (user or {}).get("active_workspace_id")
            or (user or {}).get("workspace_id")
            or ""
        ).strip()
        if workspace_id:
            if not tenant_id:
                try:
                    pool = await self.get_db_pool()
                    tenant_id = str(
                        await pool.fetchval(
                            "SELECT tenant_id::text FROM workspaces WHERE id = $1::uuid",
                            workspace_id,
                        )
                        or ""
                    ).strip()
                except Exception:
                    logger.debug(
                        "Failed to resolve tenant for Gold schema source",
                        exc_info=True,
                    )
            return tenant_id, workspace_id
        return "", ""

    async def sources_from_catalog(self, user: dict | None) -> list[str]:
        try:
            pool = await self.get_db_pool()
            tenant_id, workspace_id = await self.workspace_scope_for_apps_filter(user)
        except Exception:
            logger.debug("Gold source catalog fallback unavailable", exc_info=True)
            return []
        allowed = self.allowed_cartridges_for_user(user)
        if workspace_id:
            rows = await pool.fetch(
                """
                SELECT DISTINCT name, cartridge
                 FROM datasets
                 WHERE layer = 'gold'
                   AND workspace_id = $1::uuid
                 ORDER BY name
                """,
                workspace_id,
            )
        else:
            return []
        try:
            heads = await _published_gold_heads(tenant_id, workspace_id)
        except Exception:
            return []
        sources: list[str] = []
        for row in rows:
            cartridge = str(row["cartridge"] or "").strip()
            name = str(row["name"] or "").strip()
            if name not in heads:
                continue
            if not gold_source_visible_in_viewers(cartridge, name):
                continue
            if allowed is not None and cartridge and cartridge not in allowed:
                continue
            if DATASET_NAME_RE.fullmatch(name):
                sources.append(f"gold/{name}")
        return sorted(set(sources))

    async def semantic_entities_from_catalog(
        self, cartridge: str, user: dict | None
    ) -> list[dict[str, Any]]:
        cartridge = (cartridge or "").strip()
        if not cartridge or cartridge in HIDDEN_GOLD_SOURCE_CARTRIDGES:
            return []
        try:
            pool = await self.get_db_pool()
            tenant_id, workspace_id = await self.workspace_scope_for_apps_filter(user)
        except Exception:
            logger.debug("Gold semantic catalog fallback unavailable", exc_info=True)
            return []

        if workspace_id:
            rows = await pool.fetch(
                """
                SELECT DISTINCT name, row_count
                  FROM datasets
                 WHERE layer = 'gold'
                   AND cartridge = $1
                   AND workspace_id = $2::uuid
                 ORDER BY name
                 LIMIT 24
                """,
                cartridge,
                workspace_id,
            )
        else:
            return []
        try:
            heads = await _published_gold_heads(tenant_id, workspace_id)
        except Exception:
            return []

        entities: list[dict[str, Any]] = []
        for row in rows:
            dataset = str(row["name"] or "").strip()
            if dataset not in heads:
                continue
            if not DATASET_NAME_RE.fullmatch(
                dataset
            ) or not gold_source_visible_in_viewers(cartridge, dataset):
                continue
            try:
                payload = await self.schema_payload(f"gold/{dataset}", user)
            except HTTPException:
                continue
            preview = payload.get("preview") or {}
            columns = preview.get("columns") or preview.get("schema") or []
            fields = [
                {
                    "name": str(col.get("name") or ""),
                    "type": str(col.get("type") or ""),
                }
                for col in columns
                if isinstance(col, dict) and str(col.get("name") or "").strip()
            ]
            entities.append(
                {
                    "entity": dataset,
                    "name": dataset,
                    "display_name": dataset.replace("_", " ").title(),
                    "mode": "gold",
                    "modes": ["gold"],
                    "fields": fields,
                    "columns": fields,
                    "row_count": heads[dataset],
                    "source": "gold_catalog",
                }
            )
        return entities

    async def schema_payload(self, source: str, user: dict | None) -> dict[str, Any]:
        dataset = gold_dataset_from_source(source)
        if not dataset:
            raise HTTPException(400, "Invalid Gold source")
        dsn = gold_schema_dsn()
        if not dsn:
            raise HTTPException(503, "Gold database is not configured")
        tenant_id, workspace_id = await self.catalog_scope_for_dataset(dataset, user)
        if not workspace_id:
            raise HTTPException(404, f"No se encontró el conjunto de datos '{dataset}'")
        try:
            import asyncpg as _asyncpg

            conn = await _asyncpg.connect(dsn, command_timeout=10)
        except Exception as exc:
            raise HTTPException(503, "Gold database unavailable") from exc
        try:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                    tenant_id or "",
                    workspace_id,
                )
                relation = await resolve_published_gold_relation(
                    conn, tenant_id, workspace_id, dataset
                )
                column_types = await published_relation_column_types(conn, relation)
                columns = [
                    {"name": name, "type": data_type}
                    for name, data_type in column_types.items()
                ]
                names = {col["name"] for col in columns}
                if "workspace_id" not in names:
                    raise HTTPException(
                        404, f"No se encontró el conjunto de datos '{dataset}'"
                    )
                rows = await conn.fetch(f"SELECT * FROM {relation.sql} LIMIT 5")
        finally:
            await conn.close()
        preview_rows = [dict(row) for row in rows]
        return {
            "source": source,
            "source_kind": "gold",
            "dataset": dataset,
            "partitions": {
                "source": source,
                "kind": "gold",
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "partitions": [],
            },
            "preview": {
                "source": source,
                "schema": columns,
                "columns": columns,
                "rows": preview_rows,
                "data": preview_rows,
            },
        }
