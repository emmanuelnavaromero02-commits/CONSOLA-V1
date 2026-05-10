"""
MCP Server Registry — console side
Stores registered MCP servers in postgres and periodically health-checks them.

Contract each MCP server must implement:
  GET  {url}/mcp/tools   → {"tools": [{name, description, input_schema}]}
  POST {url}/mcp/invoke  → body: {tool, args} → {result} | {error}
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.parse

import asyncpg
import httpx

from app.security import get_internal_api_key

_pool: asyncpg.Pool | None = None
DATABASE_URL = os.environ.get("DATABASE_URL", "")


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def startup():
    """Register built-in servers from environment at app startup."""
    console_url = os.environ.get("CONSOLE_URL", "http://console:8000")
    builtin = [
        {
            "id":          "refinement",
            "name":        "Refinement Engine",
            "url":         os.environ.get("REFINEMENT_URL", "http://refinement:8500"),
            "category":    "refinement",
            "description": "DuckDB lakehouse: bronze/silver/gold, LLM SQL generation",
        },
        {
            "id":          "infra",
            "name":        "Infrastructure (Airflow · MinIO · Postgres · Superset)",
            "url":         os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010"),
            "category":    "infrastructure",
            "description": (
                "Platform tools: manage Airflow DAGs, browse MinIO lakehouse, "
                "query PostgreSQL schemas, create Superset dashboards"
            ),
        },
        {
            "id":          "monitoring",
            "name":        "Monitoring & Deeplinks",
            "url":         f"{console_url}/monitoring",
            "category":    "monitoring",
            "description": "Genera deeplinks para visualizar jobs, schemas, datasets y modelo semántico",
        },
        {
            "id":          "studio_ops",
            "name":        "Studio Operations",
            "url":         f"{console_url}/studio_ops",
            "category":    "studio",
            "description": "Cartridge & entity management: rename_entity, list_entities, update_entity",
        },
    ]
    for server in builtin:
        url = server["url"].strip()
        if url:
            await register(server)

    # RAG migrado a mcp-infra — quita el registro standalone si quedó de antes
    pool = await _get_pool()
    await pool.execute("DELETE FROM mcp_servers WHERE id='rag'")


async def list_servers() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT * FROM mcp_servers ORDER BY registered_at")
    result = []
    for r in rows:
        d = dict(r)
        # tools stored as jsonb — may come back as string in some asyncpg versions
        if isinstance(d.get("tools"), str):
            try:
                d["tools"] = json.loads(d["tools"])
            except Exception:
                d["tools"] = []
        result.append(d)
    return result




def validate_mcp_server_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL de servidor MCP no permitida por seguridad.")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL de servidor MCP no permitida por seguridad.")

    # Internal known hosts whitelist
    if hostname in ("console", "workspace", "refinement", "mcp-infra", "airflow", "vault", "postgres", "postgres_gold", "replicon-mock", "superset"):
        return

    if hostname in ("localhost", "metadata.google.internal", "metadata"):
        raise ValueError("URL de servidor MCP no permitida por seguridad.")

    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, type, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_loopback or ip_obj.is_link_local:
                raise ValueError("URL de servidor MCP no permitida por seguridad.")
            if ip_obj.is_private:
                raise ValueError("URL de servidor MCP no permitida por seguridad.")
            if str(ip_obj) in ("0.0.0.0", "::"):
                raise ValueError("URL de servidor MCP no permitida por seguridad.")
    except socket.gaierror:
        pass


async def register(server: dict) -> dict:
    validate_mcp_server_url(server["url"])
    pool = await _get_pool()
    tools = await _fetch_tools(server["url"])
    await pool.execute("""
        INSERT INTO mcp_servers (id, name, url, category, description, tools, healthy, last_seen)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,NOW())
        ON CONFLICT (id) DO UPDATE SET
            name=$2, url=$3, category=$4, description=$5,
            tools=$6::jsonb, healthy=$7, last_seen=NOW()
    """,
        server["id"],
        server["name"],
        server["url"],
        server.get("category", "other"),
        server.get("description", ""),
        json.dumps(tools),
        len(tools) > 0,
    )
    return {"registered": True, "tools": len(tools)}


async def deregister(server_id: str):
    pool = await _get_pool()
    await pool.execute("DELETE FROM mcp_servers WHERE id=$1", server_id)


async def list_tools(server_id: str) -> list[dict]:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT url FROM mcp_servers WHERE id=$1", server_id)
    if not row:
        return []
    return await _fetch_tools(row["url"])


async def invoke(server_id: str, tool: str, args: dict) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT url FROM mcp_servers WHERE id=$1", server_id)
    if not row:
        return {"error": f"Server '{server_id}' not found"}
    try:
        async with httpx.AsyncClient(headers={"x-api-key": get_internal_api_key(), "x-internal-service": "console"}, timeout=120) as client:
            r = await client.post(
                f"{row['url']}/mcp/invoke",
                json={"tool": tool, "args": args},
            )
            r.raise_for_status()
            data = r.json()
            # Unwrap {result: ...} envelope if present
            return data.get("result", data)
    except Exception as exc:
        return {"error": str(exc)}


async def health_check_all() -> int:
    """Re-fetch tools from every registered server and update healthy/tools in DB."""
    pool = await _get_pool()
    rows = await pool.fetch("SELECT id, url FROM mcp_servers")
    for row in rows:
        tools = await _fetch_tools(row["url"])
        await pool.execute(
            """UPDATE mcp_servers
               SET tools=$1::jsonb, healthy=$2, last_seen=NOW()
               WHERE id=$3""",
            json.dumps(tools), len(tools) > 0, row["id"],
        )
    return len(rows)


async def _fetch_tools(url: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(headers={"x-api-key": get_internal_api_key(), "x-internal-service": "console"}, timeout=10) as client:
            r = await client.get(f"{url}/mcp/tools")
            if r.status_code < 400:
                return r.json().get("tools", [])
    except Exception:
        pass
    return []
