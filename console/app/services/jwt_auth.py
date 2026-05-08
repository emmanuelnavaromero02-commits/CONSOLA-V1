from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import ExpiredSignatureError, JWTError, jwt


DEFAULT_JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 15


class JWTAuthError(ValueError):
    """Raised when an access token cannot be decoded safely."""


@dataclass(frozen=True)
class JWTSettings:
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int


def get_jwt_settings() -> JWTSettings:
    secret_key = os.environ.get("JWT_SECRET_KEY", "")
    if _is_insecure_secret(secret_key):
        raise RuntimeError("JWT_SECRET_KEY missing or using an insecure default. System halted for security.")

    algorithm = os.environ.get("JWT_ALGORITHM", DEFAULT_JWT_ALGORITHM).strip() or DEFAULT_JWT_ALGORITHM
    expire_raw = os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", str(DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES))
    try:
        expire_minutes = int(expire_raw)
    except ValueError as exc:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be an integer.") from exc
    if expire_minutes <= 0:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be greater than zero.")

    return JWTSettings(
        secret_key=secret_key,
        algorithm=algorithm,
        access_token_expire_minutes=expire_minutes,
    )


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    _validate_minimum_claim_input(data)
    settings = get_jwt_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    claims = {
        **data,
        "sub": str(data["sub"]),
        "iat": int(now.timestamp()),
        "exp": expires_at,
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_jwt_settings()
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except ExpiredSignatureError as exc:
        raise JWTAuthError("access token expired") from exc
    except JWTError as exc:
        raise JWTAuthError("access token invalid") from exc

    _validate_minimum_claim_output(claims)
    return claims


def _is_insecure_secret(secret_key: str) -> bool:
    if not secret_key or len(secret_key.strip()) < 32:
        return True
    lowered = secret_key.strip().lower()
    insecure_values = {
        "secret",
        "jwt-secret",
        "jwt_secret",
        "dev-secret-key",
        "change-me",
        "changeme",
    }
    if any(secrets.compare_digest(lowered, value) for value in insecure_values):
        return True
    return lowered.startswith("changeme")


def _validate_minimum_claim_input(data: dict) -> None:
    missing = [claim for claim in ("sub", "email", "role") if not data.get(claim)]
    if missing:
        raise ValueError(f"missing required access token claim input: {', '.join(missing)}")


def _validate_minimum_claim_output(claims: dict) -> None:
    missing = [claim for claim in ("sub", "email", "role", "iat", "exp", "jti") if claim not in claims]
    if missing:
        raise JWTAuthError(f"access token missing required claim: {', '.join(missing)}")
