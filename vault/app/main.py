"""
MODecissions Vault — persistent secret & connection store (PostgreSQL-backed).

API:
  # Connections (cartridge API credentials)
  GET    /connections/{cartridge}           → [{ conn_id, base_url, auth_method, ... masked }]
  GET    /connections/{cartridge}/{conn_id} → { base_url, auth_method, token, ... }
  PUT    /connections/{cartridge}/{conn_id} → upsert  body: { base_url, auth_method, token, ... }
  DELETE /connections/{cartridge}/{conn_id} → delete

  # Secrets (generic key-value)
  GET    /secrets/{scope}        → { keys: [...] }
  GET    /secrets/{scope}/{key}  → { value }
  PUT    /secrets/{scope}/{key}  → { value }  — upsert
  DELETE /secrets/{scope}/{key}  → delete

  # Legacy compat
  GET    /destinations           → { destinations: [...] }
  GET    /destinations/{name}    → { config: {...} }

  GET    /health                 → { status, store }
  POST   /reload                 → re-seed from YAML (idempotent — ON CONFLICT DO NOTHING)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
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
# Sprint v1.18: structured JSON logs to stdout, with secret redaction.
# This MUST run before any logger.* call below so the first record this
# service emits is already in JSON format.
from app.logging_config import setup_logging

setup_logging(service_name="vault")
logger = logging.getLogger("vault")

_SECRETS_FILE = Path("/vault/secrets.yaml")
_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "",
)

_SENSITIVE = {"token", "password", "secret", "api_key", "api_secret"}


# ── PostgreSQL helpers ────────────────────────────────────────────────────────

def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    ).replace(
        "postgres+psycopg2://", "postgres://"
    )


def _pg():
    return psycopg2.connect(_normalize_postgres_dsn(_DATABASE_URL))


# Sprint v1.15: secrets are encrypted at rest in vault_entries.value_encrypted
# (BYTEA). The legacy `value` JSONB column is kept as nullable so reads can
# fall back to it for rows that haven't been re-encrypted yet by the runtime
# migration. Writes ALWAYS go to value_encrypted with value = NULL.

def _row_value(row_encrypted: bytes | memoryview | None, row_value_json) -> dict | None:
    """Return the decoded dict from whichever column has data.

    Prefers ``value_encrypted`` (the post-v1.15 path). Falls back to the
    legacy ``value`` JSONB column for rows that pre-existed the rollout
    and haven't been migrated yet. Returns ``None`` if both are NULL.
    """
    if row_encrypted is not None:
        return decrypt_value(row_encrypted)
    if row_value_json is None:
        return None
    if isinstance(row_value_json, (dict, list)):
        return row_value_json  # psycopg2 already decoded the JSONB
    return json.loads(row_value_json)


def _db_upsert(scope: str, cartridge: str, key: str, value: dict) -> None:
    encrypted = encrypt_value(value)
    conn = _pg()
    with conn.cursor() as cur:
        # Write only to value_encrypted; clear the legacy value so a future
        # rollback can't read stale plaintext that no longer matches.
        cur.execute(
            """
            INSERT INTO vault_entries (scope, cartridge, key, value, value_encrypted, created_at, updated_at)
            VALUES (%s, %s, %s, NULL, %s, NOW(), NOW())
            ON CONFLICT (scope, cartridge, key) DO UPDATE
            SET value_encrypted = EXCLUDED.value_encrypted,
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
            ON CONFLICT (scope, cartridge, key) DO NOTHING
            """,
            (scope, cartridge, key, psycopg2.Binary(encrypted)),
        )
    conn.commit()
    conn.close()


def _db_get(scope: str, cartridge: str, key: str) -> dict | None:
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value_encrypted, value FROM vault_entries "
            "WHERE scope=%s AND cartridge=%s AND key=%s",
            (scope, cartridge, key),
        )
        row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return _row_value(row[0], row[1])


def _db_delete(scope: str, cartridge: str, key: str) -> bool:
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM vault_entries WHERE scope=%s AND cartridge=%s AND key=%s",
            (scope, cartridge, key),
        )
        deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def _db_list(scope: str, cartridge: str) -> list[dict]:
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT key, value_encrypted, value FROM vault_entries "
            "WHERE scope=%s AND cartridge=%s ORDER BY key",
            (scope, cartridge),
        )
        rows = cur.fetchall()
    conn.close()
    out: list[dict] = []
    for r in rows:
        decoded = _row_value(r[1], r[2])
        if decoded is None:
            continue  # row with both columns NULL — shouldn't happen post-migration
        out.append({"key": r[0], "value": decoded})
    return out


def _migrate_legacy_plaintext_secrets() -> None:
    """One-time runtime migration: encrypt rows that still have plaintext
    ``value`` and no ``value_encrypted``. Idempotent — re-running after a
    successful migration is a no-op because the query returns nothing.

    Partial failures are isolated per row: a failure encrypting row A
    does not prevent row B from being encrypted. The legacy ``value`` is
    only cleared once the encrypted write has committed for that row, so
    a mid-migration crash leaves successful rows on the new path and
    failed rows on the old path — both still readable via _db_get's
    fallback. We never delete the legacy column data without a successful
    encrypted write.
    """
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


# ── Seed from secrets.yaml ────────────────────────────────────────────────────

def _seed() -> None:
    if not _SECRETS_FILE.exists():
        return
    data = yaml.safe_load(_SECRETS_FILE.read_text(encoding="utf-8")) or {}

    # secrets: { scope: { key: value } }
    for scope, keys in (data.get("secrets") or {}).items():
        for key, val in (keys or {}).items():
            _db_upsert_if_absent("secrets", scope, key, {"value": val})

    # destinations: { name: { host, port, ... } }
    for name, config in (data.get("destinations") or {}).items():
        _db_upsert_if_absent("destinations", "platform", name, config or {})

    # connections: { cartridge_id: { conn_id: { base_url, auth_method, ... } } }
    for cartridge_id, conns in (data.get("connections") or {}).items():
        for conn_id, config in (conns or {}).items():
            _db_upsert_if_absent("connections", cartridge_id, conn_id, config or {})


# ── Credential masking ────────────────────────────────────────────────────────

def _mask(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SENSITIVE):
            out[k] = "***"
        else:
            out[k] = v
    return out


# ── FastAPI app ───────────────────────────────────────────────────────────────
INTERNAL_API_KEY = get_internal_api_key()  # legacy fallback, still accepted

# Sprint v1.12: vault is called by console and mcp-infra. Each pair has its
# own INTERNAL_API_KEY_*_TO_VAULT secret. The legacy shared key still works
# during the migration window — it gets dropped in a follow-up sprint.
_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "console":    "INTERNAL_API_KEY_CONSOLE_TO_VAULT",
    "mcp-infra":  "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT",
    # Sprint v1.26 (audit F11): provisioned dedicated keys for workspace
    # and refinement. NEITHER service calls vault as of v1.26, but vault
    # accepted them via the legacy shared key — making any future
    # misrouted call invisible until something failed. With dedicated
    # keys in place, vault still accepts the legacy key (compat window)
    # but logs a WARNING the first time it falls back, so an operator
    # can spot the dependency.
    "workspace":  "INTERNAL_API_KEY_WORKSPACE_TO_VAULT",
    "refinement": "INTERNAL_API_KEY_REFINEMENT_TO_VAULT",
    "airflow":    None,  # airflow doesn't call vault directly — legacy-only
}


# Per-process throttle for the v1.26 "legacy key used by <svc>" warning.
# We want operator-visible signal without flooding the log if a tight
# loop hits the legacy path many times in one process.
_LEGACY_WARN_SEEN: set[str] = set()


_PUBLIC_PATHS = {"/healthz"}


def verify_api_key(
    request: Request = None,  # FastAPI injects; tests can call without
    x_api_key: str = Header(None),
    x_internal_service: str = Header(None),
):
    # Sprint v1.21 (F2): /healthz is the compose-probe liveness endpoint
    # and must answer 200 without credentials. The app-level
    # `dependencies=[Depends(verify_api_key)]` cascades to every route,
    # so the only way to make /healthz public is to short-circuit here.
    # The legacy /health endpoint stays behind auth — that one returns
    # {store: postgresql} which is mild fingerprinting and was already
    # gated. Request defaults to None so the v1.12 unit tests can call
    # verify_api_key directly without spinning up a FastAPI scope.
    if request is not None and request.url.path in _PUBLIC_PATHS:
        return

    if not x_internal_service or x_internal_service not in _ALLOWED_SERVICES_TO_KEY_ENV:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    pair_key_env = _ALLOWED_SERVICES_TO_KEY_ENV.get(x_internal_service)
    pair_key = os.environ.get(pair_key_env) if pair_key_env else None

    # Match in priority order so we can tell WHICH credential the caller
    # used. Order: dedicated pair key first, legacy shared key second.
    if pair_key and secrets.compare_digest(x_api_key, pair_key):
        return
    if INTERNAL_API_KEY and secrets.compare_digest(x_api_key, INTERNAL_API_KEY):
        # Sprint v1.26 (audit F11): warn when a service that HAS a
        # dedicated key is still using the legacy shared one. This is
        # the migration trail — operators grep for this line to find
        # callers that need to be rolled forward. Throttled per-process
        # so a tight loop doesn't flood the log.
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
    # Sprint v1.15: fail fast at startup if VAULT_ENCRYPTION_KEY is missing
    # or unparseable. The alternative — booting and silently 500'ing every
    # request — is much worse for the on-call.
    try:
        _get_fernet()
    except VaultEncryptionError as e:
        raise RuntimeError(str(e)) from e
    _seed()
    _migrate_legacy_plaintext_secrets()
    yield


app = FastAPI(title="MODecissions Vault", dependencies=[Depends(verify_api_key)], lifespan=lifespan)


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


# ── Connections ───────────────────────────────────────────────────────────────

@app.get("/connections/{cartridge}")
def list_connections(cartridge: str):
    rows = _db_list("connections", cartridge)
    return {
        "connections": [
            {"conn_id": r["key"], **_mask(r["value"])} for r in rows
        ]
    }


@app.get("/connections/{cartridge}/{conn_id}")
def get_connection(cartridge: str, conn_id: str):
    """Returns full credentials — called by DAGs internally, not exposed to users."""
    value = _db_get("connections", cartridge, conn_id)
    if value is None:
        raise HTTPException(404, f"Connection '{cartridge}/{conn_id}' not found")
    return {"conn_id": conn_id, **value}


@app.put("/connections/{cartridge}/{conn_id}")
def put_connection(cartridge: str, conn_id: str, body: dict):
    _db_upsert("connections", cartridge, conn_id, body)
    return {"saved": True, "conn_id": conn_id}


@app.delete("/connections/{cartridge}/{conn_id}")
def delete_connection(cartridge: str, conn_id: str):
    if not _db_delete("connections", cartridge, conn_id):
        raise HTTPException(404, f"Connection '{cartridge}/{conn_id}' not found")
    return {"deleted": True}


# ── Secrets ───────────────────────────────────────────────────────────────────

@app.get("/secrets/{scope}")
def list_secret_keys(scope: str):
    rows = _db_list("secrets", scope)
    return {"keys": [r["key"] for r in rows]}


@app.get("/secrets/{scope}/{key}")
def get_secret(scope: str, key: str):
    row = _db_get("secrets", scope, key)
    if row is None:
        raise HTTPException(404, f"Secret '{scope}/{key}' not found")
    return {"value": row.get("value", row)}


@app.put("/secrets/{scope}/{key}")
def put_secret(scope: str, key: str, body: dict):
    _db_upsert("secrets", scope, key, {"value": body.get("value", body)})
    return {"saved": True}


@app.delete("/secrets/{scope}/{key}")
def delete_secret(scope: str, key: str):
    if not _db_delete("secrets", scope, key):
        raise HTTPException(404, f"Secret '{scope}/{key}' not found")
    return {"deleted": True}


# ── Destinations (legacy compat) ──────────────────────────────────────────────

@app.get("/destinations")
def list_destinations():
    rows = _db_list("destinations", "platform")
    return {"destinations": [r["key"] for r in rows]}


@app.get("/destinations/{name}")
def get_destination(name: str):
    config = _db_get("destinations", "platform", name)
    if config is None:
        raise HTTPException(404, f"Destination '{name}' not found")
    return {"config": config}
