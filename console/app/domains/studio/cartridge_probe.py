from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


async def probe_microservice(
    base_url: str,
    cartridge_id: str,
    *,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
    headers_factory: Callable[[], dict] | None = None,
) -> dict:
    try:
        async with http_client_factory(timeout=3) as client:
            response = await client.get(f"{base_url}/health")
    except (httpx.HTTPError, OSError) as exc:
        return {"status": "offline", "reason": f"/health unreachable: {exc!s}"}
    if response.status_code >= 500 or not response.is_success:
        return {"status": "offline", "reason": f"/health HTTP {response.status_code}"}

    try:
        headers = headers_factory() if headers_factory else {}
        async with http_client_factory(timeout=5, headers=headers) as client:
            deep = await client.get(f"{base_url}/health/{cartridge_id}")
        if deep.is_success:
            data = (
                deep.json()
                if "application/json" in deep.headers.get("content-type", "")
                else {}
            )
            return {"status": "operational", **(data or {})}
        try:
            payload = deep.json()
        except Exception:
            payload = {"detail": deep.text[:200]}
        return {"status": "degraded", "reason": payload}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"status": "degraded", "reason": f"deep health unavailable: {exc!s}"}
