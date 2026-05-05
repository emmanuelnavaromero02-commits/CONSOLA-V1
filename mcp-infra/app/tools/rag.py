"""
RAG MCP tools — semantic search + ingest over pgvector.
Migrated from the standalone rag service into mcp-infra.
"""
from __future__ import annotations

from app.registry import tool
from app.rag import config as rag_config
from app.rag.chunker import chunk_document
from app.rag.embeddings import embed_documents, embed_query
from app.rag.store import (
    list_sources as _list_sources,
    delete_source as _delete_source,
    ingest_chunks as _ingest_chunks,
    search as _search,
)


# ── Tool 1 · search_rag ───────────────────────────────────────────────────────

@tool(
    name="search_rag",
    description=(
        "Semantic search over the RAG knowledge base. Returns parent-context "
        "chunks ranked by ANN similarity. Use before answering questions about "
        "ingested documents or reports."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query":      {"type": "string", "description": "Question or search query"},
            "top_k":      {"type": "integer", "description": "Results to return (default 5)", "default": 5},
            "source_ids": {"type": "array", "items": {"type": "integer"},
                           "description": "Filter by source IDs (optional)"},
        },
        "required": ["query"],
    },
)
async def search_rag(query: str, top_k: int = 5, source_ids: list[int] | None = None) -> dict:
    query_vec = await embed_query(query)
    results = await _search(query_vec, top_k=top_k, source_ids=source_ids)
    return {"results": results}


# ── Tool 2 · list_rag_sources ─────────────────────────────────────────────────

@tool(
    name="list_rag_sources",
    description="List all documents currently ingested in the RAG knowledge base.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def list_rag_sources() -> dict:
    return {"sources": await _list_sources()}


# ── Tool 3 · ingest_document ──────────────────────────────────────────────────

@tool(
    name="ingest_document",
    description=(
        "Ingest a text document into the RAG knowledge base. "
        "Splits using parent-child chunking and writes embeddings to pgvector."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name":        {"type": "string", "description": "Unique document name"},
            "content":     {"type": "string", "description": "Full text content"},
            "description": {"type": "string", "description": "Optional description"},
        },
        "required": ["name", "content"],
    },
)
async def ingest_document(name: str, content: str, description: str = "") -> dict:
    return await _do_ingest(name=name, content=content, description=description, mime_type="text/plain")


# ── Helpers (also imported by REST endpoints in main.py) ─────────────────────

async def _do_ingest(name: str, content: str, description: str = "", mime_type: str = "text/plain") -> dict:
    chunks = chunk_document(
        content,
        parent_size=rag_config.PARENT_CHUNK_SIZE,
        child_size=rag_config.CHILD_CHUNK_SIZE,
        parent_overlap=rag_config.PARENT_OVERLAP,
        child_overlap=rag_config.CHILD_OVERLAP,
    )
    if not chunks:
        return {"error": "Empty content — nothing to ingest"}

    child_chunks = [c for c in chunks if c.chunk_type == "child"]
    vectors = await embed_documents([c.content for c in child_chunks])
    embeddings = {c.index: vec for c, vec in zip(child_chunks, vectors)}

    return await _ingest_chunks(
        source_name=name,
        source_desc=description,
        mime_type=mime_type,
        size_chars=len(content),
        chunks=chunks,
        embeddings=embeddings,
    )


async def _do_search(query: str, top_k: int = 5, source_ids: list[int] | None = None) -> list[dict]:
    query_vec = await embed_query(query)
    return await _search(query_vec, top_k=top_k, source_ids=source_ids)
