import hashlib
import hmac
import json

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from app.dependencies import require_admin
from app.services import auth as _auth

router = APIRouter(prefix="/security", tags=["Security Center"])


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _table_exists(conn, table_name: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}"))


async def _columns(conn, table_name: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = $1
        """,
        table_name,
    )
    return {row["column_name"] for row in rows}


def _select_column(
    columns: set[str],
    name: str,
    fallback: str,
    alias: str | None = None,
    table_alias: str = "s",
) -> str:
    target = alias or name
    if name in columns:
        return f"{table_alias}.{name} AS {target}"
    return f"{fallback} AS {target}"


@router.get("/sessions")
async def get_sessions(user: dict = Depends(require_admin)):
    p = await _auth.pool()
    if not await _table_exists(p, "user_sessions"):
        return []
    session_columns = await _columns(p, "user_sessions")
    select_parts = [
        "s.token",
        "s.user_id",
        "u.email AS user_email",
        _select_column(session_columns, "ip", "NULL::text"),
        _select_column(session_columns, "last_seen", "NULL::timestamptz"),
        _select_column(session_columns, "user_agent", "NULL::text"),
        _select_column(session_columns, "created_at", "NULL::timestamptz"),
        _select_column(session_columns, "expires_at", "NULL::timestamptz"),
    ]
    order_expr = "s.last_seen DESC NULLS LAST" if "last_seen" in session_columns else "s.created_at DESC NULLS LAST"
    rows = await p.fetch(
        f"""SELECT {", ".join(select_parts)}
           FROM user_sessions s
           JOIN users u ON u.id = s.user_id
           ORDER BY {order_expr}"""
    )
    res = []
    for r in rows:
        d = dict(r)
        token = d.pop("token")
        d["session_id"] = _session_id(token)
        d["token_preview"] = token[:8] + "..." if token and len(token) > 8 else "***"
        res.append(d)
    return res

@router.delete("/sessions/{token}")
async def revoke_session(token: str, user: dict = Depends(require_admin)):
    p = await _auth.pool()
    res = await p.execute("DELETE FROM user_sessions WHERE token = $1", token)
    if res == "DELETE 0" and len(token) == 64:
        rows = await p.fetch("SELECT token FROM user_sessions")
        matched = next(
            (
                r["token"]
                for r in rows
                if hmac.compare_digest(_session_id(r["token"]), token)
            ),
            None,
        )
        if matched:
            res = await p.execute("DELETE FROM user_sessions WHERE token = $1", matched)
    if res == "DELETE 0":
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}

@router.get("/audit")
async def get_audit_events(user: dict = Depends(require_admin)):
    p = await _auth.pool()
    if not await _table_exists(p, "audit_events"):
        return []
    audit_columns = await _columns(p, "audit_events")
    select_parts = [
        _select_column(audit_columns, "id", "NULL::bigint", table_alias="a"),
        _select_column(audit_columns, "user_id", "NULL::bigint", table_alias="a"),
        "COALESCE(u.email, a.email) AS user_email" if "email" in audit_columns else "u.email AS user_email",
        _select_column(audit_columns, "action", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "resource_type", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "resource_id", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "metadata", "NULL::jsonb", "details", table_alias="a"),
        _select_column(audit_columns, "ip", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "created_at", "NULL::timestamptz", table_alias="a"),
    ]
    order_expr = "a.created_at DESC" if "created_at" in audit_columns else "a.id DESC"
    rows = await p.fetch(
        f"""SELECT {", ".join(select_parts)}
           FROM audit_events a
           LEFT JOIN users u ON u.id = a.user_id
           ORDER BY {order_expr}
           LIMIT 100"""
    )
    res = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("details"), asyncpg.Record):
            d["details"] = dict(d["details"])
        if isinstance(d.get("details"), str):
            try:
                d["details"] = json.loads(d["details"])
            except json.JSONDecodeError:
                pass
        res.append(d)
    return res
