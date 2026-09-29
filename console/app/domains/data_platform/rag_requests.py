from __future__ import annotations

import json
import time
from typing import Any, Callable

from fastapi import HTTPException, Request

from app.services import token_store

RAG_INGEST_MAX_BODY_BYTES = 15 * 1024 * 1024


def _declared_content_length(request: Request) -> int | None:
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        return None
    try:
        declared = int(raw_length)
    except ValueError as exc:
        raise HTTPException(400, "Invalid Content-Length") from exc
    if declared < 0:
        raise HTTPException(400, "Invalid Content-Length")
    return declared


def require_rag_ingest_size(request: Request) -> None:
    """Runs before the CSRF body fallback so no unbounded buffering path remains."""
    declared = _declared_content_length(request)
    if declared is None:
        raise HTTPException(411, "Content-Length required")
    if declared > RAG_INGEST_MAX_BODY_BYTES:
        raise HTTPException(413, "Request body exceeds size limit")


async def read_json_body_capped(request: Request, max_bytes: int) -> dict[str, Any]:
    declared = _declared_content_length(request)
    if declared is not None and declared > max_bytes:
        raise HTTPException(413, "Request body exceeds size limit")
    received = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(413, "Request body exceeds size limit")
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks) or b"{}")
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON body")
    return body


async def rag_answer_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    rag_url: str,
    http_client_factory: Callable[..., Any],
    headers_factory: Callable[[str], dict[str, str]],
    mcp_payload_factory: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
    upstream_error_detail: Callable[[Any, str], Any],
    llm_client: Any,
    uuid_factory: Callable[[], Any],
    logger_exception: Callable[..., None],
    rag_search_arguments: Callable[[dict[str, Any]], dict[str, Any]],
    rag_empty_answer: Callable[[], dict[str, Any]],
    rag_synthesis_messages: Callable[[str, list[dict[str, Any]]], dict[str, str]],
) -> dict[str, Any]:
    search_args = rag_search_arguments(body)
    query = search_args["query"]
    if not query:
        raise HTTPException(400, "Missing 'query'")

    async with http_client_factory(headers=headers_factory("MCP_INFRA"), timeout=60) as c:
        response = await c.post(
            f"{rag_url}/mcp/invoke",
            json=mcp_payload_factory("search_rag", search_args, user),
        )
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                upstream_error_detail(response, "RAG search failed"),
            )
        results = (response.json().get("result") or {}).get("results") or []

    if not results:
        return rag_empty_answer()

    rag_messages = rag_synthesis_messages(query, results)

    try:
        llm_client._ensure_provider_configured("anthropic")
        model = llm_client._resolve_chat_model(None)
        started = time.perf_counter()
        response = await llm_client._anthropic_client().messages.create(
            model=model,
            max_tokens=1024,
            system=rag_messages["system"],
            messages=[{"role": "user", "content": rag_messages["user"]}],
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        if usage is not None:
            await token_store.record(
                "anthropic", model,
                int(getattr(usage, "input_tokens", 0) or 0),
                int(getattr(usage, "output_tokens", 0) or 0),
                int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                user_context=user,
                duration_ms=duration_ms,
                surface="rag",
            )
        answer = (
            next(
                (
                    block.text
                    for block in response.content
                    if getattr(block, "type", "") == "text"
                ),
                "",
            ).strip()
            or "(sin respuesta)"
        )
    except Exception as exc:
        error_id = uuid_factory().hex
        logger_exception("LLM synthesis failed error_id=%s", error_id)
        raise HTTPException(
            500, f"Internal server error. error_id={error_id}"
        ) from exc

    return {"answer": answer, "results": results}


async def rag_sources_payload(
    *,
    kinds: str,
    user: dict[str, Any],
    rag_url: str,
    http_client_factory: Callable[..., Any],
    headers_for_user: Callable[[dict[str, Any]], dict[str, str]],
) -> dict[str, Any]:
    async with http_client_factory(headers=headers_for_user(user), timeout=10) as client:
        params = {"kinds": kinds} if kinds else None
        response = await client.get(f"{rag_url}/rag/sources", params=params)
        response.raise_for_status()
        return response.json()


async def rag_delete_source_payload(
    *,
    source_id: int,
    user: dict[str, Any],
    rag_url: str,
    http_client_factory: Callable[..., Any],
    headers_for_user: Callable[[dict[str, Any]], dict[str, str]],
) -> dict[str, Any]:
    async with http_client_factory(headers=headers_for_user(user), timeout=10) as client:
        response = await client.delete(f"{rag_url}/rag/sources/{source_id}")
        if response.status_code == 404:
            raise HTTPException(404, "Source not found")
        response.raise_for_status()
        return response.json()


async def rag_search_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    rag_url: str,
    http_client_factory: Callable[..., Any],
    headers_factory: Callable[[str], dict[str, str]],
    mcp_payload_factory: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
    upstream_error_detail: Callable[[Any, str], Any],
) -> dict[str, Any]:
    async with http_client_factory(
        headers=headers_factory("MCP_INFRA"), timeout=60
    ) as client:
        response = await client.post(
            f"{rag_url}/mcp/invoke",
            json=mcp_payload_factory(
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
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                upstream_error_detail(response, "RAG search failed"),
            )
        payload = response.json()
        return payload.get("result") or payload


async def rag_reindex_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    rag_url: str,
    http_client_factory: Callable[..., Any],
    headers_factory: Callable[[str], dict[str, str]],
    upstream_error_detail: Callable[[Any, str], Any],
    refinement_invoke: Callable[..., Any],
    require_cartridge_visible: Callable[[dict[str, Any], str], None],
    build_security_context: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(body or {})
    kind = str(payload.get("kind") or "").strip().lower()
    requested_cartridge = str(payload.get("cartridge") or "").strip()
    if requested_cartridge:
        require_cartridge_visible(user, requested_cartridge)
    if kind == "dataset" and requested_cartridge:
        dataset_name = str(payload.get("name") or "").strip()
        datasets_payload = await refinement_invoke("list_datasets", {}, user=user)
        datasets = (
            datasets_payload.get("datasets")
            if isinstance(datasets_payload, dict)
            else []
        )
        match = next(
            (
                dataset
                for dataset in (datasets or [])
                if str(dataset.get("name") or "") == dataset_name
                and str(dataset.get("cartridge") or "") == requested_cartridge
            ),
            None,
        )
        if not match:
            raise HTTPException(
                404,
                f"dataset '{dataset_name}' not found for cartridge '{requested_cartridge}'",
            )
    payload = {**payload, "security_context": build_security_context(user)}
    async with http_client_factory(
        headers=headers_factory("MCP_INFRA"), timeout=300
    ) as client:
        response = await client.post(f"{rag_url}/rag/reindex", json=payload)
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                upstream_error_detail(response, "RAG reindex failed"),
            )
        return response.json()


async def rag_ingest_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    rag_url: str,
    http_client_factory: Callable[..., Any],
    headers_factory: Callable[[str], dict[str, str]],
    upstream_error_detail: Callable[[Any, str], Any],
    build_security_context: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    payload = {**dict(body or {}), "security_context": build_security_context(user)}
    async with http_client_factory(
        headers=headers_factory("MCP_INFRA"), timeout=300
    ) as client:
        response = await client.post(f"{rag_url}/rag/ingest", json=payload)
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                upstream_error_detail(response, "RAG ingest failed"),
            )
        return response.json()
