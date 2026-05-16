"""
MCP Infrastructure Server
=========================
Single FastAPI service that exposes tools for Airflow, MinIO, PostgreSQL and Superset
via the standard MCP contract:

  GET  /mcp/tools          → { tools: [{name, description, input_schema}] }
  POST /mcp/invoke         → { tool, args } → { result } | { error }
  GET  /health             → { status }   (public; tool count intentionally redacted)
"""
from __future__ import annotations
import os
import secrets

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel

from app import registry
from app.security import get_internal_api_key
# Sprint v1.41.1 — structured JSON logs so request_id correlates here too.
from app.logging_config import setup_logging  # noqa: E402

setup_logging(service_name="mcp-infra")

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

INTERNAL_API_KEY = get_internal_api_key()  # legacy fallback, still accepted

# Sprint v1.12: mcp-infra is called by console, workspace and airflow. Each
# pair has its own INTERNAL_API_KEY_*_TO_MCP_INFRA secret. The legacy shared
# key keeps working during the migration window and is dropped in a follow-up.
_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "console":   "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
    "workspace": "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
    "airflow":   "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
    # The old whitelist allowed these; we keep them via legacy key only.
    "refinement": None,
    "mcp-infra":  None,
}


def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    if not x_internal_service or x_internal_service not in _ALLOWED_SERVICES_TO_KEY_ENV:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    accepted: list[str] = []
    pair_key_env = _ALLOWED_SERVICES_TO_KEY_ENV.get(x_internal_service)
    if pair_key_env:
        pair_key = os.environ.get(pair_key_env)
        if pair_key:
            accepted.append(pair_key)
    if INTERNAL_API_KEY:
        accepted.append(INTERNAL_API_KEY)

    if not any(secrets.compare_digest(x_api_key, k) for k in accepted if k):
        raise HTTPException(status_code=403, detail="Forbidden")

app = FastAPI(
    title="MODecissions MCP Infra",
    description="MCP tools for Airflow, MinIO, PostgreSQL, Superset, RAG",
    version="1.1.0",
    lifespan=_lifespan,
)

# Sprint v1.41.1 — correlation IDs.
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)


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

@app.get("/healthz")
def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    Same contract as the legacy /health below — minimal payload, no
    auth — but uses the /healthz path the rest of the platform pins
    its healthchecks on."""
    return {"ok": True, "service": "mcp-infra"}


@app.get("/health")
def health():
    # Public endpoint used by Docker healthchecks and load balancers — keep
    # the response minimal so unauthenticated callers can't fingerprint how
    # many tools / integrations this instance has loaded. Internal callers
    # that need that detail use GET /mcp/tools behind x-api-key.
    return {"status": "ok"}


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
