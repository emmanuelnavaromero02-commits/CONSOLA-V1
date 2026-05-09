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
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg2
import yaml
from fastapi import FastAPI, Header, HTTPException, Depends
from app.security import get_internal_api_key

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


def _db_upsert(scope: str, cartridge: str, key: str, value: dict) -> None:
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vault_entries (scope, cartridge, key, value, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (scope, cartridge, key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (scope, cartridge, key, json.dumps(value)),
        )
    conn.commit()
    conn.close()


def _db_upsert_if_absent(scope: str, cartridge: str, key: str, value: dict) -> None:
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vault_entries (scope, cartridge, key, value, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (scope, cartridge, key) DO NOTHING
            """,
            (scope, cartridge, key, json.dumps(value)),
        )
    conn.commit()
    conn.close()


def _db_get(scope: str, cartridge: str, key: str) -> dict | None:
    conn = _pg()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM vault_entries WHERE scope=%s AND cartridge=%s AND key=%s",
            (scope, cartridge, key),
        )
        row = cur.fetchone()
    conn.close()
    return row[0] if row else None


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
            "SELECT key, value FROM vault_entries "
            "WHERE scope=%s AND cartridge=%s ORDER BY key",
            (scope, cartridge),
        )
        rows = cur.fetchall()
    conn.close()
    return [{"key": r[0], "value": r[1]} for r in rows]


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
INTERNAL_API_KEY = get_internal_api_key()
def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    # Validate the key and that the caller explicitly declares itself
    if not x_internal_service or x_internal_service not in ["console", "workspace", "refinement", "mcp-infra", "airflow"]:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key or not secrets.compare_digest(x_api_key, INTERNAL_API_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")

@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed()
    yield


app = FastAPI(title="MODecissions Vault", dependencies=[Depends(verify_api_key)], lifespan=lifespan)


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
async def put_connection(cartridge: str, conn_id: str, body: dict):
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
async def put_secret(scope: str, key: str, body: dict):
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
