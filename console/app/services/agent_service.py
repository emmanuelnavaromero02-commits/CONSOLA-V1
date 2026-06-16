"""
Agent CRUD service — persists rows in the `agents` table and exposes them
as plain dicts for the HTTP / MCP layers.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
import uuid
from typing import Any, AsyncIterator

import asyncpg

from app.services.db_scope import SET_SCOPE_SQL


# ── Connection helper ──────────────────────────────────────────────────────

async def _pg():
    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    return await asyncpg.connect(dsn)


@asynccontextmanager
async def _scoped_pg(user_context: dict | None = None) -> AsyncIterator[asyncpg.Connection]:
    conn = await _pg()
    tenant_id, workspace_id = _tenant_workspace(user_context)
    try:
        if workspace_id:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id or "", workspace_id)
                yield conn
        else:
            yield conn
    finally:
        await conn.close()


# ── Serialization ──────────────────────────────────────────────────────────

_FIELDS = [
    "id", "tenant_id", "workspace_id", "cartridge_id", "slug", "name", "description",
    "instructions", "personality", "allowed_tools", "rag_filter", "extra",
    "model", "max_tokens", "temperature",
    "owner_user_id", "is_active", "created_at", "updated_at",
]


def _row_value(row: asyncpg.Record | dict, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _row_to_dict(row: asyncpg.Record | None) -> dict | None:
    if row is None:
        return None
    d: dict[str, Any] = {}
    for k in _FIELDS:
        v = _row_value(row, k)
        if k in ("allowed_tools", "rag_filter", "extra"):
            if v is None:
                d[k] = [] if k == "allowed_tools" else {}
            elif isinstance(v, (dict, list)):
                d[k] = v
            else:
                try:
                    d[k] = json.loads(v)
                except Exception:
                    d[k] = [] if k == "allowed_tools" else {}
        elif k in ("created_at", "updated_at"):
            d[k] = v.isoformat() if v else None
        elif k in {"id", "tenant_id", "workspace_id"}:
            d[k] = str(v) if v else None
        else:
            d[k] = v
    return d


async def _has_scope_columns(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema='public'
               AND table_name='agents'
               AND column_name='workspace_id'
        )
        """
    ))


def _is_platform_admin(user_context: dict | None) -> bool:
    return (user_context or {}).get("role") in {"owner", "super_admin", "admin"}


def _tenant_workspace(user_context: dict | None) -> tuple[str | None, str | None]:
    user = user_context or {}
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip() or None
    workspace_id = str(user.get("active_workspace_id") or user.get("workspace_id") or "").strip() or None
    return tenant_id, workspace_id


def _allowed_cartridges(user_context: dict | None) -> set[str] | None:
    if not user_context or _is_platform_admin(user_context):
        return None
    return {str(item) for item in (user_context.get("allowed_cartridges") or []) if str(item).strip()}


def _can_view(agent: dict | None, user_context: dict | None) -> bool:
    if not agent:
        return False
    if not user_context or _is_platform_admin(user_context):
        return True
    allowed = _allowed_cartridges(user_context) or set()
    if str(agent.get("cartridge_id") or "") not in allowed:
        return False
    _, workspace_id = _tenant_workspace(user_context)
    agent_workspace = agent.get("workspace_id")
    return agent_workspace is None or str(agent_workspace) == str(workspace_id)


def _can_manage(agent: dict | None, user_context: dict | None) -> bool:
    if not agent:
        return False
    if _is_platform_admin(user_context):
        return True
    _, workspace_id = _tenant_workspace(user_context)
    return bool(workspace_id and agent.get("workspace_id") and str(agent.get("workspace_id")) == str(workspace_id))


def _require_allowed_cartridge(payload: dict, user_context: dict | None) -> None:
    allowed = _allowed_cartridges(user_context)
    if allowed is None:
        return
    cartridge_id = str(payload.get("cartridge_id") or "")
    if cartridge_id not in allowed:
        raise PermissionError("cartridge is not visible in this workspace")


# ── Public CRUD ────────────────────────────────────────────────────────────

async def list_agents(cartridge_id: str | None = None,
                      include_inactive: bool = False,
                      user_context: dict | None = None) -> list[dict]:
    where  = []
    params: list = []
    if cartridge_id:
        params.append(cartridge_id)
        where.append(f"cartridge_id = ${len(params)}")
    if not include_inactive:
        where.append("is_active = TRUE")

    async with _scoped_pg(user_context) as conn:
        if user_context and not _is_platform_admin(user_context):
            allowed = sorted(_allowed_cartridges(user_context) or set())
            if not allowed:
                return []
            params.append(allowed)
            where.append(f"cartridge_id = ANY(${len(params)}::text[])")
            if await _has_scope_columns(conn):
                _, workspace_id = _tenant_workspace(user_context)
                if not workspace_id:
                    return []
                params.append(workspace_id)
                where.append(f"(workspace_id IS NULL OR workspace_id = ${len(params)}::uuid)")
        sql = "SELECT * FROM agents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY cartridge_id, slug"
        rows = await conn.fetch(sql, *params)
    return [_row_to_dict(r) for r in rows]


async def get_agent(agent_id: str, user_context: dict | None = None) -> dict | None:
    async with _scoped_pg(user_context) as conn:
        row = await conn.fetchrow("SELECT * FROM agents WHERE id=$1::uuid", agent_id)
    agent = _row_to_dict(row)
    return agent if _can_view(agent, user_context) else None


async def get_agent_by_slug(
    cartridge_id: str,
    slug: str,
    user_context: dict | None = None,
) -> dict | None:
    async with _scoped_pg(user_context) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM agents WHERE cartridge_id=$1 AND slug=$2",
            cartridge_id, slug,
        )
    return _row_to_dict(row)


async def create_agent(payload: dict, owner_user_id: int | None = None, user_context: dict | None = None) -> dict:
    required = ("cartridge_id", "slug", "name", "instructions")
    for f in required:
        if not (payload.get(f) or "").strip():
            raise ValueError(f"missing required field: {f}")
    _validate_payload(payload, partial=False)
    _require_allowed_cartridge(payload, user_context)

    new_id = uuid.uuid4()
    async with _scoped_pg(user_context) as conn:
        scoped = await _has_scope_columns(conn)
        tenant_id, workspace_id = _tenant_workspace(user_context)
        if user_context and not _is_platform_admin(user_context) and not (scoped and tenant_id and workspace_id):
            raise PermissionError("workspace-scoped agents migration is required")
        scope_cols = ", tenant_id, workspace_id" if scoped else ""
        scope_vals = ", $16::uuid, $17::uuid" if scoped else ""
        params = [
            new_id,
            payload["cartridge_id"],
            payload["slug"].strip(),
            payload["name"].strip(),
            (payload.get("description") or "").strip(),
            payload["instructions"],
            payload.get("personality") or "",
            json.dumps(payload.get("allowed_tools") or []),
            json.dumps(payload.get("rag_filter") or {}),
            json.dumps(payload.get("extra") or {}),
            payload.get("model") or "claude-sonnet-4-6",
            int(payload.get("max_tokens") or 8192),
            float(payload.get("temperature") if payload.get("temperature") is not None else 0.4),
            owner_user_id,
            bool(payload.get("is_active", True)),
        ]
        if scoped:
            params.extend([
                payload.get("tenant_id") if _is_platform_admin(user_context) else tenant_id,
                payload.get("workspace_id") if _is_platform_admin(user_context) else workspace_id,
            ])
        row = await conn.fetchrow(
            f"""
            INSERT INTO agents (
                id, cartridge_id, slug, name, description,
                instructions, personality, allowed_tools, rag_filter, extra,
                model, max_tokens, temperature,
                owner_user_id, is_active{scope_cols}
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8::jsonb, $9::jsonb, $10::jsonb,
                $11, $12, $13,
                $14, $15{scope_vals}
            )
            RETURNING *
            """,
            *params,
        )
    return _row_to_dict(row)


_UPDATABLE = {
    "name", "description", "instructions", "personality",
    "allowed_tools", "rag_filter", "extra",
    "model", "max_tokens", "temperature", "is_active",
    "slug", "cartridge_id",   # rename / move allowed
}
_JSON_FIELDS = {"allowed_tools", "rag_filter", "extra"}


def _validate_payload(payload: dict, *, partial: bool = False) -> None:
    if (not partial) or "allowed_tools" in payload:
        if not isinstance(payload.get("allowed_tools") or [], list):
            raise ValueError("allowed_tools must be an array")
    if (not partial) or "rag_filter" in payload:
        if not isinstance(payload.get("rag_filter") or {}, dict):
            raise ValueError("rag_filter must be an object")
    if (not partial) or "extra" in payload:
        if not isinstance(payload.get("extra") or {}, dict):
            raise ValueError("extra must be an object")

    if (not partial) or "max_tokens" in payload:
        try:
            max_tokens = int(payload.get("max_tokens") or 8192)
        except (TypeError, ValueError):
            raise ValueError("max_tokens must be an integer")
        if max_tokens < 256 or max_tokens > 64000:
            raise ValueError("max_tokens must be between 256 and 64000")
        payload["max_tokens"] = max_tokens

    if (not partial) or "temperature" in payload:
        try:
            temperature = float(payload.get("temperature") if payload.get("temperature") is not None else 0.4)
        except (TypeError, ValueError):
            raise ValueError("temperature must be a number")
        if temperature < 0 or temperature > 2:
            raise ValueError("temperature must be between 0 and 2")
        payload["temperature"] = temperature


async def update_agent(agent_id: str, patch: dict, user_context: dict | None = None) -> dict | None:
    _validate_payload(patch, partial=True)
    current = await get_agent(agent_id, user_context=user_context)
    if not current:
        return None
    if not _can_manage(current, user_context):
        raise PermissionError("agent is read-only for this workspace")
    if "cartridge_id" in patch:
        _require_allowed_cartridge(patch, user_context)
    sets: list[str] = []
    params: list = []
    for k, v in patch.items():
        if k not in _UPDATABLE:
            continue
        params.append(json.dumps(v) if k in _JSON_FIELDS else v)
        cast = "::jsonb" if k in _JSON_FIELDS else ""
        sets.append(f"{k} = ${len(params)}{cast}")
    if not sets:
        return await get_agent(agent_id)
    sets.append("updated_at = NOW()")
    params.append(agent_id)

    sql = f"UPDATE agents SET {', '.join(sets)} WHERE id = ${len(params)}::uuid RETURNING *"
    async with _scoped_pg(user_context) as conn:
        row = await conn.fetchrow(sql, *params)
    return _row_to_dict(row) if _can_view(_row_to_dict(row), user_context) else None


async def delete_agent(agent_id: str, user_context: dict | None = None) -> bool:
    current = await get_agent(agent_id, user_context=user_context)
    if not current:
        return False
    if not _can_manage(current, user_context):
        raise PermissionError("agent is read-only for this workspace")
    async with _scoped_pg(user_context) as conn:
        res = await conn.execute("DELETE FROM agents WHERE id=$1::uuid", agent_id)
    return res.endswith(" 1")


# ── Runs ───────────────────────────────────────────────────────────────────

async def list_runs(agent_id: str, limit: int = 20, user_context: dict | None = None) -> list[dict]:
    if not await get_agent(agent_id, user_context=user_context):
        return []
    async with _scoped_pg(user_context) as conn:
        rows = await conn.fetch(
            "SELECT id, started_at, finished_at, status, "
            "       length(output_text) AS out_chars, "
            "       jsonb_array_length(tool_calls) AS n_tool_calls, "
            "       error_message "
            "FROM agent_runs WHERE agent_id=$1::uuid "
            "ORDER BY started_at DESC LIMIT $2",
            agent_id, limit,
        )
    return [
        {
            "id":            r["id"],
            "started_at":    r["started_at"].isoformat() if r["started_at"] else None,
            "finished_at":   r["finished_at"].isoformat() if r["finished_at"] else None,
            "status":        r["status"],
            "out_chars":     r["out_chars"],
            "n_tool_calls":  r["n_tool_calls"],
            "error_message": r["error_message"],
        } for r in rows
    ]


async def get_run(run_id: int, user_context: dict | None = None) -> dict | None:
    async with _scoped_pg(user_context) as conn:
        row = await conn.fetchrow(
            "SELECT id, agent_id, user_id, started_at, finished_at, status, "
            "       input_messages, output_text, tool_calls, error_message "
            "FROM agent_runs WHERE id=$1",
            run_id,
        )
    if not row:
        return None
    if not await get_agent(str(row["agent_id"]), user_context=user_context):
        return None
    return {
        "id":             row["id"],
        "agent_id":       str(row["agent_id"]),
        "user_id":        row["user_id"],
        "started_at":     row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at":    row["finished_at"].isoformat() if row["finished_at"] else None,
        "status":         row["status"],
        "input_messages": row["input_messages"],
        "output_text":    row["output_text"],
        "tool_calls":     row["tool_calls"],
        "error_message":  row["error_message"],
    }
