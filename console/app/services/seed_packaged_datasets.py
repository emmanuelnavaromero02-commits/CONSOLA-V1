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
from typing import Any

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


async def _update_dataset_row(
    conn: asyncpg.Connection,
    *,
    dataset: dict,
    tenant_id: object,
    workspace_id: object,
    has_tenant_id: bool,
    scoped_conflict: bool,
) -> bool:
    """Update an existing dataset row without relying on a specific constraint.

    Some long-lived AWS beta databases passed through both the legacy
    ``datasets.name`` primary-key contract and the workspace-scoped contract.
    During that transition the named constraint may be absent or unusable for
    ``ON CONFLICT``. A deterministic update-first upsert keeps startup seeding
    compatible with both shapes.
    """
    if has_tenant_id:
        if scoped_conflict:
            status = await conn.execute(
                """
                UPDATE datasets
                   SET layer = $2,
                       cartridge = $3,
                       sources = $4::jsonb,
                       sql_def = $5,
                       description = $6,
                       updated_at = NOW(),
                       tenant_id = $7,
                       workspace_id = $8
                 WHERE name = $1
                   AND workspace_id = $8::uuid
                """,
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
            status = await conn.execute(
                """
                UPDATE datasets
                   SET layer = $2,
                       cartridge = $3,
                       sources = $4::jsonb,
                       sql_def = $5,
                       description = $6,
                       updated_at = NOW(),
                       tenant_id = $7,
                       workspace_id = $8
                 WHERE name = $1
                """,
                dataset["name"],
                dataset["layer"],
                dataset["cartridge"],
                json.dumps(dataset["sources"]),
                dataset["sql"],
                dataset["description"],
                tenant_id,
                workspace_id,
            )
    elif scoped_conflict:
        status = await conn.execute(
            """
            UPDATE datasets
               SET layer = $2,
                   cartridge = $3,
                   sources = $4::jsonb,
                   sql_def = $5,
                   description = $6,
                   updated_at = NOW(),
                   workspace_id = $7
             WHERE name = $1
               AND workspace_id = $7::uuid
            """,
            dataset["name"],
            dataset["layer"],
            dataset["cartridge"],
            json.dumps(dataset["sources"]),
            dataset["sql"],
            dataset["description"],
            workspace_id,
        )
    else:
        status = await conn.execute(
            """
            UPDATE datasets
               SET layer = $2,
                   cartridge = $3,
                   sources = $4::jsonb,
                   sql_def = $5,
                   description = $6,
                   updated_at = NOW(),
                   workspace_id = $7
             WHERE name = $1
            """,
            dataset["name"],
            dataset["layer"],
            dataset["cartridge"],
            json.dumps(dataset["sources"]),
            dataset["sql"],
            dataset["description"],
            workspace_id,
        )
    return status.split()[-1] != "0"


async def _insert_dataset_row(
    conn: asyncpg.Connection,
    *,
    dataset: dict,
    tenant_id: object,
    workspace_id: object,
    has_tenant_id: bool,
) -> None:
    if has_tenant_id:
        await conn.execute(
            """INSERT INTO datasets
                   (name, layer, cartridge, sources, sql_def, description,
                    column_mapping, schedule, updated_at, tenant_id, workspace_id)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, '{}'::jsonb, NULL,
                        NOW(), $7, $8)""",
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
                        NOW(), $7)""",
            dataset["name"],
            dataset["layer"],
            dataset["cartridge"],
            json.dumps(dataset["sources"]),
            dataset["sql"],
            dataset["description"],
            workspace_id,
        )


async def _upsert_dataset_row(
    conn: asyncpg.Connection,
    *,
    dataset: dict,
    tenant_id: object,
    workspace_id: object,
    has_tenant_id: bool,
    scoped_conflict: bool,
) -> None:
    if await _update_dataset_row(
        conn,
        dataset=dataset,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        has_tenant_id=has_tenant_id,
        scoped_conflict=scoped_conflict,
    ):
        return
    try:
        await _insert_dataset_row(
            conn,
            dataset=dataset,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            has_tenant_id=has_tenant_id,
        )
    except asyncpg.UniqueViolationError:
        await _update_dataset_row(
            conn,
            dataset=dataset,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            has_tenant_id=has_tenant_id,
            scoped_conflict=False,
        )


async def _seed_packaged_dataset_rows(
    conn: asyncpg.Connection,
    *,
    packaged: dict[str, list[pathlib.Path]],
    target_workspaces: list[Any],
    has_tenant_id: bool,
    scoped_conflict: bool,
) -> dict[str, int]:
    seeded_by_cartridge: dict[str, int] = {}
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
            seeded_by_cartridge[cartridge_id] = 0
            continue

        names = [dataset["name"] for dataset in datasets]
        seeded_rows = 0
        for workspace in target_workspaces:
            tenant_id = workspace["tenant_id"]
            workspace_id = workspace["id"]
            await _set_seed_scope(conn, tenant_id, workspace_id)

            for dataset in datasets:
                await _upsert_dataset_row(
                    conn,
                    dataset=dataset,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    has_tenant_id=has_tenant_id,
                    scoped_conflict=scoped_conflict,
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
        seeded_by_cartridge[cartridge_id] = seeded_rows
    return seeded_by_cartridge


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
            await _seed_packaged_dataset_rows(
                conn,
                packaged=packaged,
                target_workspaces=list(target_workspaces),
                has_tenant_id=has_tenant_id,
                scoped_conflict=scoped_conflict,
            )


async def seed_packaged_datasets_for_workspace(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str | None = None,
) -> dict[str, Any]:
    """Seed packaged dataset definitions for a workspace just before sync.

    Startup seeding covers workspaces that already exist. A newly-created
    tenant/workspace can trigger a cartridge sync before the next service
    restart, so sync-now needs a scoped idempotent seed to make Silver/Gold
    definitions available before Airflow starts materialization.
    """
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required")

    packaged = _dataset_files()
    if cartridge_id:
        cartridge_files = packaged.get(cartridge_id)
        packaged = {cartridge_id: cartridge_files} if cartridge_files else {}
    if not packaged:
        logger.warning(
            "[seed_packaged_datasets] no packaged datasets found for workspace sync cartridge=%s",
            cartridge_id or "*",
        )
        return {
            "status": "skipped",
            "reason": "no_packaged_datasets",
            "cartridge_id": cartridge_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "seeded_rows": 0,
            "cartridges": {},
        }

    async with pool.acquire() as conn:
        has_tenant_id = await _datasets_has_column(conn, "tenant_id")
        scoped_conflict = await _datasets_workspace_name_conflict_available(conn)
        if not scoped_conflict:
            logger.warning(
                "[seed_packaged_datasets] datasets_workspace_name_key missing; "
                "workspace sync seed will upsert by dataset name"
            )

        async with conn.transaction():
            seeded_by_cartridge = await _seed_packaged_dataset_rows(
                conn,
                packaged=packaged,
                target_workspaces=[{"id": workspace_id, "tenant_id": tenant_id}],
                has_tenant_id=has_tenant_id,
                scoped_conflict=scoped_conflict,
            )

    seeded_rows = sum(seeded_by_cartridge.values())
    return {
        "status": "success",
        "cartridge_id": cartridge_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "seeded_rows": seeded_rows,
        "cartridges": seeded_by_cartridge,
    }
