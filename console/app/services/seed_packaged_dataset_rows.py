from __future__ import annotations

import json

import asyncpg


async def set_seed_scope(
    conn: asyncpg.Connection, tenant_id: object, workspace_id: object
) -> None:
    await conn.execute(
        """SELECT pg_catalog.set_config('app.tenant_id', $1, true),
                  pg_catalog.set_config('app.workspace_id', $2, true)""",
        str(tenant_id),
        str(workspace_id),
    )


async def datasets_has_column(conn: asyncpg.Connection, column_name: str) -> bool:
    return bool(
        await conn.fetchval(
            """SELECT EXISTS (
                   SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'datasets'
                      AND column_name = $1
               )""",
            column_name,
        )
    )


async def datasets_workspace_name_conflict_available(
    conn: asyncpg.Connection,
) -> bool:
    return bool(
        await conn.fetchval(
            """SELECT EXISTS (
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
                   AND index_record.indisunique AND index_record.indisvalid
                   AND index_record.indisready AND index_record.indimmediate
                   AND (
                       SELECT array_agg(attribute.attname::text ORDER BY key.ordinality)
                         FROM pg_catalog.unnest(constraint_record.conkey)
                              WITH ORDINALITY AS key(attnum, ordinality)
                         JOIN pg_catalog.pg_attribute AS attribute
                           ON attribute.attrelid = constraint_record.conrelid
                          AND attribute.attnum = key.attnum
                   ) = ARRAY['workspace_id', 'name']::text[]
            )"""
        )
    )


async def require_workspace_scoped_dataset_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("LOCK TABLE public.datasets IN ROW SHARE MODE")
    if not await datasets_workspace_name_conflict_available(conn):
        raise RuntimeError(
            "datasets_workspace_name_key is required for packaged dataset seeding"
        )


async def update_dataset_row(
    conn: asyncpg.Connection,
    *,
    dataset: dict,
    tenant_id: object,
    workspace_id: object,
    has_tenant_id: bool,
) -> bool:
    if has_tenant_id:
        status = await conn.execute(
            """UPDATE public.datasets
               SET layer = $2, cartridge = $3, sources = $4::jsonb,
                   sql_def = $5, description = $6, updated_at = NOW(),
                   tenant_id = $7, workspace_id = $8
             WHERE name = $1 AND workspace_id = $8::uuid""",
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
            """UPDATE public.datasets
               SET layer = $2, cartridge = $3, sources = $4::jsonb,
                   sql_def = $5, description = $6, updated_at = NOW(),
                   workspace_id = $7
             WHERE name = $1 AND workspace_id = $7::uuid""",
            dataset["name"],
            dataset["layer"],
            dataset["cartridge"],
            json.dumps(dataset["sources"]),
            dataset["sql"],
            dataset["description"],
            workspace_id,
        )
    return status.split()[-1] != "0"


async def insert_dataset_row(
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
