"""Transport wrapper for Refinement MCP calls."""

from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import HTTPException


async def refinement_invoke(
    tool: str,
    args: dict,
    *,
    timeout: int = 30,
    user: dict | None = None,
    httpx_module: Any,
    hdr_for: Callable[[str], dict[str, str]],
    mcp_payload: Callable[[str, dict, dict | None], dict],
    upstream_error_detail: Callable[[Any, str], Any],
    raise_for_refinement_payload_error: Callable[[Any, str], None],
) -> Any:
    refinement_url = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
    try:
        async with httpx_module.AsyncClient(
            headers=hdr_for("REFINEMENT"), timeout=timeout
        ) as client:
            response = await client.post(
                f"{refinement_url}/mcp/invoke",
                json=mcp_payload(tool, args, user),
            )
            if response.status_code >= 400:
                raise HTTPException(
                    response.status_code,
                    upstream_error_detail(response, "Refinement request failed"),
                )
            payload = response.json()
            raise_for_refinement_payload_error(payload, "Refinement request failed")
            return payload
    except httpx_module.TimeoutException as exc:
        raise HTTPException(503, f"Refinement timed out while running {tool}") from exc
    except httpx_module.TransportError as exc:
        raise HTTPException(
            503, f"Refinement unavailable while running {tool}: {type(exc).__name__}"
        ) from exc
