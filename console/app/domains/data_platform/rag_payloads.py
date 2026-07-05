"""Pure payload helpers for RAG-facing console routes."""

from __future__ import annotations

from typing import Any


def rag_search_arguments(body: dict[str, Any], *, default_top_k: int = 5) -> dict[str, Any]:
    return {
        "query": str((body or {}).get("query") or "").strip(),
        "top_k": int((body or {}).get("top_k") or default_top_k),
        "source_ids": (body or {}).get("source_ids") or None,
        "kinds": (body or {}).get("kinds") or None,
    }


def rag_empty_answer() -> dict[str, Any]:
    return {
        "answer": "No encontré información relacionada en las fuentes ingeridas.",
        "results": [],
    }


def rag_context_from_results(results: list[dict[str, Any]]) -> str:
    ctx_blocks: list[str] = []
    for i, hit in enumerate(results, start=1):
        source = hit.get("source_name") or "?"
        body_text = hit.get("context") or hit.get("child_content") or ""
        ctx_blocks.append(f"[{i}] Fuente: {source}\n{body_text}")
    return "\n\n---\n\n".join(ctx_blocks)


def rag_synthesis_messages(
    query: str, results: list[dict[str, Any]]
) -> dict[str, str]:
    context = rag_context_from_results(results)
    system = (
        "Eres un asistente que responde preguntas usando ÚNICAMENTE el contexto provisto. "
        "Si la respuesta no está en el contexto, di explícitamente que no la encuentras. "
        "Cita las fuentes usando el formato [n] al final de cada afirmación. "
        "Sé conciso y responde en el idioma de la pregunta."
    )
    user = f"Contexto:\n\n{context}\n\nPregunta: {query}"
    return {"system": system, "user": user}
