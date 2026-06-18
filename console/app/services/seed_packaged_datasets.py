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


async def _dataset_scope_for_name(
    conn: asyncpg.Connection,
    *,
    name: str,
    workspaces: list[asyncpg.Record],
    has_tenant_id: bool,
) -> tuple[object, object]:
    """Return the existing dataset scope when RLS makes a global lookup impossible.

    ``datasets.name`` is still globally unique in this schema generation, but
    the table is now FORCE RLS. A startup seed running under one workspace
    cannot update an older packaged row owned by another workspace; the
    conflict row is invisible and Postgres rejects the update. Probe each
    visible workspace scope and update existing rows in place. New rows keep
    the historical first-workspace seed behavior.
    """

    for workspace in workspaces:
        await _set_seed_scope(conn, workspace["tenant_id"], workspace["id"])
        if has_tenant_id:
            row = await conn.fetchrow(
                """
                SELECT tenant_id, workspace_id
                  FROM datasets
                 WHERE name = $1
                """,
                name,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT w.tenant_id, d.workspace_id
                  FROM datasets d
                  JOIN workspaces w ON w.id = d.workspace_id
                 WHERE d.name = $1
                """,
                name,
            )
        if row:
            return row["tenant_id"], row["workspace_id"]

    default_workspace = workspaces[0]
    return default_workspace["tenant_id"], default_workspace["id"]


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
        has_tenant_id = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'datasets'
                   AND column_name = 'tenant_id'
            )
            """,
        )

        async with conn.transaction():
            for cartridge_id, sql_files in packaged.items():
                names: list[str] = []
                touched_scopes: set[tuple[str, str]] = set()
                for sql_path in sql_files:
                    try:
                        dataset = _parse_dataset(sql_path)
                    except Exception as exc:
                        logger.warning("[seed_packaged_datasets] skip %s: %s", sql_path, exc)
                        continue

                    tenant_id, workspace_id = await _dataset_scope_for_name(
                        conn,
                        name=dataset["name"],
                        workspaces=workspaces,
                        has_tenant_id=bool(has_tenant_id),
                    )
                    await _set_seed_scope(conn, tenant_id, workspace_id)
                    touched_scopes.add((str(tenant_id), str(workspace_id)))

                    if has_tenant_id:
                        await conn.execute(
                            """INSERT INTO datasets
                                  (name, layer, cartridge, sources, sql_def, description,
                                   column_mapping, schedule, updated_at, tenant_id, workspace_id)
                               VALUES ($1, $2, $3, $4::jsonb, $5, $6, '{}'::jsonb, NULL,
                                       NOW(), $7, $8)
                               ON CONFLICT (name) DO UPDATE
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
                        await conn.execute(
                            """INSERT INTO datasets
                                  (name, layer, cartridge, sources, sql_def, description,
                                   column_mapping, schedule, updated_at, workspace_id)
                               VALUES ($1, $2, $3, $4::jsonb, $5, $6, '{}'::jsonb, NULL,
                                       NOW(), $7)
                               ON CONFLICT (name) DO UPDATE
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
                    names.append(dataset["name"])

                if names:
                    for tenant_id, workspace_id in sorted(touched_scopes):
                        await _set_seed_scope(conn, tenant_id, workspace_id)
                        await conn.execute(
                            "DELETE FROM datasets WHERE cartridge = $1 AND NOT (name = ANY($2::text[]))",
                            cartridge_id,
                            names,
                        )
                    logger.info(
                        "[seed_packaged_datasets] %s: seeded %d datasets",
                        cartridge_id,
                        len(names),
                    )
