from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.security import is_valid_internal_request


def verify_api_key(
    x_internal_api_key: str | None = Header(None, alias="X-Internal-Api-Key"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    x_internal_service: str | None = Header(None, alias="X-Internal-Service"),
) -> None:
    presented = x_internal_api_key or x_api_key
    if not is_valid_internal_request(presented, x_internal_service):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Internal-Api-Key",
        )
