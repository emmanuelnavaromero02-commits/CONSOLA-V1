from __future__ import annotations

import json
import re
import uuid
from typing import Any

import psycopg2
import psycopg2.extras

from app.registry import tool
from app.tools.postgres import _conn


_FIELDS = (
    "id", "cartridge_id", "slug", "name", "description",
    "instructions", "personality", "allowed_tools", "rag_filter", "extra",
    "model", "max_tokens", "temperature",
    "owner_user_id", "is_active", "created_at", "updated_at",
)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+$")
_TEXT_LIMITS = {
    "name": 160,
    "description": 2000,
    "instructions": 32000,
    "personality": 8000,
    "model": 160,
}


def _validate_json_object(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _validate_allowed_tools(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("allowed_tools must be a list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _TOOL_NAME_RE.fullmatch(item):
            raise ValueError("allowed_tools must contain fully-qualified '<server>__<tool>' names")
        if item not in out:
            out.append(item)
    if len(out) > 100:
        raise ValueError("allowed_tools cannot exceed 100 entries")
    return out


def _validate_agent_payload(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    required = {"cartridge_id", "slug", "name", "instructions"}
    if not partial:
        missing = [k for k in required if not payload.get(k)]
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")

    for key, value in payload.items():
        if key not in _UPDATABLE and key != "cartridge_id":
            continue
        if value is None:
            continue
        if key == "slug":
            value = str(value).strip()
            if not _SLUG_RE.fullmatch(value):
                raise ValueError("slug must be lowercase alphanumeric, '-' or '_' and max 80 chars")
        elif key == "cartridge_id":
            value = str(value).strip()
            if not _SLUG_RE.fullmatch(value):
                raise ValueError("cartridge_id must be lowercase alphanumeric, '-' or '_' and max 80 chars")
        elif key in _TEXT_LIMITS:
            value = str(value).strip()
            if key in {"name", "instructions"} and not value:
                raise ValueError(f"{key} cannot be empty")
            if len(value) > _TEXT_LIMITS[key]:
                raise ValueError(f"{key} exceeds {_TEXT_LIMITS[key]} characters")
        elif key == "allowed_tools":
            value = _validate_allowed_tools(value)
        elif key in {"rag_filter", "extra"}:
            value = _validate_json_object(value, key)
        elif key == "max_tokens":
            value = int(value)
            if value < 256 or value > 64000:
                raise ValueError("max_tokens must be between 256 and 64000")
        elif key == "temperature":
            value = float(value)
            if value < 0 or value > 2:
                raise ValueError("temperature must be between 0 and 2")
        elif key == "is_active":
            value = bool(value)
        clean[key] = value
    return clean


def _row_to_dict(row: dict | None) -> dict | None:
    if not row:
        return None
    d: dict[str, Any] = {}
    for k in _FIELDS:
        v = row.get(k)
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


def _scope_from_context(security_context: dict | None) -> tuple[str | None, str | None, int | None]:
    if not isinstance(security_context, dict):
        return None, None, None
    tenant_id = str(security_context.get("tenant_id") or "").strip() or None
    workspace_id = str(security_context.get("workspace_id") or "").strip() or None
    raw_user_id = security_context.get("user_id") or security_context.get("id")
    try:
        user_id = int(raw_user_id) if raw_user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    return tenant_id, workspace_id, user_id


def _set_db_scope(cur, security_context: dict | None) -> tuple[str | None, str | None, int | None]:
    tenant_id, workspace_id, user_id = _scope_from_context(security_context)
    if tenant_id and workspace_id:
        cur.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
            (tenant_id, workspace_id),
        )
    return tenant_id, workspace_id, user_id


def _fetch_one(cur, sql: str, params: tuple) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


@tool(
    name="agent_list",
    description=(
        "List configured agents. Optionally filter by cartridge. Use this BEFORE "
        "creating a new agent to avoid duplicates and to discover what's already "
        "available in a cartridge."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id":     {"type": "string", "description": "Filter by cartridge id (omit for all)"},
            "include_inactive": {"type": "boolean", "description": "Include soft-disabled agents", "default": False},
        },
    },
)
async def agent_list(cartridge_id: str | None = None,
                     include_inactive: bool = False,
                     security_context: dict | None = None) -> dict:
    where, params = [], []
    if cartridge_id:
        params.append(cartridge_id)
        where.append(f"cartridge_id = %s")
    if not include_inactive:
        where.append("is_active = TRUE")
    sql = "SELECT * FROM agents"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY cartridge_id, slug"
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _set_db_scope(cur, security_context)
            cur.execute(sql, tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"agents": [_row_to_dict(r) for r in rows]}


@tool(
    name="agent_get",
    description="Fetch a single agent by id OR by (cartridge_id, slug).",
    input_schema={
        "type": "object",
        "properties": {
            "agent_id":     {"type": "string"},
            "cartridge_id": {"type": "string"},
            "slug":         {"type": "string"},
        },
    },
)
async def agent_get(agent_id: str | None = None,
                    cartridge_id: str | None = None,
                    slug: str | None = None,
                    security_context: dict | None = None) -> dict:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _set_db_scope(cur, security_context)
            if agent_id:
                row = _fetch_one(cur, "SELECT * FROM agents WHERE id=%s::uuid", (agent_id,))
            elif cartridge_id and slug:
                row = _fetch_one(
                    cur,
                    "SELECT * FROM agents WHERE cartridge_id=%s AND slug=%s",
                    (cartridge_id, slug),
                )
            else:
                return {"error": "must pass agent_id OR (cartridge_id AND slug)"}
    finally:
        conn.close()
    if not row:
        return {"error": "agent not found"}
    return _row_to_dict(row)


@tool(
    name="agent_create",
    description=(
        "Create a new agent inside a cartridge. The cartridge is the agent's "
        "'mind' (data + hints); this row is the agent's specialization (role, "
        "tools, personality, prompt). `allowed_tools` is a list of fully-qualified "
        "tool names like ['refinement__query_dataset', 'mcp-infra__search_rag']."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id":  {"type": "string"},
            "slug":          {"type": "string", "description": "Stable identifier within the cartridge (snake_case)"},
            "name":          {"type": "string", "description": "Human-readable label"},
            "description":   {"type": "string"},
            "instructions":  {"type": "string", "description": "System prompt — what the agent does and how"},
            "personality":   {"type": "string", "description": "Tone / style / language"},
            "allowed_tools": {"type": "array", "items": {"type": "string"},
                              "description": "Fully-qualified tool names: '<server>__<tool>'"},
            "rag_filter":    {"type": "object", "description": "Defaults applied to search_rag, e.g. {'kinds':['document']}"},
            "model":         {"type": "string", "description": "Model id, e.g. claude-sonnet-4-6 / claude-haiku-4-5-20251001"},
            "max_tokens":    {"type": "integer", "default": 8192},
            "temperature":   {"type": "number",  "default": 0.4},
            "extra":         {"type": "object", "description": "{'variables':{}, 'schedule':{'cron':'…'}}"},
            "is_active":     {"type": "boolean", "default": True},
        },
        "required": ["cartridge_id", "slug", "name", "instructions"],
    },
)
async def agent_create(
    cartridge_id: str, slug: str, name: str, instructions: str,
    description: str = "", personality: str = "",
    allowed_tools: list[str] | None = None,
    rag_filter: dict | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 8192, temperature: float = 0.4,
    extra: dict | None = None, is_active: bool = True,
    security_context: dict | None = None,
) -> dict:
    try:
        payload = _validate_agent_payload(
            {
                "cartridge_id": cartridge_id,
                "slug": slug,
                "name": name,
                "description": description,
                "instructions": instructions,
                "personality": personality,
                "allowed_tools": allowed_tools,
                "rag_filter": rag_filter,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "extra": extra,
                "is_active": is_active,
            }
        )
    except ValueError as exc:
        return {"error": str(exc)}
    new_id = str(uuid.uuid4())
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            tenant_id, workspace_id, user_id = _set_db_scope(cur, security_context)
            columns = [
                "id", "cartridge_id", "slug", "name", "description",
                "instructions", "personality", "allowed_tools", "rag_filter", "extra",
                "model", "max_tokens", "temperature", "is_active",
            ]
            placeholders = [
                "%s::uuid", "%s", "%s", "%s", "%s",
                "%s", "%s", "%s::jsonb", "%s::jsonb", "%s::jsonb",
                "%s", "%s", "%s", "%s",
            ]
            values: list = [
                new_id, payload["cartridge_id"], payload["slug"], payload["name"], payload.get("description", ""),
                payload["instructions"], payload.get("personality", ""),
                json.dumps(payload.get("allowed_tools") or []),
                json.dumps(payload.get("rag_filter") or {}),
                json.dumps(payload.get("extra") or {}),
                payload.get("model", "claude-sonnet-4-6"),
                payload.get("max_tokens", 8192),
                payload.get("temperature", 0.4),
                payload.get("is_active", True),
            ]
            if tenant_id and workspace_id:
                columns.extend(["tenant_id", "workspace_id"])
                placeholders.extend(["%s::uuid", "%s::uuid"])
                values.extend([tenant_id, workspace_id])
            if user_id is not None:
                columns.append("owner_user_id")
                placeholders.append("%s")
                values.append(user_id)
            cur.execute(
                f"""
                INSERT INTO agents ({', '.join(columns)})
                VALUES ({', '.join(placeholders)})
                RETURNING *
                """,
                tuple(values),
            )
            row = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return {"error": f"agent with slug '{slug}' already exists in cartridge '{cartridge_id}'"}
    except Exception as exc:                                       # noqa: BLE001
        conn.rollback()
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()
    return _row_to_dict(dict(row))


_UPDATABLE = {
    "name", "description", "instructions", "personality",
    "allowed_tools", "rag_filter", "extra",
    "model", "max_tokens", "temperature", "is_active",
    "slug",
}
_JSON_FIELDS = {"allowed_tools", "rag_filter", "extra"}


@tool(
    name="agent_update",
    description=(
        "Patch one or more fields of an existing agent. Pass `agent_id` plus only "
        "the fields you want to change."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "agent_id":      {"type": "string"},
            "name":          {"type": "string"},
            "description":   {"type": "string"},
            "instructions":  {"type": "string"},
            "personality":   {"type": "string"},
            "allowed_tools": {"type": "array", "items": {"type": "string"}},
            "rag_filter":    {"type": "object"},
            "extra":         {"type": "object"},
            "model":         {"type": "string"},
            "max_tokens":    {"type": "integer"},
            "temperature":   {"type": "number"},
            "is_active":     {"type": "boolean"},
            "slug":          {"type": "string"},
        },
        "required": ["agent_id"],
    },
)
async def agent_update(agent_id: str, security_context: dict | None = None, **patch) -> dict:
    try:
        uuid.UUID(str(agent_id))
        patch = _validate_agent_payload(patch, partial=True)
    except ValueError as exc:
        return {"error": str(exc)}
    sets, params = [], []
    for k, v in patch.items():
        if k not in _UPDATABLE:
            continue
        params.append(json.dumps(v) if k in _JSON_FIELDS else v)
        cast = "::jsonb" if k in _JSON_FIELDS else ""
        sets.append(f"{k} = %s{cast}")
    if not sets:
        return {"error": "no updatable fields provided"}
    sets.append("updated_at = NOW()")
    params.append(agent_id)
    sql = f"UPDATE agents SET {', '.join(sets)} WHERE id = %s::uuid RETURNING *"
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _set_db_scope(cur, security_context)
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        conn.commit()
    except Exception as exc:                                       # noqa: BLE001
        conn.rollback()
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()
    if not row:
        return {"error": "agent not found"}
    return _row_to_dict(dict(row))


@tool(
    name="agent_delete",
    description="Permanently delete an agent. Prefer agent_update with is_active=false for soft-disable.",
    input_schema={
        "type": "object",
        "properties": {"agent_id": {"type": "string"}},
        "required": ["agent_id"],
    },
)
async def agent_delete(agent_id: str, security_context: dict | None = None) -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _set_db_scope(cur, security_context)
            cur.execute("DELETE FROM agents WHERE id=%s::uuid", (agent_id,))
            n = cur.rowcount
        conn.commit()
    except Exception as exc:                                       # noqa: BLE001
        conn.rollback()
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()
    return {"deleted": n == 1}
