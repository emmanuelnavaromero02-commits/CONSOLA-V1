"""Request handlers for RAG-facing console routes."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


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
        response = await llm_client._anthropic_client().messages.create(
            model=llm_client._resolve_chat_model(None),
            max_tokens=1024,
            system=rag_messages["system"],
            messages=[{"role": "user", "content": rag_messages["user"]}],
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
