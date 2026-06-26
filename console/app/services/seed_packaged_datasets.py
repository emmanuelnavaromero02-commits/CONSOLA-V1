"""Seed packaged cartridge datasets from /registry/cartridges/*/datasets.

MEJORAS ships Replicon analytical datasets as SQL files. Existing beta
databases may already contain rows for the same names with older SQL, so
Console startup refreshes the dataset rows from the mounted cartridge source
tree. The SQL text is stored exactly as packaged.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re

import asyncpg


logger = logging.getLogger(__name__)

_REGISTRY = pathlib.Path("/registry/cartridges")
_HEADER_RE = re.compile(
    r"^--\s*(?P<name>[A-Za-z_][\w]*)\s+\((?P<layer>[^)]+)\)\s+cartridge:\s*(?P<cartridge>[\w-]+)",
)


def _dataset_files() -> dict[str, list[pathlib.Path]]:
    if not _REGISTRY.exists():
        return {}
    out: dict[str, list[pathlib.Path]] = {}
    for datasets_dir in _REGISTRY.glob("*/datasets"):
        if datasets_dir.is_dir():
            out[datasets_dir.parent.name] = sorted(datasets_dir.glob("*.sql"))
    return out


def _parse_dataset(sql_path: pathlib.Path) -> dict:
    sql = sql_path.read_text(encoding="utf-8")
    first_line = sql.splitlines()[0] if sql.splitlines() else ""
    match = _HEADER_RE.match(first_line)
    name = match.group("name") if match else sql_path.stem
    layer = (match.group("layer") if match else "silver").strip().lower()
    cartridge = (match.group("cartridge") if match else sql_path.parents[1].name).strip()
    if layer == "master":
        layer = "gold"

    sources: list[str] = []
    description = ""
    for line in sql.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("-- sources:"):
            raw = stripped.removeprefix("-- sources:").strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    sources = [str(item) for item in parsed]
            except Exception as exc:
                logger.warning(
                    "[seed_packaged_datasets] invalid sources in %s: %s",
                    sql_path, exc, exc_info=True,
                )
        elif stripped.startswith("-- description:"):
            description = stripped.removeprefix("-- description:").strip()

    return {
        "name": name,
        "layer": layer,
        "cartridge": cartridge,
        "sources": sources,
        "description": description,
        "sql": sql,
    }


async def _set_seed_scope(conn: asyncpg.Connection, tenant_id: object, workspace_id: object) -> None:
    await conn.execute(
        """
        SELECT
            set_config('app.tenant_id', $1, true),
            set_config('app.workspace_id', $2, true)
        """,
        str(tenant_id),
        str(workspace_id),
    )


async def _datasets_has_column(conn: asyncpg.Connection, column_name: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'datasets'
                   AND column_name = $1
            )
            """,
            column_name,
        )
    )


async def _datasets_workspace_name_conflict_available(conn: asyncpg.Connection) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conrelid = 'public.datasets'::regclass
                   AND conname = 'datasets_workspace_name_key'
            )
            """
        )
    )


async def seed_packaged_datasets(pool: asyncpg.Pool) -> None:
    packaged = _dataset_files()
    if not packaged:
        logger.info("[seed_packaged_datasets] no packaged datasets found")
        return

    async with pool.acquire() as conn:
        workspaces = await conn.fetch(
            """
            SELECT id, tenant_id
              FROM workspaces
             ORDER BY created_at ASC, name ASC
            """,
        )
        if not workspaces:
            logger.warning("[seed_packaged_datasets] no workspace found; skipping")
            return
        has_tenant_id = await _datasets_has_column(conn, "tenant_id")
        scoped_conflict = await _datasets_workspace_name_conflict_available(conn)
        target_workspaces = workspaces if scoped_conflict else workspaces[:1]
        if not scoped_conflict:
            logger.warning(
                "[seed_packaged_datasets] datasets_workspace_name_key missing; "
                "seeding packaged datasets only in the first workspace"
            )

        async with conn.transaction():
            for cartridge_id, sql_files in packaged.items():
                names: list[str] = []
                datasets: list[dict] = []
                for sql_path in sql_files:
                    try:
                        datasets.append(_parse_dataset(sql_path))
                    except Exception as exc:
                        logger.warning("[seed_packaged_datasets] skip %s: %s", sql_path, exc)
                        continue
                if not datasets:
                    continue

                names = [dataset["name"] for dataset in datasets]
                seeded_rows = 0
                for workspace in target_workspaces:
                    tenant_id = workspace["tenant_id"]
                    workspace_id = workspace["id"]
                    await _set_seed_scope(conn, tenant_id, workspace_id)

                    for dataset in datasets:
                        if has_tenant_id:
                            conflict_target = (
                                "(workspace_id, name)" if scoped_conflict else "(name)"
                            )
                            await conn.execute(
                                f"""INSERT INTO datasets
                                       (name, layer, cartridge, sources, sql_def, description,
                                        column_mapping, schedule, updated_at, tenant_id, workspace_id)
                                    VALUES ($1, $2, $3, $4::jsonb, $5, $6, '{{}}'::jsonb, NULL,
                                            NOW(), $7, $8)
                                    ON CONFLICT {conflict_target} DO UPDATE
                                       SET layer = EXCLUDED.layer,
                                           cartridge = EXCLUDED.cartridge,
                                           sources = EXCLUDED.sources,
                                           sql_def = EXCLUDED.sql_def,
                                           description = EXCLUDED.description,
                                           updated_at = NOW(),
                                           tenant_id = EXCLUDED.tenant_id,
                                           workspace_id = EXCLUDED.workspace_id""",
                                dataset["name"],
                                dataset["layer"],
                                dataset["cartridge"],
                                json.dumps(dataset["sources"]),
                                dataset["sql"],
                                dataset["description"],
                                tenant_id,
                                workspace_id,
                            )
                        else:
                            conflict_target = (
                                "(workspace_id, name)" if scoped_conflict else "(name)"
                            )
                            await conn.execute(
                                f"""INSERT INTO datasets
                                       (name, layer, cartridge, sources, sql_def, description,
                                        column_mapping, schedule, updated_at, workspace_id)
                                    VALUES ($1, $2, $3, $4::jsonb, $5, $6, '{{}}'::jsonb, NULL,
                                            NOW(), $7)
                                    ON CONFLICT {conflict_target} DO UPDATE
                                       SET layer = EXCLUDED.layer,
                                           cartridge = EXCLUDED.cartridge,
                                           sources = EXCLUDED.sources,
                                           sql_def = EXCLUDED.sql_def,
                                           description = EXCLUDED.description,
                                           updated_at = NOW(),
                                           workspace_id = EXCLUDED.workspace_id""",
                                dataset["name"],
                                dataset["layer"],
                                dataset["cartridge"],
                                json.dumps(dataset["sources"]),
                                dataset["sql"],
                                dataset["description"],
                                workspace_id,
                            )
                        seeded_rows += 1

                    if scoped_conflict:
                        await conn.execute(
                            """
                            DELETE FROM datasets
                             WHERE cartridge = $1
                               AND workspace_id = $2::uuid
                               AND NOT (name = ANY($3::text[]))
                            """,
                            cartridge_id,
                            workspace_id,
                            names,
                        )

                logger.info(
                    "[seed_packaged_datasets] %s: seeded %d dataset rows across %d workspaces",
                    cartridge_id,
                    seeded_rows,
                    len(target_workspaces),
                )
