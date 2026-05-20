"""
Agent runtime — executes a configurable Agent (row in `agents`).

The Agent's `cartridge_id` is its "mind" (hints inherited from the cartridge).
The Agent's own `instructions` + `personality` + `allowed_tools` + `rag_filter`
+ `model` are its specialization. The platform (this runtime + MCP servers)
is its "body".
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx

from app.security import get_internal_api_key
from app.services import llm_client
from app.services.security_context import build_security_context, rls_user_context

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
MCP_INFRA_URL  = os.environ.get("MCP_INFRA_URL",  "http://mcp-infra:8010")

SERVER_URLS = {
    "refinement": REFINEMENT_URL,
    "mcp-infra":  MCP_INFRA_URL,
}

_SERVER_ENV_KEYS = {
    "refinement": "REFINEMENT",
    "mcp-infra":  "MCP_INFRA",
}


def _headers_for(server_id: str) -> dict[str, str]:
    server_key = _SERVER_ENV_KEYS.get(server_id, server_id.replace("-", "_").upper())
    pair_key = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server_key}")
    if os.environ.get("APP_ENV", "production").lower() in {"production", "prod"} and not pair_key:
        raise RuntimeError(f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server_key}; legacy fallback disabled in production")
    return {
        "x-api-key": pair_key or get_internal_api_key(),
        "x-internal-service": "console",
    }


# ── Agent definition ─────────────────────────────────────────────────────────

@dataclass
class Agent:
    id:            str
    cartridge_id:  str
    slug:          str
    name:          str
    description:   str
    instructions:  str
    personality:   str
    allowed_tools: list[str]          # ["refinement__query_dataset", ...]
    rag_filter:    dict               # {"cartridges":[...], "kinds":[...]}
    model:         str
    max_tokens:    int
    temperature:   float
    extra:         dict               # {"variables":{}, "schedule":{...}}
    is_active:     bool = True

    @classmethod
    def from_row(cls, row: dict | asyncpg.Record) -> "Agent":
        if isinstance(row, asyncpg.Record):
            row = dict(row)

        def _as_obj(v, default):
            if v is None:
                return default
            if isinstance(v, (dict, list)):
                return v
            try:
                return json.loads(v)
            except Exception:
                return default

        return cls(
            id            = str(row["id"]),
            cartridge_id  = row["cartridge_id"],
            slug          = row["slug"],
            name          = row["name"],
            description   = row.get("description") or "",
            instructions  = row.get("instructions") or "",
            personality   = row.get("personality") or "",
            allowed_tools = _as_obj(row.get("allowed_tools"), []),
            rag_filter    = _as_obj(row.get("rag_filter"), {}),
            model         = row.get("model") or "claude-sonnet-4-6",
            max_tokens    = int(row.get("max_tokens") or 8192),
            temperature   = float(row.get("temperature") if row.get("temperature") is not None else 0.4),
            extra         = _as_obj(row.get("extra"), {}),
            is_active     = bool(row.get("is_active", True)),
        )


# ── Connection pool (lazy-init) ─────────────────────────────────────────────
# A single pool shared by every agent run avoids the per-call connect+close
# tax (≈30-50ms each) and the postgres `max_connections` ceiling we'd hit
# at moderate concurrency.

import asyncio as _asyncio

_pool: asyncpg.Pool | None = None
_pool_lock = _asyncio.Lock()


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            dsn = os.environ.get("DATABASE_URL", "").replace(
                "postgresql+psycopg2://", "postgresql://"
            )
            _pool = await asyncpg.create_pool(
                dsn, min_size=2, max_size=10, command_timeout=30,
            )
    return _pool


# ── Loaders ──────────────────────────────────────────────────────────────────

async def load_agent(agent_id: str) -> Agent | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM agents WHERE id=$1", agent_id)
    return Agent.from_row(row) if row else None


async def load_agent_by_slug(cartridge_id: str, slug: str) -> Agent | None:
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM agents WHERE cartridge_id=$1 AND slug=$2",
        cartridge_id, slug,
    )
    return Agent.from_row(row) if row else None


# ── Cartridge hints (mind context) ──────────────────────────────────────────

_hints_cache: dict[str, tuple[str, float]] = {}
_HINTS_TTL = 300


async def _cartridge_hints(cartridge_id: str) -> str:
    cached = _hints_cache.get(cartridge_id)
    if cached and (time.time() - cached[1]) < _HINTS_TTL:
        return cached[0]
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT COALESCE(assistant_hints,'') AS h FROM cartridges WHERE id=$1",
        cartridge_id,
    )
    text = (row["h"] if row else "") or ""
    _hints_cache[cartridge_id] = (text, time.time())
    return text


# ── Tool discovery (filtered by agent.allowed_tools) ────────────────────────
# Catalog cache: GET /mcp/tools is identical for every agent talking to the
# same MCP server. Cache the full server catalog with TTL, then filter
# per-agent purely in memory.

_catalog_cache: dict[str, tuple[list[dict], float]] = {}
_CATALOG_TTL = 300


async def _server_catalog(srv_id: str, base: str) -> list[dict]:
    cached = _catalog_cache.get(srv_id)
    if cached and (time.time() - cached[1]) < _CATALOG_TTL:
        return cached[0]
    try:
        async with httpx.AsyncClient(headers=_headers_for(srv_id), timeout=10) as c:
            r = await c.get(f"{base}/mcp/tools")
            r.raise_for_status()
            data = r.json()
        tools = data.get("tools", []) or []
    except Exception:
        # On failure keep the stale entry if we have one — better than no tools
        return cached[0] if cached else []
    _catalog_cache[srv_id] = (tools, time.time())
    return tools


async def _discover_agent_tools(agent: Agent) -> tuple[list[dict], dict[str, str]]:
    """Return (anthropic_tools, server_map) honoring agent.allowed_tools."""
    by_server: dict[str, set] = {}
    for full in agent.allowed_tools:
        if "__" not in full:
            continue
        srv, name = full.split("__", 1)
        by_server.setdefault(srv, set()).add(name)

    tools: list[dict] = []
    server_map: dict[str, str] = {}
    for srv_id, allow in by_server.items():
        base = SERVER_URLS.get(srv_id)
        if not base:
            continue
        for t in await _server_catalog(srv_id, base):
            if t["name"] not in allow:
                continue
            full = f"{srv_id}__{t['name']}"
            tools.append({
                "name":         full,
                "description":  t.get("description", ""),
                "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
            })
            server_map[full] = srv_id
    return tools, server_map


def invalidate_catalog_cache(server_id: str | None = None):
    """Force the next discovery to re-fetch from the MCP server."""
    if server_id is None:
        _catalog_cache.clear()
    else:
        _catalog_cache.pop(server_id, None)


# ── Invocation (applies rag_filter automatically) ───────────────────────────

def _rls_user_context(user: dict | None) -> dict:
    return rls_user_context(user)


def _agent_security_context(agent: Agent, user: dict | None) -> dict:
    if user is not None:
        return build_security_context(user)
    cartridge = (agent.cartridge_id or "").strip()
    prefixes = []
    if cartridge:
        prefixes = [
            f"raw/{cartridge}/",
            f"silver/{cartridge}/",
            f"gold/{cartridge}/",
            f"uploads/{cartridge}/",
            f"cartridges/{cartridge}/",
        ]
    return {
        "trusted": True,
        "source": "agent_runner",
        "user_id": None,
        "email": "agent-runner@omega.local",
        "role": "agent",
        "workspace_role": None,
        "tenant_id": None,
        "workspace_id": None,
        "permissions": ["datasets.read", "cartridges.read"],
        "allowed_cartridges": [cartridge] if cartridge else [],
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": prefixes,
        "_trusted_admin": False,
    }


def _make_invoke(agent: Agent, user: dict | None = None):
    rf = agent.rag_filter or {}

    async def invoke(server_id: str, tool: str, args: dict) -> Any:
        # Apply RAG filter defaults if the agent didn't override per-call.
        # Only `kinds` is supported by the current search_rag MCP tool; a
        # `cartridges` filter would need a future tool change to take effect.
        if tool in ("search_rag", "list_rag_sources"):
            if "kinds" in rf and "kinds" not in args:
                args = {**args, "kinds": rf["kinds"]}
        if server_id == "refinement" and tool in ("query_dataset", "preview_sql", "preview_transform"):
            # Always overwrite model-supplied context with the authenticated
            # backend context. Agent prompts/tool args are untrusted input.
            args = {**args, "user_context": _rls_user_context(user)}

        base = SERVER_URLS.get(server_id)
        if not base:
            return {"error": f"unknown server: {server_id}"}
        payload = {
            "tool": tool,
            "args": args,
            "security_context": _agent_security_context(agent, user),
        }
        async with httpx.AsyncClient(headers=_headers_for(server_id), timeout=120) as c:
            r = await c.post(f"{base}/mcp/invoke", json=payload)
        try:
            payload = r.json()
        except Exception:
            return {"error": f"non-JSON response: {r.text[:300]}"}
        if r.status_code >= 400:
            if isinstance(payload, dict):
                message = payload.get("detail") or payload.get("error") or f"HTTP {r.status_code}"
            else:
                message = f"HTTP {r.status_code}"
            return {"error": message, "status_code": r.status_code}
        return payload

    return invoke


# ── System prompt assembly ──────────────────────────────────────────────────

def _interpolate_variables(text: str, variables: dict) -> str:
    if not variables:
        return text
    for k, v in variables.items():
        text = text.replace("{{" + str(k) + "}}", str(v))
    return text


def _build_system_prompt(agent: Agent, cartridge_hints: str) -> str:
    parts = [
        f"# Agente: {agent.name}",
        "",
        f"Eres un agente especializado dentro del cartucho `{agent.cartridge_id}`. "
        f"Tu rol específico está definido a continuación.",
        "",
        "## Instrucciones",
        agent.instructions.strip() or "(sin instrucciones específicas)",
    ]
    if agent.personality.strip():
        parts += ["", "## Estilo / Personalidad", agent.personality.strip()]
    if cartridge_hints.strip():
        parts += [
            "",
            f"<hints_cartucho id=\"{agent.cartridge_id}\">",
            cartridge_hints.strip(),
            "</hints_cartucho>",
        ]
    text = "\n".join(parts)
    vars_dict = (agent.extra or {}).get("variables") or {}
    return _interpolate_variables(text, vars_dict)


# ── Run logging ─────────────────────────────────────────────────────────────

async def _start_run(agent_id: str, user_id: int | None, input_messages: list[dict]) -> int:
    pool = await _get_pool()
    row = await pool.fetchrow(
        "INSERT INTO agent_runs (agent_id, user_id, input_messages) "
        "VALUES ($1, $2, $3::jsonb) RETURNING id",
        agent_id, user_id, json.dumps(input_messages),
    )
    return int(row["id"])


async def _finish_run(run_id: int, *, status: str, output_text: str = "",
                      tool_calls: list[dict] | None = None,
                      error_message: str | None = None):
    pool = await _get_pool()
    await pool.execute(
        "UPDATE agent_runs SET finished_at=NOW(), status=$2, output_text=$3, "
        "tool_calls=$4::jsonb, error_message=$5 WHERE id=$1",
        run_id, status, output_text, json.dumps(tool_calls or []), error_message,
    )


# ── Public entry ────────────────────────────────────────────────────────────

async def run(
    agent: Agent,
    message: str,
    history: list[dict] | None = None,
    user: dict | None = None,
    on_event=None,
) -> dict:
    """Execute one turn of the agent.

    Returns {"reply", "viewer_urls", "messages", "agent_id", "run_id"}.
    """
    if not agent.is_active:
        return {"reply": "(agent inactive)", "viewer_urls": [], "messages": [],
                "agent_id": agent.id, "run_id": None}

    history = history or []
    input_messages = list(history) + [{"role": "user", "content": message}]

    hints   = await _cartridge_hints(agent.cartridge_id)
    system  = _build_system_prompt(agent, hints)
    tools, server_map = await _discover_agent_tools(agent)
    invoke  = _make_invoke(agent, user=user)

    user_id = user.get("id") if user else None
    run_id  = await _start_run(agent.id, user_id, input_messages)

    tool_calls_log: list[dict] = []
    async def _wrapped_on_event(ev):
        if ev.get("type") == "tool_use":
            tool_calls_log.append({
                "tool":   ev.get("tool"),
                "server": ev.get("server"),
                "args":   ev.get("args"),
            })
        elif ev.get("type") == "tool_result" and tool_calls_log:
            tool_calls_log[-1]["summary"] = ev.get("summary")
        if on_event is not None:
            await on_event(ev)

    try:
        reply, viewer_urls, full_msgs = await llm_client.chat(
            system=system,
            messages=input_messages,
            tools=tools,
            invoke_tool=invoke,
            tool_server_map=server_map,
            on_event=_wrapped_on_event,
            model=agent.model,
            max_tokens=agent.max_tokens,
            temperature=agent.temperature,
        )
    except _asyncio.CancelledError:
        await _finish_run(run_id, status="cancelled", tool_calls=tool_calls_log,
                          error_message="Cancelled")
        raise
    except Exception as exc:
        await _finish_run(run_id, status="error", tool_calls=tool_calls_log,
                          error_message=f"{type(exc).__name__}: {exc}")
        raise

    await _finish_run(run_id, status="ok", output_text=reply or "",
                      tool_calls=tool_calls_log)

    return {
        "reply":       reply,
        "viewer_urls": viewer_urls,
        "messages":    full_msgs,
        "agent_id":    agent.id,
        "run_id":      run_id,
    }


def invalidate_hint_cache(cartridge_id: str | None = None):
    if cartridge_id is None:
        _hints_cache.clear()
    else:
        _hints_cache.pop(cartridge_id, None)
