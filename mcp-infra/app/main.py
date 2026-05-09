"""
MCP Infrastructure Server
=========================
Single FastAPI service that exposes tools for Airflow, MinIO, PostgreSQL and Superset
via the standard MCP contract:

  GET  /mcp/tools          → { tools: [{name, description, input_schema}] }
  POST /mcp/invoke         → { tool, args } → { result } | { error }
  GET  /health             → { status, tools }
"""
from __future__ import annotations
import os
import secrets

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel

from app import registry
from app.security import get_internal_api_key

# ── Import tool modules so decorators register themselves ──────────────────────
import app.tools.airflow     # noqa: F401
import app.tools.cartridges  # noqa: F401
import app.tools.minio       # noqa: F401
import app.tools.pipeline    # noqa: F401
import app.tools.postgres    # noqa: F401
import app.tools.rag         # noqa: F401
import app.tools.superset    # noqa: F401
import app.tools.vault       # noqa: F401

# ── App ────────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager
from app.rag.store import get_pool as _rag_get_pool


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        await _rag_get_pool()
    except Exception:
        pass  # RAG is optional — server starts even if pgvector is not ready
    yield

INTERNAL_API_KEY = get_internal_api_key()
def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    # Validate the key and that the caller explicitly declares itself
    if not x_internal_service or x_internal_service not in ["console", "workspace", "refinement", "mcp-infra", "airflow"]:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key or not secrets.compare_digest(x_api_key, INTERNAL_API_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")

app = FastAPI(
    title="MODecissions MCP Infra",
    description="MCP tools for Airflow, MinIO, PostgreSQL, Superset, RAG",
    version="1.1.0",
    lifespan=_lifespan,
)


class InvokeRequest(BaseModel):
    tool: str
    args: dict = {}


# ── MCP endpoints ──────────────────────────────────────────────────────────────

@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
def get_tools():
    return {"tools": registry.list_tools()}


@app.post("/mcp/invoke", dependencies=[Depends(verify_api_key)])
async def invoke_tool(req: InvokeRequest):
    try:
        result = await registry.invoke(req.tool, req.args)
        return {"result": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        # Return structured error so the LLM can reason about it
        return {"error": str(exc), "tool": req.tool}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    tools = registry.list_tools()
    return {"status": "ok", "tools": len(tools)}


# ── RAG REST endpoints (used by Studio UI) ─────────────────────────────────────

import io as _io
from app.rag.store import list_sources as _rag_list_sources, delete_source as _rag_delete_source
from app.tools.rag import _do_ingest as _rag_do_ingest, _do_search as _rag_do_search


@app.get("/rag/sources", dependencies=[Depends(verify_api_key)])
async def rag_rest_list_sources():
    return {"sources": await _rag_list_sources()}


@app.delete("/rag/sources/{source_id}", dependencies=[Depends(verify_api_key)])
async def rag_rest_delete_source(source_id: int):
    ok = await _rag_delete_source(source_id)
    if not ok:
        raise HTTPException(404, "Source not found")
    return {"deleted": True, "source_id": source_id}


@app.post("/rag/search", dependencies=[Depends(verify_api_key)])
async def rag_rest_search(body: dict):
    return {"results": await _rag_do_search(
        query=body["query"],
        top_k=body.get("top_k", 5),
        source_ids=body.get("source_ids"),
    )}


@app.post("/rag/ingest", dependencies=[Depends(verify_api_key)])
async def rag_rest_ingest(body: dict):
    content = body.get("content", "")
    if body.get("mime_type") == "application/pdf":
        import base64
        from pypdf import PdfReader
        pdf_bytes = base64.b64decode(body["content"])
        reader = PdfReader(_io.BytesIO(pdf_bytes))
        content = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if not content:
            raise HTTPException(400, "Could not extract text from PDF")
    return await _rag_do_ingest(
        name=body["name"],
        content=content,
        description=body.get("description", ""),
        mime_type=body.get("mime_type", "text/plain"),
    )
