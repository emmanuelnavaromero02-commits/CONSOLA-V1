from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from app.domains.data_platform.refinement_errors import (
    raise_for_refinement_payload_error,
)
from app.domains.data_platform.semantic_enrichment import (
    semantic_enrichment_candidates,
    semantic_enrichment_empty_response,
    semantic_enrichment_limit,
    semantic_enrichment_success_response,
)


async def semantic_enrich_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    scope_catalog_cartridge_arg: Callable[[dict[str, Any], Any], Awaitable[str | None]],
    refinement_invoke: Callable[..., Awaitable[Any]],
    scoped_read_cache_invalidate: Callable[..., Any],
) -> dict[str, Any]:
    cartridge = await scope_catalog_cartridge_arg(user, body.get("cartridge"))
    if not cartridge:
        raise HTTPException(404, "No active cartridge available for semantic enrichment")
    limit = semantic_enrichment_limit(body)

    catalog = await refinement_invoke(
        "get_data_catalog",
        {"cartridge": cartridge},
        timeout=45,
        user=user,
    )
    candidate_payload = semantic_enrichment_candidates(
        catalog,
        cartridge=cartridge,
        limit=limit,
    )
    entries = candidate_payload["entries"]
    if not entries:
        return semantic_enrichment_empty_response(
            cartridge=cartridge,
            candidate_payload=candidate_payload,
        )

    result = await refinement_invoke(
        "upsert_catalog_entries",
        {"entries": entries},
        timeout=45,
        user=user,
    )
    raise_for_refinement_payload_error(result, "Semantic enrichment failed")
    scoped_read_cache_invalidate("catalog", user)
    return semantic_enrichment_success_response(
        cartridge=cartridge,
        candidate_payload=candidate_payload,
        result=result,
    )
