"""Seed packaged cartridge datasets from /registry/cartridges/*/datasets.

MEJORAS ships Replicon analytical datasets as SQL files. Existing beta
databases may already contain rows for the same names with older SQL, so
Console startup refreshes the dataset rows from the mounted cartridge source
tree. The SQL text is stored exactly as packaged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
from typing import Any

import asyncpg


logger = logging.getLogger(__name__)

_REGISTRY = pathlib.Path("/registry/cartridges")
_EXPECTED_CATALOG_FILES = 229
_EXPECTED_CATALOG_DIGEST = (
    "e7e043afc59c4b49c339bc08545707dc84450c206f6693ebc0a8e12c488cf2b0"
)
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
    paths = sorted(
        (path for cartridge_paths in out.values() for path in cartridge_paths),
        key=lambda path: path.relative_to(_REGISTRY).as_posix(),
    )
    entries = [
        f"{path.relative_to(_REGISTRY).as_posix()}\0"
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in paths
    ]
    digest = hashlib.sha256(("\n".join(entries) + "\n").encode()).hexdigest()
    if len(entries) != _EXPECTED_CATALOG_FILES or digest != _EXPECTED_CATALOG_DIGEST:
        raise ValueError(
            "packaged dataset catalog integrity check failed: "
            f"expected {_EXPECTED_CATALOG_FILES} versioned files"
        )
    return out


def _parse_dataset(sql_path: pathlib.Path) -> dict:
    sql = sql_path.read_text(encoding="utf-8")
    first_line = sql.splitlines()[0] if sql.splitlines() else ""
    match = _HEADER_RE.match(first_line)
    if match is None:
        raise ValueError(f"{sql_path}: missing or invalid packaged dataset header")
    name = match.group("name")
    layer = match.group("layer").strip().lower()
    cartridge = match.group("cartridge").strip()
    if layer not in {"gold", "master", "silver"}:
        raise ValueError(f"{sql_path}: unsupported packaged dataset layer {layer!r}")
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
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{sql_path}: invalid packaged dataset sources"
                ) from exc
            if not isinstance(parsed, list) or not all(
                isinstance(item, str) and item.strip() for item in parsed
            ):
                raise ValueError(
                    f"{sql_path}: packaged dataset sources must be a string list"
                )
            sources = list(parsed)
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


def _load_packaged_manifest(
    packaged: dict[str, list[pathlib.Path]],
) -> dict[str, list[dict]]:
    """Validate the complete selected manifest before the first database write."""

    if not packaged:
        raise ValueError("packaged dataset manifest is empty")
    manifest: dict[str, list[dict]] = {}
    names: dict[str, pathlib.Path] = {}
    for cartridge_id in sorted(packaged):
        sql_files = sorted(packaged[cartridge_id], key=lambda path: path.as_posix())
        if not sql_files:
            raise ValueError(f"packaged dataset manifest is empty for {cartridge_id}")
        datasets: list[dict] = []
        for sql_path in sql_files:
            dataset = _parse_dataset(sql_path)
            if dataset["cartridge"] != cartridge_id:
                raise ValueError(
                    f"{sql_path}: cartridge {dataset['cartridge']!r} does not match "
                    f"manifest {cartridge_id!r}"
                )
            if dataset["name"] in names:
                raise ValueError(
                    f"duplicate packaged dataset {dataset['name']!r}: "
                    f"{names[dataset['name']]} and {sql_path}"
                )
            names[dataset["name"]] = sql_path
            datasets.append(dataset)
        manifest[cartridge_id] = datasets
    return manifest


async def _set_seed_scope(
    conn: asyncpg.Connection, tenant_id: object, workspace_id: object
) -> None:
    await conn.execute(
        """
        SELECT
            pg_catalog.set_config('app.tenant_id', $1, true),
            pg_catalog.set_config('app.workspace_id', $2, true)
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
                  FROM pg_catalog.pg_constraint AS constraint_record
                  JOIN pg_catalog.pg_index AS index_record
                    ON index_record.indexrelid = constraint_record.conindid
                 WHERE constraint_record.conrelid = 'public.datasets'::regclass
                   AND constraint_record.conname = 'datasets_workspace_name_key'
                   AND constraint_record.contype = 'u'
                   AND constraint_record.convalidated
                   AND NOT constraint_record.condeferrable
                   AND NOT constraint_record.condeferred
                   AND index_record.indisunique
                   AND index_record.indisvalid
                   AND index_record.indisready
                   AND index_record.indimmediate
                   AND (
                       SELECT array_agg(attribute.attname::text ORDER BY key.ordinality)
                         FROM pg_catalog.unnest(constraint_record.conkey) WITH ORDINALITY
                              AS key(attnum, ordinality)
                         JOIN pg_catalog.pg_attribute AS attribute
                           ON attribute.attrelid = constraint_record.conrelid
                          AND attribute.attnum = key.attnum
                   ) = ARRAY['workspace_id', 'name']::text[]
            )
            """
        )
    )


async def _require_workspace_scoped_dataset_schema(
    conn: asyncpg.Connection,
) -> None:
    """Stabilize the table schema and reject every unscoped write shape."""

    await conn.execute("LOCK TABLE public.datasets IN ROW SHARE MODE")
    if not await _datasets_workspace_name_conflict_available(conn):
        raise RuntimeError(
            "datasets_workspace_name_key is required for packaged dataset seeding"
        )


async def _update_dataset_row(
    conn: asyncpg.Connection,
    *,
    dataset: dict,
    tenant_id: object,
    workspace_id: object,
    has_tenant_id: bool,
) -> bool:
    """Update only the definition owned by the explicit workspace."""

    if has_tenant_id:
        status = await conn.execute(
            """
            UPDATE public.datasets
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
            UPDATE public.datasets
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
            """INSERT INTO public.datasets
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
            """INSERT INTO public.datasets
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
) -> None:
    if await _update_dataset_row(
        conn,
        dataset=dataset,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        has_tenant_id=has_tenant_id,
    ):
        return
    try:
        async with conn.transaction():
            await _insert_dataset_row(
                conn,
                dataset=dataset,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                has_tenant_id=has_tenant_id,
            )
    except asyncpg.UniqueViolationError:
        updated = await _update_dataset_row(
            conn,
            dataset=dataset,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            has_tenant_id=has_tenant_id,
        )
        if not updated:
            raise


async def _seed_packaged_dataset_rows(
    conn: asyncpg.Connection,
    *,
    packaged: dict[str, list[pathlib.Path]],
    target_workspaces: list[Any],
    has_tenant_id: bool,
) -> dict[str, int]:
    manifest = _load_packaged_manifest(packaged)
    ordered_workspaces = sorted(
        target_workspaces,
        key=lambda workspace: (str(workspace["tenant_id"]), str(workspace["id"])),
    )
    seeded_by_cartridge: dict[str, int] = {}
    for cartridge_id, datasets in manifest.items():
        seeded_rows = 0
        for workspace in ordered_workspaces:
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
                )
                seeded_rows += 1

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
        raise ValueError("packaged dataset manifest is empty")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_workspace_scoped_dataset_schema(conn)
            workspaces = await conn.fetch(
                """
                SELECT id, tenant_id
                  FROM public.workspaces
                 ORDER BY created_at ASC, name ASC, id ASC
                """,
            )
            if not workspaces:
                logger.warning("[seed_packaged_datasets] no workspace found; skipping")
                return
            has_tenant_id = await _datasets_has_column(conn, "tenant_id")
            await _seed_packaged_dataset_rows(
                conn,
                packaged=packaged,
                target_workspaces=list(workspaces),
                has_tenant_id=has_tenant_id,
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
        raise ValueError("packaged dataset manifest is empty")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_workspace_scoped_dataset_schema(conn)
            has_tenant_id = await _datasets_has_column(conn, "tenant_id")
            seeded_by_cartridge = await _seed_packaged_dataset_rows(
                conn,
                packaged=packaged,
                target_workspaces=[{"id": workspace_id, "tenant_id": tenant_id}],
                has_tenant_id=has_tenant_id,
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
