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


async def list_sources(kinds: list[str] | None = None) -> list[dict]:
    pool = await get_pool()
    if kinds:
        rows = await pool.fetch(
            "SELECT id, name, description, mime_type, size_chars, chunk_count, kind, created_at "
            "FROM rag_sources WHERE kind = ANY($1) ORDER BY created_at DESC",
            kinds,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, name, description, mime_type, size_chars, chunk_count, kind, created_at "
            "FROM rag_sources ORDER BY created_at DESC"
        )
    return [
        {**dict(r), "created_at": str(r["created_at"])[:19]}
        for r in rows
    ]


async def delete_source(source_id: int) -> bool:
    pool = await get_pool()
    result = await pool.execute("DELETE FROM rag_sources WHERE id = $1", source_id)
    return result == "DELETE 1"


async def ingest_chunks(
    source_name: str,
    source_desc: str,
    mime_type: str,
    size_chars: int,
    chunks: list[TextChunk],
    embeddings: dict[int, list[float]],   # child_index → vector
    kind: str = "document",
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            source_id: int = await conn.fetchval("""
                INSERT INTO rag_sources (name, description, mime_type, size_chars, kind)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    mime_type   = EXCLUDED.mime_type,
                    size_chars  = EXCLUDED.size_chars,
                    kind        = EXCLUDED.kind,
                    updated_at  = NOW()
                RETURNING id
            """, source_name, source_desc, mime_type, size_chars, kind)

            await conn.execute("DELETE FROM rag_chunks WHERE source_id = $1", source_id)

            parent_ids: dict[int, int] = {}
            for c in chunks:
                if c.chunk_type != "parent":
                    continue
                db_id: int = await conn.fetchval("""
                    INSERT INTO rag_chunks (source_id, chunk_type, chunk_index, content)
                    VALUES ($1, 'parent', $2, $3) RETURNING id
                """, source_id, c.index, c.content)
                parent_ids[c.index] = db_id

            child_count = 0
            for c in chunks:
                if c.chunk_type != "child":
                    continue
                vec = embeddings.get(c.index)
                await conn.execute("""
                    INSERT INTO rag_chunks
                        (source_id, chunk_type, parent_id, chunk_index, content, embedding)
                    VALUES ($1, 'child', $2, $3, $4, $5)
                """, source_id, parent_ids.get(c.parent_index), c.index, c.content, vec)
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
) -> list[dict]:
    pool = await get_pool()
    params: list = [query_vec, top_k * 3]
    extras: list[str] = []
    if source_ids:
        params.append(source_ids)
        extras.append(f"AND c.source_id = ANY(${len(params)})")
    if kinds:
        params.append(kinds)
        extras.append(f"AND s.kind = ANY(${len(params)})")
    extra = (" " + " ".join(extras)) if extras else ""

    rows = await pool.fetch(f"""
        SELECT
            p.id          AS parent_id,
            p.content     AS context,
            s.name        AS source_name,
            s.id          AS source_id,
            s.kind        AS source_kind,
            c.content     AS child_content,
            1 - (c.embedding <=> $1) AS similarity
        FROM rag_chunks c
        JOIN rag_chunks  p ON p.id = c.parent_id
        JOIN rag_sources s ON s.id = c.source_id
        WHERE c.chunk_type = 'child' AND c.embedding IS NOT NULL{extra}
        ORDER BY c.embedding <=> $1
        LIMIT $2
    """, *params)

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
                "context":     r["context"],
                "child_content": r["child_content"],
                "similarity":  float(r["similarity"]),
            })
        if len(results) >= top_k:
            break
    return results
