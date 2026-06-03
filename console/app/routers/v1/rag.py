from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

# Import the current console runtime namespace, including private helper
# functions used by legacy handlers. Handlers are rebound to app.main's
# namespace before registration so existing tests and monkeypatches that
# patch app.main.<helper> continue to affect the handler at runtime.
globals().update(_console_main.__dict__)
router = APIRouter()


def _bind_to_main(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _console_main.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _console_main.__name__
    _console_main.__dict__[fn.__name__] = rebound
    return rebound

# /rag
@router.get("/rag", dependencies=[Depends(require_admin)])
@_bind_to_main
async def rag_page():
    # MEJORAS moved RAG operation into Studio step 7; keep /rag as a
    # compatibility entrypoint without serving the removed standalone page.
    return RedirectResponse(url="/studio")

# /api/rag/sources
@router.get("/api/rag/sources", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_rag_sources(kinds: str = "", user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_rag_headers_for_user(user), timeout=10) as c:
        params = {"kinds": kinds} if kinds else None
        r = await c.get(f"{_RAG_URL}/rag/sources", params=params)
        r.raise_for_status()
        return r.json()

# /api/rag/sources/{source_id}
@router.delete("/api/rag/sources/{source_id}", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
@_bind_to_main
async def api_rag_delete_source(source_id: int, user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_rag_headers_for_user(user), timeout=10) as c:
        r = await c.delete(f"{_RAG_URL}/rag/sources/{source_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Source not found")
        r.raise_for_status()
        return r.json()

# /api/rag/search
@router.post("/api/rag/search", dependencies=[Depends(require_csrf), Depends(require_authenticated)])
@_bind_to_main
async def api_rag_search(body: dict, user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/mcp/invoke",
            json=_mcp_payload(
                "search_rag",
                {
                    "query": body.get("query"),
                    "top_k": body.get("top_k", 5),
                    "source_ids": body.get("source_ids"),
                    "kinds": body.get("kinds"),
                },
                user,
            ),
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG search failed"))
        return r.json().get("result") or r.json()

# /api/rag/reindex
@router.post("/api/rag/reindex", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
@_bind_to_main
async def api_rag_reindex(body: dict, user: dict = Depends(require_authenticated)):
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/reindex", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG reindex failed"))
        return r.json()

# /api/rag/ingest
@router.post("/api/rag/ingest", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
@_bind_to_main
async def api_rag_ingest(body: dict, user: dict = Depends(require_authenticated)):
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/ingest", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG ingest failed"))
        return r.json()

# /api/rag/ask
@router.post("/api/rag/ask", dependencies=[Depends(require_csrf), Depends(require_authenticated)])
@_bind_to_main
async def api_rag_ask(body: dict, user: dict = Depends(require_authenticated)):
    """Retrieval-augmented answer: search top-K chunks, synthesize with the chat LLM."""
    from app.services import llm_client as _llm

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "Missing 'query'")
    top_k       = int(body.get("top_k") or 5)
    source_ids  = body.get("source_ids") or None
    kinds       = body.get("kinds") or None

    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/mcp/invoke",
            json=_mcp_payload(
                "search_rag",
                {"query": query, "top_k": top_k, "source_ids": source_ids, "kinds": kinds},
                user,
            ),
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG search failed"))
        results = ((r.json().get("result") or {}).get("results") or [])

    if not results:
        return {"answer": "No encontré información relacionada en las fuentes ingeridas.", "results": []}

    ctx_blocks = []
    for i, h in enumerate(results, start=1):
        src  = h.get("source_name") or "?"
        body_text = h.get("context") or h.get("child_content") or ""
        ctx_blocks.append(f"[{i}] Fuente: {src}\n{body_text}")
    context = "\n\n---\n\n".join(ctx_blocks)

    system = (
        "Eres un asistente que responde preguntas usando ÚNICAMENTE el contexto provisto. "
        "Si la respuesta no está en el contexto, di explícitamente que no la encuentras. "
        "Cita las fuentes usando el formato [n] al final de cada afirmación. "
        "Sé conciso y responde en el idioma de la pregunta."
    )
    user_msg = f"Contexto:\n\n{context}\n\nPregunta: {query}"

    try:
        _llm._ensure_provider_configured("anthropic")
        resp = await _llm._anthropic_client().messages.create(
            model=_llm._resolve_chat_model(None),
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        answer = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "").strip() or "(sin respuesta)"
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("LLM synthesis failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    return {"answer": answer, "results": results}
