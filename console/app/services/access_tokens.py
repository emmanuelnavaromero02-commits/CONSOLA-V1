from __future__ import annotations

import hashlib
import re
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import auth as _auth


TOKEN_PREFIX = "omega_pat_"
TOKEN_BODY_LENGTH = 43
TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_RE = re.compile(rf"{TOKEN_PREFIX}[A-Za-z0-9]{{{TOKEN_BODY_LENGTH}}}")
DISPLAY_PREFIX_LENGTH = len(TOKEN_PREFIX) + 4
SCOPE_READ = "lectura"
SCOPE_ACTIONS = "acciones"
SCOPES = (SCOPE_READ, SCOPE_ACTIONS)
EXPIRY_DAYS = (7, 30, 90)
DEFAULT_EXPIRY_DAYS = 30
MAX_ACTIVE_TOKENS = 10
MAX_NAME_CHARS = 80
ACTION_SCOPE_PERMISSIONS = ("pipelines.run", "apps.write")
TOKEN_STATUSES = frozenset(
    {
        "activo",
        "vencido",
        "revocado",
        "usuario_inactivo",
        "cambio_de_contrasena",
        "espacio_inexistente",
    }
)

UNEXPIRED_LIST_STATUSES = frozenset({"activo", "sin_acceso"})

_SQLSTATE_ERRORS = {
    "22023": (400, "Los datos del token no son válidos."),
    "42501": (403, "No puedes crear tokens para este espacio de trabajo."),
    "53400": (
        409,
        f"Alcanzaste el máximo de {MAX_ACTIVE_TOKENS} tokens activos; revoca uno antes de crear otro.",
    ),
}


class AccessTokenError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class CreatedToken:
    secret: str
    token: dict[str, Any]

    def __repr__(self) -> str:
        return f"CreatedToken(token_prefix={self.token.get('token_prefix')!r})"


def generate_token() -> str:
    body = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_BODY_LENGTH))
    return f"{TOKEN_PREFIX}{body}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_token(value: object) -> bool:
    return isinstance(value, str) and TOKEN_RE.fullmatch(value) is not None


def has_token_prefix(value: object) -> bool:
    return isinstance(value, str) and value.startswith(TOKEN_PREFIX)


def display_prefix(token: str) -> str:
    return token[:DISPLAY_PREFIX_LENGTH]


def normalize_scopes(alcance: str) -> list[str]:
    if alcance == SCOPE_READ:
        return [SCOPE_READ]
    if alcance == SCOPE_ACTIONS:
        return [SCOPE_ACTIONS, SCOPE_READ]
    raise AccessTokenError(400, "El alcance debe ser «lectura» o «acciones».")


def allowed_scopes(permissions: set[str] | frozenset[str]) -> list[str]:
    if any(permission in permissions for permission in ACTION_SCOPE_PERMISSIONS):
        return [SCOPE_READ, SCOPE_ACTIONS]
    return [SCOPE_READ]


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


def _public_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": str(data.get("token_id") or ""),
        "nombre": str(data.get("token_name") or ""),
        "espacio_de_trabajo": {
            "id": str(data.get("workspace_id") or ""),
            "nombre": data.get("workspace_name"),
        },
        "token_prefix": str(data.get("token_prefix") or ""),
        "alcances": [str(item) for item in (data.get("scopes") or [])],
        "creado_en": _iso(data.get("created_at")),
        "vence_en": _iso(data.get("expires_at")),
        "ultimo_uso_en": _iso(data.get("last_used_at")),
        "revocado_en": _iso(data.get("revoked_at")),
        "motivo_revocacion": data.get("revoked_reason"),
        "estado": str(data.get("status") or "activo"),
    }


def _sqlstate_error(exc: Exception) -> AccessTokenError | None:
    state = str(getattr(exc, "sqlstate", "") or "")
    mapped = _SQLSTATE_ERRORS.get(state)
    if mapped is None:
        return None
    return AccessTokenError(*mapped)


async def create_token(
    *,
    user_id: int,
    workspace_id: str,
    workspace_name: str | None,
    name: str,
    scopes: list[str],
    days: int,
) -> CreatedToken:
    clean_name = " ".join(str(name or "").split())
    if not 1 <= len(clean_name) <= MAX_NAME_CHARS:
        raise AccessTokenError(400, "El nombre debe tener entre 1 y 80 caracteres.")
    if days not in EXPIRY_DAYS:
        raise AccessTokenError(400, "La vigencia debe ser de 7, 30 o 90 días.")
    try:
        workspace_uuid = uuid.UUID(str(workspace_id))
    except (TypeError, ValueError) as exc:
        raise AccessTokenError(403, "Se requiere un espacio de trabajo activo.") from exc
    secret = generate_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    pool = await _auth.pool()
    try:
        row = await pool.fetchrow(
            "SELECT * FROM omega_auth_create_access_token($1, $2, $3, $4, $5, $6, $7)",
            int(user_id),
            workspace_uuid,
            clean_name,
            hash_token(secret),
            display_prefix(secret),
            list(scopes),
            expires_at,
        )
    except Exception as exc:
        mapped = _sqlstate_error(exc)
        if mapped is not None:
            raise mapped from None
        raise
    if row is None:
        raise AccessTokenError(503, "No se pudo crear el token.")
    token = _public_row({**dict(row), "workspace_name": workspace_name, "status": "activo"})
    return CreatedToken(secret=secret, token=token)


async def list_tokens(user_id: int) -> list[dict[str, Any]]:
    pool = await _auth.pool()
    rows = await pool.fetch("SELECT * FROM omega_auth_list_access_tokens($1)", int(user_id))
    return [_public_row(row) for row in rows]


async def revoke_token(user_id: int, token_id: str) -> dict[str, Any] | None:
    try:
        token_uuid = uuid.UUID(str(token_id))
    except (TypeError, ValueError):
        return None
    pool = await _auth.pool()
    row = await pool.fetchrow(
        "SELECT * FROM omega_auth_revoke_access_token($1, $2)", int(user_id), token_uuid
    )
    # A legacy boolean revoke function yields a row without token_id; never report that as revoked.
    if row is None or not dict(row).get("token_id"):
        return None
    data = dict(row)
    return {
        "id": str(data.get("token_id") or ""),
        "token_prefix": str(data.get("token_prefix") or ""),
        "alcances": [str(item) for item in (data.get("scopes") or [])],
        "espacio_de_trabajo": {"id": str(data.get("workspace_id") or "")},
        "vence_en": _iso(data.get("expires_at")),
    }


async def resolve_token(token: str, ip: str | None) -> dict[str, Any] | None:
    if not looks_like_token(token):
        return None
    pool = await _auth.pool()
    row = await pool.fetchrow(
        "SELECT * FROM omega_auth_resolve_access_token($1, $2)",
        hash_token(token),
        (str(ip)[:64] if ip else None),
    )
    if row is None:
        return None
    data = dict(row)
    status = str(data.get("status") or "")
    if status not in TOKEN_STATUSES:
        return None
    user_tenant = data.get("user_tenant_id")
    return {
        "token_id": str(data.get("token_id") or ""),
        "status": status,
        "user_id": data.get("user_id"),
        "email": data.get("email"),
        "name": data.get("user_name"),
        "role": data.get("role"),
        "user_tenant_id": str(user_tenant) if user_tenant else None,
        "tenant_id": str(data.get("tenant_id") or ""),
        "workspace_id": str(data.get("workspace_id") or ""),
        "workspace_name": data.get("workspace_name"),
        "token_name": data.get("token_name"),
        "token_prefix": data.get("token_prefix"),
        "scopes": [str(item) for item in (data.get("scopes") or [])],
        "expires_at": _iso(data.get("expires_at")),
    }


__all__ = (
    "ACTION_SCOPE_PERMISSIONS",
    "AccessTokenError",
    "CreatedToken",
    "DEFAULT_EXPIRY_DAYS",
    "EXPIRY_DAYS",
    "MAX_ACTIVE_TOKENS",
    "SCOPES",
    "SCOPE_ACTIONS",
    "SCOPE_READ",
    "TOKEN_PREFIX",
    "TOKEN_RE",
    "UNEXPIRED_LIST_STATUSES",
    "allowed_scopes",
    "create_token",
    "display_prefix",
    "generate_token",
    "has_token_prefix",
    "hash_token",
    "list_tokens",
    "looks_like_token",
    "normalize_scopes",
    "resolve_token",
    "revoke_token",
)
