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
from urllib.parse import urlparse

import asyncpg
import httpx
from fastapi import HTTPException

from app.security import get_internal_api_key

_pool: asyncpg.Pool | None = None
DATABASE_URL = os.environ.get("DATABASE_URL", "")

ALLOWED_MCP_HOSTS = {
    "mcp-infra",
    "replicon",
    "sap-hcm",
    "sap-s4hana",
    "sap-successfactors",
    "console",
    "refinement",
    "127.0.0.1",
    "::1",
    "localhost",
}
_BLOCKED_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def _configured_allowed_hosts() -> set[str]:
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "")
    return {h.strip().rstrip(".").lower() for h in raw.split(",") if h.strip()}


def _configured_allowed_cidrs() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    raw = os.environ.get("MCP_ALLOWED_CIDRS", "")
    for item in (p.strip() for p in raw.split(",")):
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _validate_mcp_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("mcp URL must use http or https")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("mcp URL host is required")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host not in ALLOWED_MCP_HOSTS and host not in _configured_allowed_hosts():
            raise ValueError(f"mcp host not allowlisted: {host}")
        return

    if host in ALLOWED_MCP_HOSTS and ip.is_loopback:
        return
    if ip.is_link_local or host.startswith("169.254.") or ip in _BLOCKED_SHARED_ADDRESS_SPACE:
        raise ValueError("metadata/link-local MCP hosts are blocked")
    if any(ip in cidr for cidr in _configured_allowed_cidrs()):
        return
    if ip.is_private or ip.is_global or ip.is_reserved or ip.is_multicast:
        raise ValueError(f"mcp IP host not allowlisted: {host}")


def _enforce_mcp_url(url: str) -> None:
    try:
        _validate_mcp_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured (mcp_registry)")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def startup():
    """Register built-in servers from environment at app startup."""
    console_url = os.environ.get("CONSOLE_INTERNAL_URL", "http://console:8000").rstrip("/")
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
        # v1.43.1 (Codex P0-3): register the 4 cartridges so the
        # copilot's tool_manifest sees their /mcp/tools at boot.
        # ``register`` HTTP-fetches /mcp/tools and stores the result in
        # the tools JSONB column, so refreshing the console picks up
        # newly-added tools automatically. Migration 42 also seeds the
        # rows so fresh installs have them even before console boots.
        {
            "id":          "replicon",
            "name":        "Replicon Time & Attendance",
            "url":         os.environ.get("REPLICON_URL", "http://replicon:8201"),
            "category":    "cartridge",
            "description": "Connector for Replicon workforce management platform.",
        },
        {
            "id":          "sap_hcm",
            "name":        "SAP HCM Core",
            "url":         os.environ.get("SAP_HCM_URL", "http://sap-hcm:8202"),
            "category":    "cartridge",
            "description": "Connector for SAP HCM on-premise / S4HANA HCM.",
        },
        {
            "id":          "sap_successfactors",
            "name":        "SAP SuccessFactors",
            "url":         os.environ.get("SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"),
            "category":    "cartridge",
            "description": "Connector for SAP SuccessFactors Employee Central.",
        },
        {
            "id":          "sap_s4hana",
            "name":        "SAP S/4HANA",
            "url":         os.environ.get("SAP_S4HANA_URL", "http://sap-s4hana:8204"),
            "category":    "cartridge",
            "description": "Connector for SAP S/4HANA modules.",
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


async def register(server: dict) -> dict:
    _enforce_mcp_url(server["url"])
    pool = await _get_pool()
    tools = await _fetch_tools(server["url"])
    tool_count = len(tools)
    await pool.execute("""
        INSERT INTO mcp_servers (id, name, url, category, description, tools, tool_count, healthy, last_seen)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,NOW())
        ON CONFLICT (id) DO UPDATE SET
            name=$2, url=$3, category=$4, description=$5,
            tools=$6::jsonb, tool_count=$7, healthy=$8, last_seen=NOW()
    """,
        server["id"],
        server["name"],
        server["url"],
        server.get("category", "other"),
        server.get("description", ""),
        json.dumps(tools),
        tool_count,
        tool_count > 0,
    )
    return {"registered": True, "tools": tool_count, "tool_count": tool_count}


async def deregister(server_id: str):
    pool = await _get_pool()
    await pool.execute("DELETE FROM mcp_servers WHERE id=$1", server_id)


async def list_tools(server_id: str) -> list[dict]:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT url FROM mcp_servers WHERE id=$1", server_id)
    if not row:
        return []
    try:
        _validate_mcp_url(row["url"])
    except ValueError:
        return []
    return await _fetch_tools(row["url"])


async def invoke(server_id: str, tool: str, args: dict) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT url FROM mcp_servers WHERE id=$1", server_id)
    if not row:
        return {"error": f"Server '{server_id}' not found"}
    try:
        _validate_mcp_url(row["url"])
    except ValueError as exc:
        return {"error": "mcp_host_not_allowlisted", "detail": str(exc)}
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
        try:
            _validate_mcp_url(row["url"])
            tools = await _fetch_tools(row["url"])
        except ValueError:
            tools = []
        tool_count = len(tools)
        await pool.execute(
            """UPDATE mcp_servers
               SET tools=$1::jsonb, tool_count=$2, healthy=$3, last_seen=NOW()
               WHERE id=$4""",
            json.dumps(tools), tool_count, tool_count > 0, row["id"],
        )
    return len(rows)


async def _fetch_tools(url: str) -> list[dict]:
    _validate_mcp_url(url)
    try:
        async with httpx.AsyncClient(headers={"x-api-key": get_internal_api_key(), "x-internal-service": "console"}, timeout=10) as client:
            r = await client.get(f"{url}/mcp/tools")
            if r.status_code < 400:
                return r.json().get("tools", [])
    except Exception:
        pass
    return []
