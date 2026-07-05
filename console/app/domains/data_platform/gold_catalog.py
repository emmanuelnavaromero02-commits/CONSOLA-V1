from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.dependencies import ROLE_ADMIN
from app.services.security_context import build_security_context

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


def gold_table_for_dataset(dataset: str) -> str:
    if not DATASET_NAME_RE.fullmatch(dataset or ""):
        raise HTTPException(400, "Invalid dataset name")
    return f"gold_{dataset}"


def quote_pg_ident(identifier: str) -> str:
    if not DATASET_NAME_RE.fullmatch(identifier or ""):
        raise HTTPException(400, "Invalid identifier")
    return '"' + identifier.replace('"', '""') + '"'


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
        if str((user or {}).get("role") or "").lower() not in {
            "owner",
            "super_admin",
            ROLE_ADMIN,
        }:
            return "", ""
        pool = await self.get_db_pool()
        row = await pool.fetchrow(
            """
            SELECT d.workspace_id::text AS workspace_id, w.tenant_id::text AS tenant_id
              FROM datasets d
              JOIN workspaces w ON w.id = d.workspace_id
             WHERE d.name = $1
               AND d.layer = 'gold'
               AND d.workspace_id IS NOT NULL
               AND COALESCE(d.row_count, 0) > 0
             ORDER BY d.updated_at DESC NULLS LAST,
                      d.last_refresh DESC NULLS LAST,
                      d.created_at DESC NULLS LAST
             LIMIT 1
            """,
            dataset,
        )
        if not row:
            return "", ""
        return str(row["tenant_id"] or "").strip(), str(row["workspace_id"] or "").strip()

    async def sources_from_catalog(self, user: dict | None) -> list[str]:
        try:
            pool = await self.get_db_pool()
            _tenant_id, workspace_id = await self.workspace_scope_for_apps_filter(user)
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
                   AND COALESCE(row_count, 0) > 0
                 ORDER BY name
                """,
                workspace_id,
            )
        elif str((user or {}).get("role") or "").lower() in {
            "owner",
            "super_admin",
            ROLE_ADMIN,
        }:
            rows = await pool.fetch(
                """
                SELECT DISTINCT name, cartridge
                  FROM datasets
                 WHERE layer = 'gold'
                   AND workspace_id IS NOT NULL
                   AND COALESCE(row_count, 0) > 0
                 ORDER BY name
                """
            )
        else:
            rows = []
        sources: list[str] = []
        for row in rows:
            cartridge = str(row["cartridge"] or "").strip()
            name = str(row["name"] or "").strip()
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
        """Build Semantic viewer cards from real Gold datasets."""
        cartridge = (cartridge or "").strip()
        if not cartridge or cartridge in HIDDEN_GOLD_SOURCE_CARTRIDGES:
            return []
        try:
            pool = await self.get_db_pool()
            _tenant_id, workspace_id = await self.workspace_scope_for_apps_filter(user)
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
                   AND COALESCE(row_count, 0) > 0
                 ORDER BY name
                 LIMIT 24
                """,
                cartridge,
                workspace_id,
            )
        elif str((user or {}).get("role") or "").lower() in {
            "owner",
            "super_admin",
            ROLE_ADMIN,
        }:
            rows = await pool.fetch(
                """
                SELECT DISTINCT name, row_count
                  FROM datasets
                 WHERE layer = 'gold'
                   AND cartridge = $1
                   AND workspace_id IS NOT NULL
                   AND COALESCE(row_count, 0) > 0
                 ORDER BY name
                 LIMIT 24
                """,
                cartridge,
            )
        else:
            rows = []

        entities: list[dict[str, Any]] = []
        for row in rows:
            dataset = str(row["name"] or "").strip()
            if not DATASET_NAME_RE.fullmatch(dataset) or not gold_source_visible_in_viewers(
                cartridge, dataset
            ):
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
                    "row_count": row["row_count"],
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
        table = gold_table_for_dataset(dataset)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                    tenant_id or "",
                    workspace_id,
                )
                exists = bool(
                    await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
                )
                if not exists:
                    raise HTTPException(
                        404, f"No se encontró el conjunto de datos '{dataset}'"
                    )
                column_rows = await conn.fetch(
                    """
                    SELECT column_name, data_type
                      FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = $1
                     ORDER BY ordinal_position
                    """,
                    table,
                )
                columns = [
                    {"name": str(row["column_name"]), "type": str(row["data_type"])}
                    for row in column_rows
                ]
                names = {col["name"] for col in columns}
                if "workspace_id" not in names:
                    raise HTTPException(
                        404, f"No se encontró el conjunto de datos '{dataset}'"
                    )
                values: list[Any] = [workspace_id]
                clauses = ["workspace_id::text = $1"]
                if "tenant_id" in names and tenant_id:
                    values.append(tenant_id)
                    clauses.append(f"tenant_id::text = ${len(values)}")
                values.append(5)
                rows = await conn.fetch(
                    (
                        f"SELECT * FROM public.{quote_pg_ident(table)} "
                        f"WHERE {' AND '.join(clauses)} LIMIT ${len(values)}"
                    ),
                    *values,
                )
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
