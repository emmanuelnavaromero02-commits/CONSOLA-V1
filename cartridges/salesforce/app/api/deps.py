from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status

from app.security import ALLOWED_INTERNAL_SERVICES, get_internal_api_key

_ALLOWED_INTERNAL_SERVICES = ALLOWED_INTERNAL_SERVICES


def verify_api_key(
    x_internal_api_key: str | None = Header(None, alias="X-Internal-Api-Key"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    x_internal_service: str | None = Header(None, alias="X-Internal-Service"),
) -> None:
    presented = x_internal_api_key or x_api_key
    accepted = []
    if x_internal_service == "console":
        pair = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE")
        if pair:
            accepted.append(pair)
    if x_internal_service == "airflow":
        pair = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE")
        if pair:
            accepted.append(pair)
    if os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}:
        accepted.append(get_internal_api_key())

    if not presented or not any(secrets.compare_digest(presented, k) for k in accepted if k):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Internal-Api-Key",
        )

    if not x_internal_service or x_internal_service not in _ALLOWED_INTERNAL_SERVICES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Internal-Service",
        )
