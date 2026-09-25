from __future__ import annotations

import json
import hmac
import hashlib
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg2
import yaml
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from app.security import get_internal_api_key
from app.crypto import (
    VaultEncryptionError,
    _get_fernet,
    decrypt_value,
    encrypt_value,
)
from app.logging_config import setup_logging

setup_logging(service_name="vault")
logger = logging.getLogger("vault")

_SECRETS_FILE = Path("/vault/secrets.yaml")
_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "",
)

_SENSITIVE = {"token", "password", "secret", "api_key", "api_secret", "private_key"}
_ADMIN_ROLES = {"admin", "owner", "super_admin"}
_GLOBAL_SECRET_SCOPES = {"global", "platform", "studio", "system", "_system"}
_ALLOW_UNSCOPED_VAULT_CONNECTIONS_ENV = "ALLOW_UNSCOPED_VAULT_CONNECTIONS"
_SECURITY_CONTEXT_SIGNATURE_FIELD = "_signature"
_SECURITY_CONTEXT_SIGNED_AT_FIELD = "_signed_at"
_SECURITY_CONTEXT_SIGNATURE_VERSION_FIELD = "_signature_version"
_SECURITY_CONTEXT_SIGNATURE_VERSION = "hmac-sha256-v1"
_SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS = 300
_SECURITY_CONTEXT_SIGNATURE_FUTURE_SKEW_SECONDS = 30
_SECURITY_CONTEXT_MIN_SIGNING_KEY_LEN = 32


def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    ).replace(
        "postgres+psycopg2://", "postgres://"
    )


def _pg():
    return psycopg2.connect(_normalize_postgres_dsn(_DATABASE_URL))


def _security_context_signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _SECURITY_CONTEXT_MIN_SIGNING_KEY_LEN:
        raise ValueError("SECURITY_CONTEXT_SIGNING_KEY is required")
    for env_name, value in os.environ.items():
        if not (env_name == "INTERNAL_API_KEY" or env_name.startswith("INTERNAL_API_KEY_")):
            continue
        transport_key = (value or "").strip()
        if transport_key and hmac.compare_digest(key, transport_key):
            raise ValueError(f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {env_name}")
    return key


def _canonical_security_context(ctx: dict) -> bytes:
    payload = {key: value for key, value in ctx.items() if key != _SECURITY_CONTEXT_SIGNATURE_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _security_context_signature_valid(ctx: dict) -> bool:
    if not ctx.get("trusted"):
        return True
    try:
        key = _security_context_signing_key()
    except ValueError:
        return False
    signature = str(ctx.get(_SECURITY_CONTEXT_SIGNATURE_FIELD) or "")
    if not signature:
        return False
    if ctx.get(_SECURITY_CONTEXT_SIGNATURE_VERSION_FIELD) != _SECURITY_CONTEXT_SIGNATURE_VERSION:
        return False
    try:
        signed_at = int(ctx.get(_SECURITY_CONTEXT_SIGNED_AT_FIELD))
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if signed_at > now + _SECURITY_CONTEXT_SIGNATURE_FUTURE_SKEW_SECONDS:
        return False
    if now - signed_at > _SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS:
        return False
    expected = hmac.new(key.encode("utf-8"), _canonical_security_context(ctx), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _security_context_from_header(header_value: str | None) -> dict:
    if not header_value:
        return {}
    try:
        ctx = json.loads(header_value)
    except Exception:
        return {}
    if not isinstance(ctx, dict):
        return {}
    if not _security_context_signature_valid(ctx):
        if ctx.get("trusted"):
            raise HTTPException(403, "Invalid signed security_context")
        return {}
    return ctx


def _is_unscoped_admin_context(ctx: dict) -> bool:
    if not ctx.get("trusted"):
        return False
    if str(ctx.get("role") or "").lower() not in _ADMIN_ROLES:
        return False
    if ctx.get("tenant_id") or ctx.get("workspace_id"):
        return False
    return "*" in {str(item).strip() for item in (ctx.get("allowed_cartridges") or [])}


def _require_cartridge_scope(ctx: dict, cartridge: str, *, allow_platform_global: bool = True) -> None:
    if not ctx.get("trusted"):
        raise HTTPException(403, "vault access requires signed tenant/workspace context")
    if _is_unscoped_admin_context(ctx):
        if allow_platform_global:
            return
        raise HTTPException(403, "vault connection writes require tenant/workspace scope")
    _vault_scope(ctx)
    allowed = {str(item).strip() for item in (ctx.get("allowed_cartridges") or []) if str(item).strip()}
    if "*" not in allowed and cartridge not in allowed:
        raise HTTPException(403, "vault cartridge outside caller scope")


def _require_secret_scope(ctx: dict, scope: str) -> None:
    clean = str(scope or "").strip()
    if not clean:
        raise HTTPException(400, "vault scope is required")
    if clean in _GLOBAL_SECRET_SCOPES:
        if not _is_unscoped_admin_context(ctx):
            raise HTTPException(403, "global vault scope requires platform admin")
        return
    if _is_unscoped_admin_context(ctx):
        raise HTTPException(403, "workspace vault scope requires tenant/workspace context")
    if not ctx.get("trusted"):
        raise HTTPException(403, "vault access requires signed tenant/workspace context")
    _vault_scope(ctx)


def _vault_scope(ctx: dict, *, allow_unscoped: bool = False) -> tuple[str | None, str | None]:
    if _is_unscoped_admin_context(ctx):
        return None, None
    if allow_unscoped and not ctx.get("trusted"):
        return None, None
    if not ctx.get("trusted"):
        raise HTTPException(403, "vault access requires signed tenant/workspace context")
    tenant = str(ctx.get("tenant_id") or "").strip() or None
    workspace = str(ctx.get("workspace_id") or "").strip() or None
    if not tenant or not workspace:
        raise HTTPException(403, "vault access requires tenant/workspace scope")
    return tenant, workspace


def _set_db_scope(cur, tenant_id: str | None, workspace_id: str | None) -> None:
    if tenant_id and workspace_id:
        cur.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
            (tenant_id, workspace_id),
        )


def _runtime_unscoped_write_allowed(scope: str, cartridge: str) -> bool:
    return scope == "secrets" and cartridge in _GLOBAL_SECRET_SCOPES


def _require_runtime_write_scope(
    scope: str,
    cartridge: str,
    tenant_id: str | None,
    workspace_id: str | None,
    *,
    operation: str,
) -> None:
    if tenant_id and workspace_id:
        return
    if _runtime_unscoped_write_allowed(scope, cartridge):
        return
    raise HTTPException(
        403,
        (
            f"vault {operation} requires tenant/workspace scope; "
            "legacy unscoped destinations/platform rows are read-only and "
            "must be migrated explicitly"
        ),
    )


def _row_value(row_encrypted: bytes | memoryview | None, row_value_json) -> dict | None:
    if row_encrypted is not None:
        return decrypt_value(row_encrypted)
    if row_value_json is None:
        return None
    if isinstance(row_value_json, (dict, list)):
        return row_value_json
    return json.loads(row_value_json)


def _db_upsert(scope: str, cartridge: str, key: str, value: dict, ctx: dict | None = None, *, allow_unscoped: bool = False) -> None:
    tenant_id, workspace_id = _vault_scope(ctx or {}, allow_unscoped=allow_unscoped)
    _require_runtime_write_scope(scope, cartridge, tenant_id, workspace_id, operation="write")
    encrypted = encrypt_value(value)
    conn = _pg()
    with conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        if tenant_id and workspace_id:
            cur.execute(
                """
                INSERT INTO vault_entries (
                    tenant_id, workspace_id, scope, cartridge, key, value,
                    value_encrypted, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NULL, %s, NOW(), NOW())
                ON CONFLICT (tenant_id, workspace_id, scope, cartridge, key)
                WHERE tenant_id IS NOT NULL AND workspace_id IS NOT NULL
                DO UPDATE SET value_encrypted = EXCLUDED.value_encrypted,
                    value = NULL,
                    updated_at = NOW()
                """,
                (tenant_id, workspace_id, scope, cartridge, key, psycopg2.Binary(encrypted)),
            )
        else:
            cur.execute(
                """
                INSERT INTO vault_entries (scope, cartridge, key, value, value_encrypted, created_at, updated_at)
                VALUES (%s, %s, %s, NULL, %s, NOW(), NOW())
                ON CONFLICT (scope, cartridge, key)
                WHERE tenant_id IS NULL AND workspace_id IS NULL
                DO UPDATE SET value_encrypted = EXCLUDED.value_encrypted,
                    value = NULL,
                    updated_at = NOW()
                """,
                (scope, cartridge, key, psycopg2.Binary(encrypted)),
            )
    conn.commit()
    conn.close()


def _db_upsert_if_absent(scope: str, cartridge: str, key: str, value: dict) -> None:
    encrypted = encrypt_value(value)
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vault_entries (scope, cartridge, key, value, value_encrypted, created_at, updated_at)
            VALUES (%s, %s, %s, NULL, %s, NOW(), NOW())
            ON CONFLICT (scope, cartridge, key)
            WHERE tenant_id IS NULL AND workspace_id IS NULL
            DO NOTHING
            """,
            (scope, cartridge, key, psycopg2.Binary(encrypted)),
        )
    conn.commit()
    conn.close()


def _db_get(scope: str, cartridge: str, key: str, ctx: dict | None = None, *, allow_unscoped: bool = False) -> dict | None:
    tenant_id, workspace_id = _vault_scope(ctx or {}, allow_unscoped=allow_unscoped)
    conn = _pg()
    with conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        if tenant_id and workspace_id:
            cur.execute(
                "SELECT value_encrypted, value FROM vault_entries "
                "WHERE tenant_id=%s AND workspace_id=%s AND scope=%s AND cartridge=%s AND key=%s",
                (tenant_id, workspace_id, scope, cartridge, key),
            )
        else:
            cur.execute(
                "SELECT value_encrypted, value FROM vault_entries "
                "WHERE tenant_id IS NULL AND workspace_id IS NULL AND scope=%s AND cartridge=%s AND key=%s",
                (scope, cartridge, key),
            )
        row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return _row_value(row[0], row[1])


def _db_delete(scope: str, cartridge: str, key: str, ctx: dict | None = None, *, allow_unscoped: bool = False) -> bool:
    tenant_id, workspace_id = _vault_scope(ctx or {}, allow_unscoped=allow_unscoped)
    _require_runtime_write_scope(scope, cartridge, tenant_id, workspace_id, operation="delete")
    conn = _pg()
    with conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        if tenant_id and workspace_id:
            cur.execute(
                "DELETE FROM vault_entries WHERE tenant_id=%s AND workspace_id=%s AND scope=%s AND cartridge=%s AND key=%s",
                (tenant_id, workspace_id, scope, cartridge, key),
            )
        else:
            cur.execute(
                "DELETE FROM vault_entries WHERE tenant_id IS NULL AND workspace_id IS NULL AND scope=%s AND cartridge=%s AND key=%s",
                (scope, cartridge, key),
            )
        deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def _db_audit_access(caller_service: str | None, scope: str, key: str, op: str, ctx: dict | None = None) -> None:
    tenant_id, workspace_id = _vault_scope(ctx or {})
    conn = _pg()
    with conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        cur.execute(
            """
            INSERT INTO vault_access_log (caller_service, scope, key, op, tenant_id, workspace_id, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (caller_service or "unknown", scope, key, op, tenant_id, workspace_id),
        )
    conn.commit()
    conn.close()


def _db_list(scope: str, cartridge: str, ctx: dict | None = None, *, allow_unscoped: bool = False) -> list[dict]:
    tenant_id, workspace_id = _vault_scope(ctx or {}, allow_unscoped=allow_unscoped)
    conn = _pg()
    with conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        if tenant_id and workspace_id:
            cur.execute(
                "SELECT key, value_encrypted, value FROM vault_entries "
                "WHERE tenant_id=%s AND workspace_id=%s AND scope=%s AND cartridge=%s ORDER BY key",
                (tenant_id, workspace_id, scope, cartridge),
            )
        else:
            cur.execute(
                "SELECT key, value_encrypted, value FROM vault_entries "
                "WHERE tenant_id IS NULL AND workspace_id IS NULL AND scope=%s AND cartridge=%s ORDER BY key",
                (scope, cartridge),
            )
        rows = cur.fetchall()
    conn.close()
    out: list[dict] = []
    for r in rows:
        decoded = _row_value(r[1], r[2])
        if decoded is None:
            continue
        out.append({"key": r[0], "value": decoded})
    return out


def _migrate_legacy_plaintext_secrets() -> None:
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scope, cartridge, key, value FROM vault_entries "
                "WHERE value_encrypted IS NULL AND value IS NOT NULL"
            )
            rows = cur.fetchall()

        if not rows:
            return

        logger.info(
            "[v1.15] migrating %d legacy plaintext secrets to encrypted at rest",
            len(rows),
        )

        ok = fail = 0
        for scope, cartridge, key, value in rows:
            try:
                payload = value if isinstance(value, (dict, list)) else json.loads(value)
                encrypted = encrypt_value(payload if isinstance(payload, dict) else {"value": payload})
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE vault_entries "
                        "SET value_encrypted = %s, value = NULL, updated_at = NOW() "
                        "WHERE scope=%s AND cartridge=%s AND key=%s",
                        (psycopg2.Binary(encrypted), scope, cartridge, key),
                    )
                conn.commit()
                ok += 1
            except Exception as e:
                logger.error(
                    "[v1.15] failed migrating %s/%s/%s: %s", scope, cartridge, key, e,
                )
                conn.rollback()
                fail += 1

        logger.info(
            "[v1.15] migration complete: %d encrypted, %d failed (left on legacy column)",
            ok, fail,
        )
    finally:
        conn.close()


def _seed() -> None:
    if not _SECRETS_FILE.exists():
        return
    data = yaml.safe_load(_SECRETS_FILE.read_text(encoding="utf-8")) or {}

    for scope, keys in (data.get("secrets") or {}).items():
        if _is_production() and str(scope or "").strip() not in _GLOBAL_SECRET_SCOPES:
            logger.warning(
                "Skipping unscoped Vault secret seed for %s; use workspace-scoped Vault writes instead",
                scope,
            )
            continue
        for key, val in (keys or {}).items():
            _db_upsert_if_absent("secrets", scope, key, {"value": val})

    for name, config in (data.get("destinations") or {}).items():
        _db_upsert_if_absent("destinations", "platform", name, config or {})

    allow_unscoped_connections = (
        os.environ.get(_ALLOW_UNSCOPED_VAULT_CONNECTIONS_ENV, "").strip().lower()
        in {"1", "true", "yes", "on"}
        and not _is_production()
    )
    for cartridge_id, conns in (data.get("connections") or {}).items():
        if not allow_unscoped_connections:
            logger.warning(
                "Skipping unscoped Vault connection seed for %s; use workspace-scoped Vault writes instead",
                cartridge_id,
            )
            continue
        for conn_id, config in (conns or {}).items():
            _db_upsert_if_absent("connections", cartridge_id, conn_id, config or {})


def _mask(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SENSITIVE):
            out[k] = "***"
        else:
            out[k] = v
    return out


INTERNAL_API_KEY = get_internal_api_key()

_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "console":    "INTERNAL_API_KEY_CONSOLE_TO_VAULT",
    "mcp-infra":  "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT",
    "workspace":  "INTERNAL_API_KEY_WORKSPACE_TO_VAULT",
    "refinement": "INTERNAL_API_KEY_REFINEMENT_TO_VAULT",
    "airflow":    None,
}


_LEGACY_WARN_SEEN: set[str] = set()


_PUBLIC_PATHS = {"/healthz"}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").lower() in {"production", "prod"}


def _require_pair_keys_in_production() -> None:
    if not _is_production():
        return
    required = sorted({env for env in _ALLOWED_SERVICES_TO_KEY_ENV.values() if env})
    missing = [env for env in required if not os.environ.get(env)]
    if missing:
        raise RuntimeError(
            "Vault production startup refused: missing per-pair internal API key(s): "
            + ", ".join(missing)
        )


_require_pair_keys_in_production()


def verify_api_key(
    request: Request = None,
    x_api_key: str = Header(None),
    x_internal_service: str = Header(None),
):
    if request is not None and request.url.path in _PUBLIC_PATHS:
        return

    if not x_internal_service or x_internal_service not in _ALLOWED_SERVICES_TO_KEY_ENV:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    pair_key_env = _ALLOWED_SERVICES_TO_KEY_ENV.get(x_internal_service)
    pair_key = os.environ.get(pair_key_env) if pair_key_env else None

    if pair_key and secrets.compare_digest(x_api_key, pair_key):
        return
    if INTERNAL_API_KEY and not _is_production() and secrets.compare_digest(x_api_key, INTERNAL_API_KEY):
        if pair_key_env and x_internal_service not in _LEGACY_WARN_SEEN:
            _LEGACY_WARN_SEEN.add(x_internal_service)
            logger.warning(
                "vault accepted legacy INTERNAL_API_KEY from service %r — "
                "dedicated %s exists and should be used instead",
                x_internal_service, pair_key_env,
            )
        return

    raise HTTPException(status_code=403, detail="Forbidden")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _get_fernet()
    except VaultEncryptionError as e:
        raise RuntimeError(str(e)) from e
    _seed()
    _migrate_legacy_plaintext_secrets()
    yield


app = FastAPI(title="ΩMEGA Vault", dependencies=[Depends(verify_api_key)], lifespan=lifespan)

from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)


@app.get("/healthz")
def healthz():
    """Sprint v1.21 (F2): unauthenticated liveness probe for the compose
    healthcheck. The app-level verify_api_key dependency short-circuits
    on this exact path (see _PUBLIC_PATHS) so this endpoint returns 200
    without credentials. The legacy /health below stays behind auth."""
    return {"ok": True, "service": "vault"}


@app.get("/health")
def health():
    return {"status": "ok", "store": "postgresql"}


@app.post("/reload")
def reload():
    _seed()
    return {"reloaded": True, "note": "ON CONFLICT DO NOTHING — existing entries not overwritten"}


@app.get("/connections/{cartridge}")
def list_connections(cartridge: str, x_security_context: str | None = Header(None, alias="x-security-context")):
    ctx = _security_context_from_header(x_security_context)
    _require_cartridge_scope(ctx, cartridge)
    rows = _db_list("connections", cartridge, ctx)
    return {
        "connections": [
            {"conn_id": r["key"], **_mask(r["value"])} for r in rows
        ]
    }


@app.get("/connections/{cartridge}/{conn_id}")
def get_connection(
    cartridge: str,
    conn_id: str,
    x_internal_service: str | None = Header(None),
    x_security_context: str | None = Header(None, alias="x-security-context"),
):
    """Returns full credentials — called by DAGs internally, not exposed to users."""
    ctx = _security_context_from_header(x_security_context)
    _require_cartridge_scope(ctx, cartridge)
    value = _db_get("connections", cartridge, conn_id, ctx)
    if value is None:
        raise HTTPException(404, f"Connection '{cartridge}/{conn_id}' not found")
    _db_audit_access(x_internal_service, "connections", f"{cartridge}/{conn_id}", "read", ctx)
    return {"conn_id": conn_id, **value}


@app.put("/connections/{cartridge}/{conn_id}")
def put_connection(cartridge: str, conn_id: str, body: dict, x_security_context: str | None = Header(None, alias="x-security-context")):
    ctx = _security_context_from_header(x_security_context)
    _require_cartridge_scope(ctx, cartridge, allow_platform_global=False)
    _db_upsert("connections", cartridge, conn_id, body, ctx)
    return {"saved": True, "conn_id": conn_id}


@app.delete("/connections/{cartridge}/{conn_id}")
def delete_connection(cartridge: str, conn_id: str, x_security_context: str | None = Header(None, alias="x-security-context")):
    ctx = _security_context_from_header(x_security_context)
    _require_cartridge_scope(ctx, cartridge, allow_platform_global=False)
    if not _db_delete("connections", cartridge, conn_id, ctx):
        raise HTTPException(404, f"Connection '{cartridge}/{conn_id}' not found")
    return {"deleted": True}


@app.get("/secrets/{scope}")
def list_secret_keys(scope: str, x_security_context: str | None = Header(None, alias="x-security-context")):
    ctx = _security_context_from_header(x_security_context)
    _require_secret_scope(ctx, scope)
    rows = _db_list("secrets", scope, ctx)
    return {"keys": [r["key"] for r in rows]}


@app.get("/secrets/{scope}/{key}")
def get_secret(
    scope: str,
    key: str,
    x_internal_service: str | None = Header(None),
    x_security_context: str | None = Header(None, alias="x-security-context"),
):
    ctx = _security_context_from_header(x_security_context)
    _require_secret_scope(ctx, scope)
    row = _db_get("secrets", scope, key, ctx)
    if row is None:
        raise HTTPException(404, f"Secret '{scope}/{key}' not found")
    _db_audit_access(x_internal_service, scope, key, "read", ctx)
    return {"value": row.get("value", row)}


@app.put("/secrets/{scope}/{key}")
def put_secret(scope: str, key: str, body: dict, x_security_context: str | None = Header(None, alias="x-security-context")):
    ctx = _security_context_from_header(x_security_context)
    _require_secret_scope(ctx, scope)
    _db_upsert("secrets", scope, key, {"value": body.get("value", body)}, ctx)
    return {"saved": True}


@app.delete("/secrets/{scope}/{key}")
def delete_secret(scope: str, key: str, x_security_context: str | None = Header(None, alias="x-security-context")):
    ctx = _security_context_from_header(x_security_context)
    _require_secret_scope(ctx, scope)
    if not _db_delete("secrets", scope, key, ctx):
        raise HTTPException(404, f"Secret '{scope}/{key}' not found")
    return {"deleted": True}


@app.get("/destinations")
def list_destinations():
    rows = _db_list("destinations", "platform", allow_unscoped=True)
    return {"destinations": [r["key"] for r in rows]}


@app.get("/destinations/{name}")
def get_destination(name: str):
    config = _db_get("destinations", "platform", name, allow_unscoped=True)
    if config is None:
        raise HTTPException(404, f"Destination '{name}' not found")
    return {"config": config}
