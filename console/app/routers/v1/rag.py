from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

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

@router.get("/rag", dependencies=[Depends(require_admin)])
@_bind_to_main
async def rag_page():
    return RedirectResponse(url="/studio")

@router.get("/api/rag/sources", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_rag_sources(kinds: str = "", user: dict = Depends(require_permission("datasets.read"))):
    return await _rag_sources_payload_impl(
        kinds=kinds,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_for_user=_rag_headers_for_user,
    )

@router.delete(
    "/api/rag/sources/{source_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
@_bind_to_main
async def api_rag_delete_source(source_id: int, user: dict = Depends(require_permission("datasets.write"))):
    return await _rag_delete_source_payload_impl(
        source_id=source_id,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_for_user=_rag_headers_for_user,
    )

@router.post("/api/rag/search", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_rag_search(body: dict, user: dict = Depends(require_permission("datasets.read"))):
    return await _rag_search_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload_factory=_mcp_payload,
        upstream_error_detail=_upstream_error_detail,
    )

@router.post(
    "/api/rag/reindex",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
@_bind_to_main
async def api_rag_reindex(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/reindex", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG reindex failed"))
        return r.json()

@router.post(
    "/api/rag/ingest",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
@_bind_to_main
async def api_rag_ingest(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    return await _rag_ingest_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        upstream_error_detail=_upstream_error_detail,
        build_security_context=build_security_context,
    )

@router.post("/api/rag/ask", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_rag_ask(body: dict, user: dict = Depends(require_permission("datasets.read"))):
    """Retrieval-augmented answer: search top-K chunks, synthesize with the chat LLM."""
    from app.services import llm_client as _llm

    return await _rag_answer_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload_factory=_mcp_payload,
        upstream_error_detail=_upstream_error_detail,
        llm_client=_llm,
        uuid_factory=uuid.uuid4,
        logger_exception=logger.exception,
        rag_search_arguments=_rag_search_arguments,
        rag_empty_answer=_rag_empty_answer,
        rag_synthesis_messages=_rag_synthesis_messages,
    )
