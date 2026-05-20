"""
Agent CRUD service — persists rows in the `agents` table and exposes them
as plain dicts for the HTTP / MCP layers.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import asyncpg


# ── Connection helper ──────────────────────────────────────────────────────

async def _pg():
    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    return await asyncpg.connect(dsn)


# ── Serialization ──────────────────────────────────────────────────────────

_FIELDS = [
    "id", "cartridge_id", "slug", "name", "description",
    "instructions", "personality", "allowed_tools", "rag_filter", "extra",
    "model", "max_tokens", "temperature",
    "owner_user_id", "is_active", "created_at", "updated_at",
]


def _row_to_dict(row: asyncpg.Record | None) -> dict | None:
    if row is None:
        return None
    d: dict[str, Any] = {}
    for k in _FIELDS:
        v = row.get(k) if isinstance(row, dict) else row[k]
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
        elif k == "id":
            d[k] = str(v) if v else None
        else:
            d[k] = v
    return d


# ── Public CRUD ────────────────────────────────────────────────────────────

async def list_agents(cartridge_id: str | None = None,
                      include_inactive: bool = False) -> list[dict]:
    where  = []
    params: list = []
    if cartridge_id:
        params.append(cartridge_id)
        where.append(f"cartridge_id = ${len(params)}")
    if not include_inactive:
        where.append("is_active = TRUE")
    sql = "SELECT * FROM agents"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY cartridge_id, slug"

    conn = await _pg()
    try:
        rows = await conn.fetch(sql, *params)
    finally:
        await conn.close()
    return [_row_to_dict(r) for r in rows]


async def get_agent(agent_id: str) -> dict | None:
    conn = await _pg()
    try:
        row = await conn.fetchrow("SELECT * FROM agents WHERE id=$1::uuid", agent_id)
    finally:
        await conn.close()
    return _row_to_dict(row)


async def get_agent_by_slug(cartridge_id: str, slug: str) -> dict | None:
    conn = await _pg()
    try:
        row = await conn.fetchrow(
            "SELECT * FROM agents WHERE cartridge_id=$1 AND slug=$2",
            cartridge_id, slug,
        )
    finally:
        await conn.close()
    return _row_to_dict(row)


async def create_agent(payload: dict, owner_user_id: int | None = None) -> dict:
    required = ("cartridge_id", "slug", "name", "instructions")
    for f in required:
        if not (payload.get(f) or "").strip():
            raise ValueError(f"missing required field: {f}")
    _validate_payload(payload, partial=False)

    new_id = uuid.uuid4()
    conn = await _pg()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO agents (
                id, cartridge_id, slug, name, description,
                instructions, personality, allowed_tools, rag_filter, extra,
                model, max_tokens, temperature,
                owner_user_id, is_active
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8::jsonb, $9::jsonb, $10::jsonb,
                $11, $12, $13,
                $14, $15
            )
            RETURNING *
            """,
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
        )
    finally:
        await conn.close()
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


async def update_agent(agent_id: str, patch: dict) -> dict | None:
    _validate_payload(patch, partial=True)
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
    conn = await _pg()
    try:
        row = await conn.fetchrow(sql, *params)
    finally:
        await conn.close()
    return _row_to_dict(row)


async def delete_agent(agent_id: str) -> bool:
    conn = await _pg()
    try:
        res = await conn.execute("DELETE FROM agents WHERE id=$1::uuid", agent_id)
    finally:
        await conn.close()
    return res.endswith(" 1")


# ── Runs ───────────────────────────────────────────────────────────────────

async def list_runs(agent_id: str, limit: int = 20) -> list[dict]:
    conn = await _pg()
    try:
        rows = await conn.fetch(
            "SELECT id, started_at, finished_at, status, "
            "       length(output_text) AS out_chars, "
            "       jsonb_array_length(tool_calls) AS n_tool_calls, "
            "       error_message "
            "FROM agent_runs WHERE agent_id=$1::uuid "
            "ORDER BY started_at DESC LIMIT $2",
            agent_id, limit,
        )
    finally:
        await conn.close()
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


async def get_run(run_id: int) -> dict | None:
    conn = await _pg()
    try:
        row = await conn.fetchrow(
            "SELECT id, agent_id, user_id, started_at, finished_at, status, "
            "       input_messages, output_text, tool_calls, error_message "
            "FROM agent_runs WHERE id=$1",
            run_id,
        )
    finally:
        await conn.close()
    if not row:
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
