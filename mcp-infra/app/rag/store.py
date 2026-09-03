from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

from app.rag.config import PG_DSN
from app.rag.chunker import TextChunk

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = PG_DSN.replace("postgresql+psycopg2://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, init=_init_conn)
    return _pool


def _scope_values(scope: dict | None) -> tuple[str | None, str | None, bool]:
    scope = scope if isinstance(scope, dict) else {}
    role = str(scope.get("role") or "").lower()
    allowed = {
        str(item).strip()
        for item in (scope.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    tenant_id = str(scope.get("tenant_id") or "").strip()
    workspace_id = str(scope.get("workspace_id") or "").strip()
    platform_admin = role in {"admin", "owner", "super_admin"} and not (tenant_id or workspace_id) and "*" in allowed
    return tenant_id or None, workspace_id or None, platform_admin


def _cartridge_filter(
    requested: list[str] | None,
    scope: dict | None,
) -> list[str] | None:
    # Internal maintenance callers predate scoped capability contexts.  Keep
    # those calls working, while treating an explicitly supplied scope with no
    # cartridge entitlement as deny-all.
    if scope is None:
        wanted = {
            str(value).strip() for value in (requested or []) if str(value).strip()
        }
        return sorted(wanted) if wanted else None
    scope = scope if isinstance(scope, dict) else {}
    allowed = {
        str(value).strip()
        for value in (scope.get("allowed_cartridges") or [])
        if str(value).strip()
    }
    wanted = {
        str(value).strip() for value in (requested or []) if str(value).strip()
    }
    if "*" in allowed:
        return sorted(wanted) if wanted else None
    if not allowed:
        return ["__no_authorized_cartridge__"]
    effective = allowed & wanted if wanted else allowed
    return sorted(effective) if effective else ["__no_authorized_cartridge__"]


def _cartridge_read_filter(
    requested: list[str] | None,
    scope: dict | None,
) -> tuple[list[str] | None, bool]:
    """Return the entitled cartridge filter and whether workspace docs are included.

    An explicit cartridge request is strict (used by agent ``rag_filter``) and
    never includes unclassified documents.  A normal workspace listing/search
    includes its own documents plus cartridge sources the caller may access.
    """
    explicit = bool(
        [value for value in (requested or []) if str(value).strip()]
    )
    filtered = _cartridge_filter(requested, scope)
    if explicit:
        return filtered, False
    if scope is None:
        return filtered, False
    scope = scope if isinstance(scope, dict) else {}
    allowed = {
        str(value).strip()
        for value in (scope.get("allowed_cartridges") or [])
        if str(value).strip()
    }
    if "*" in allowed:
        return None, True
    return (sorted(allowed) if allowed else []), True


async def _set_rls_context(conn: asyncpg.Connection, scope: dict | None) -> tuple[str | None, str | None, bool]:
    tenant_id, workspace_id, platform_admin = _scope_values(scope)
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id or "")
    await conn.execute("SELECT set_config('app.workspace_id', $1, true)", workspace_id or "")
    await conn.execute("SELECT set_config('app.platform_admin', $1, true)", "true" if platform_admin else "false")
    return tenant_id, workspace_id, platform_admin


def _scope_where(
    *,
    tenant_id: str | None,
    workspace_id: str | None,
    platform_admin: bool,
    alias: str = "s",
) -> tuple[str, list[str]]:
    if platform_admin:
        return "", []
    if not tenant_id or not workspace_id:
        return " AND 1 = 0", []
    return f" AND {alias}.tenant_id::text = $SCOPE_TENANT AND {alias}.workspace_id::text = $SCOPE_WORKSPACE", [
        tenant_id,
        workspace_id,
    ]


def _bind_scope_placeholders(sql: str, params: list, scope_params: list[str]) -> str:
    for value in scope_params:
        params.append(value)
        placeholder = f"${len(params)}"
        if "$SCOPE_TENANT" in sql:
            sql = sql.replace("$SCOPE_TENANT", placeholder, 1)
        else:
            sql = sql.replace("$SCOPE_WORKSPACE", placeholder, 1)
    return sql


async def list_sources(
    kinds: list[str] | None = None,
    cartridges: list[str] | None = None,
    source_prefixes: list[str] | None = None,
    scope: dict | None = None,
) -> list[dict]:
    cartridges, include_workspace_documents = _cartridge_read_filter(
        cartridges, scope
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, workspace_id, platform_admin = await _set_rls_context(conn, scope)
            params: list = []
            where = "WHERE 1 = 1"
            scope_sql, scope_params = _scope_where(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                platform_admin=platform_admin,
                alias="s",
            )
            where += scope_sql
            if kinds:
                params.append(kinds)
                where += f" AND s.kind = ANY(${len(params)})"
            literal_prefixes = [
                str(value) for value in (source_prefixes or []) if str(value)
            ]
            if literal_prefixes:
                params.append(literal_prefixes)
                where += (
                    " AND EXISTS (SELECT 1 "
                    f"FROM unnest(${len(params)}::text[]) AS prefix(value) "
                    "WHERE starts_with(s.name, prefix.value))"
                )
            if cartridges and include_workspace_documents:
                params.append(cartridges)
                where += (
                    f" AND (s.cartridge_id = ANY(${len(params)}) "
                    "OR s.cartridge_id IS NULL)"
                )
            elif cartridges:
                params.append(cartridges)
                where += f" AND s.cartridge_id = ANY(${len(params)})"
            elif include_workspace_documents:
                where += " AND s.cartridge_id IS NULL"
            sql = (
                "SELECT id, name, description, mime_type, size_chars, chunk_count, kind, "
                "tenant_id::text AS tenant_id, workspace_id::text AS workspace_id, "
                "cartridge_id, visibility, created_at "
                f"FROM rag_sources s {where} ORDER BY created_at DESC"
            )
            sql = _bind_scope_placeholders(sql, params, scope_params)
            rows = await conn.fetch(sql, *params)
    return [
        {**dict(r), "created_at": str(r["created_at"])[:19]}
        for r in rows
    ]


async def get_source(source_id: int, scope: dict | None = None) -> dict | None:
    """Load one source under exact tenant/workspace RLS for authorization."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, workspace_id, platform_admin = await _set_rls_context(conn, scope)
            params: list = [source_id]
            scope_sql, scope_params = _scope_where(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                platform_admin=platform_admin,
                alias="s",
            )
            sql = (
                "SELECT id, name, kind, cartridge_id, visibility, "
                "tenant_id::text AS tenant_id, workspace_id::text AS workspace_id "
                "FROM rag_sources s WHERE id = $1" + scope_sql
            )
            sql = _bind_scope_placeholders(sql, params, scope_params)
            row = await conn.fetchrow(sql, *params)
    return dict(row) if row else None


async def delete_source(
    source_id: int,
    scope: dict | None = None,
    *,
    user_owned_document_only: bool = False,
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, workspace_id, platform_admin = await _set_rls_context(conn, scope)
            if user_owned_document_only and (
                not tenant_id or not workspace_id or platform_admin
            ):
                return False
            params: list = [source_id]
            scope_sql, scope_params = _scope_where(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                platform_admin=platform_admin,
                alias="rag_sources",
            )
            sql = "DELETE FROM rag_sources WHERE id = $1" + scope_sql
            if user_owned_document_only:
                # Repeat the endpoint provenance check in the atomic DELETE so
                # a concurrent re-index cannot turn a checked document into a
                # protected server-managed source between SELECT and DELETE.
                sql += (
                    " AND kind = 'document'"
                    " AND cartridge_id IS NULL"
                    " AND visibility = 'workspace'"
                    " AND NOT starts_with(name, 'raw:')"
                    " AND NOT starts_with(name, 'dataset:')"
                    " AND NOT starts_with(name, '_semantic_')"
                )
            sql = _bind_scope_placeholders(sql, params, scope_params)
            result = await conn.execute(sql, *params)
    return result == "DELETE 1"


async def ingest_chunks(
    source_name: str,
    source_desc: str,
    mime_type: str,
    size_chars: int,
    chunks: list[TextChunk],
    embeddings: dict[int, list[float]],   # child_index → vector
    kind: str = "document",
    cartridge_id: str | None = None,
    scope: dict | None = None,
) -> dict:
    allowed = _cartridge_filter([cartridge_id] if cartridge_id else None, scope)
    if cartridge_id and allowed == ["__no_authorized_cartridge__"]:
        raise PermissionError("RAG cartridge is outside caller scope")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, workspace_id, platform_admin = await _set_rls_context(conn, scope)
            visibility = "platform" if platform_admin else "workspace"
            source_id: int = await conn.fetchval("""
                INSERT INTO rag_sources
                    (name, description, mime_type, size_chars, kind, cartridge_id,
                     tenant_id, workspace_id, visibility)
                VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8::uuid, $9)
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    mime_type   = EXCLUDED.mime_type,
                    size_chars  = EXCLUDED.size_chars,
                    kind        = EXCLUDED.kind,
                    cartridge_id = EXCLUDED.cartridge_id,
                    tenant_id   = EXCLUDED.tenant_id,
                    workspace_id = EXCLUDED.workspace_id,
                    visibility  = EXCLUDED.visibility,
                    updated_at  = NOW()
                RETURNING id
            """, source_name, source_desc, mime_type, size_chars, kind,
                cartridge_id, tenant_id, workspace_id, visibility)

            await conn.execute("DELETE FROM rag_chunks WHERE source_id = $1", source_id)

            parent_ids: dict[int, int] = {}
            for c in chunks:
                if c.chunk_type != "parent":
                    continue
                db_id: int = await conn.fetchval("""
                    INSERT INTO rag_chunks
                        (source_id, chunk_type, chunk_index, content, tenant_id, workspace_id)
                    VALUES ($1, 'parent', $2, $3, $4::uuid, $5::uuid) RETURNING id
                """, source_id, c.index, c.content, tenant_id, workspace_id)
                parent_ids[c.index] = db_id

            child_count = 0
            for c in chunks:
                if c.chunk_type != "child":
                    continue
                vec = embeddings.get(c.index)
                await conn.execute("""
                    INSERT INTO rag_chunks
                        (source_id, chunk_type, parent_id, chunk_index, content, embedding, tenant_id, workspace_id)
                    VALUES ($1, 'child', $2, $3, $4, $5, $6::uuid, $7::uuid)
                """, source_id, parent_ids.get(c.parent_index), c.index, c.content, vec, tenant_id, workspace_id)
                child_count += 1

            await conn.execute(
                "UPDATE rag_sources SET chunk_count = $1, updated_at = NOW() WHERE id = $2",
                child_count, source_id,
            )

    return {
        "source_id": source_id,
        "parents":   len(parent_ids),
        "children":  child_count,
    }


async def search(
    query_vec: list[float],
    top_k: int = 5,
    source_ids: list[int] | None = None,
    kinds: list[str] | None = None,
    cartridges: list[str] | None = None,
    source_prefixes: list[str] | None = None,
    scope: dict | None = None,
) -> list[dict]:
    cartridges, include_workspace_documents = _cartridge_read_filter(
        cartridges, scope
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, workspace_id, platform_admin = await _set_rls_context(conn, scope)
            params: list = [query_vec, top_k * 3]
            extras: list[str] = []
            scope_sql, scope_params = _scope_where(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                platform_admin=platform_admin,
                alias="s",
            )
            extras.append(scope_sql.strip())
            if source_ids:
                params.append(source_ids)
                extras.append(f"AND c.source_id = ANY(${len(params)})")
            if kinds:
                params.append(kinds)
                extras.append(f"AND s.kind = ANY(${len(params)})")
            literal_prefixes = [
                str(value) for value in (source_prefixes or []) if str(value)
            ]
            if literal_prefixes:
                params.append(literal_prefixes)
                extras.append(
                    "AND EXISTS (SELECT 1 "
                    f"FROM unnest(${len(params)}::text[]) AS prefix(value) "
                    "WHERE starts_with(s.name, prefix.value))"
                )
            if cartridges and include_workspace_documents:
                params.append(cartridges)
                extras.append(
                    f"AND (s.cartridge_id = ANY(${len(params)}) "
                    "OR s.cartridge_id IS NULL)"
                )
            elif cartridges:
                params.append(cartridges)
                extras.append(f"AND s.cartridge_id = ANY(${len(params)})")
            elif include_workspace_documents:
                extras.append("AND s.cartridge_id IS NULL")
            extra = (" " + " ".join(e for e in extras if e)) if extras else ""
            sql = f"""
                SELECT
                    p.id          AS parent_id,
                    p.content     AS context,
                    s.name        AS source_name,
                    s.id          AS source_id,
                    s.kind        AS source_kind,
                    s.cartridge_id AS cartridge_id,
                    s.tenant_id::text AS tenant_id,
                    s.workspace_id::text AS workspace_id,
                    c.content     AS child_content,
                    1 - (c.embedding <=> $1) AS similarity
                FROM rag_chunks c
                JOIN rag_chunks  p ON p.id = c.parent_id
                JOIN rag_sources s ON s.id = c.source_id
                WHERE c.chunk_type = 'child' AND c.embedding IS NOT NULL{extra}
                ORDER BY c.embedding <=> $1
                LIMIT $2
            """
            sql = _bind_scope_placeholders(sql, params, scope_params)
            rows = await conn.fetch(sql, *params)

    seen: set[int] = set()
    results: list[dict] = []
    for r in rows:
        pid = r["parent_id"]
        if pid not in seen:
            seen.add(pid)
            results.append({
                "parent_id":   pid,
                "source_id":   r["source_id"],
                "source_name": r["source_name"],
                "source_kind": r["source_kind"],
                "cartridge_id": r["cartridge_id"],
                "context":     r["context"],
                "child_content": r["child_content"],
                "similarity":  float(r["similarity"]),
            })
        if len(results) >= top_k:
            break
    return results
