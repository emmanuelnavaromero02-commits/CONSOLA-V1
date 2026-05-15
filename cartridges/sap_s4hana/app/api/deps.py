"""
Shared FastAPI dependencies — primarily the X-Internal-Api-Key check.

The function is defined here (not in main.py) so any router or test can import
it without triggering the FastAPI app construction.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.security import get_internal_api_key

_ALLOWED_INTERNAL_SERVICES = {
    "console",
    "workspace",
    "refinement",
    "mcp-infra",
    "airflow",
}


def verify_api_key(
    x_internal_api_key: str | None = Header(None, alias="X-Internal-Api-Key"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    x_internal_service: str | None = Header(None, alias="X-Internal-Service"),
) -> None:
    """Reject the request unless a valid internal API key is presented.

    Accepts either ``X-Internal-Api-Key`` (preferred) or ``X-Api-Key``
    (legacy). When ``X-Internal-Service`` is provided it must match the
    allow-list of service identifiers.
    """
    expected = get_internal_api_key()
    presented = x_internal_api_key or x_api_key

    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Internal-Api-Key",
        )

    if not x_internal_service or x_internal_service not in _ALLOWED_INTERNAL_SERVICES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Internal-Service",
        )
