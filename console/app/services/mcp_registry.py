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
import re
from urllib.parse import urlparse

import asyncpg
import httpx
from fastapi import HTTPException

from app.security import get_internal_api_key
from app.services.security_context import build_security_context

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
_ADMIN_ROLES = {"admin", "owner", "super_admin"}
_CARTRIDGE_READ_TOOLS = {
    "list_entities",
    "get_schema",
    "get_semantic",
    "get_manifest",
    "get_run_logs",
    "get_job_status",
    "list_jobs",
    "list_kbs",
}
_CARTRIDGE_DATA_TOOLS = {"preview", "query_kb"}
_CARTRIDGE_EXECUTE_TOOLS = {"extract", "extract_all", "run_kb", "sync_semantic_to_rag"}
_SQL_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_SQL_FORBIDDEN_RE = re.compile(
    r"\b(attach|call|copy|create|delete|drop|export|import|insert|install|load|pragma|set|truncate|update|alter)\b",
    re.IGNORECASE,
)
_SQL_COMMENT_RE = re.compile(r"(--|/\*)")
_SQL_READER_CALL_RE = re.compile(
    r"\b(read_parquet|read_csv|read_json|read_ndjson|parquet_scan|csv_scan|csv_auto|json_scan|read_blob)\s*\(",
    re.IGNORECASE,
)
_SCOPED_READER_RE = re.compile(
    r"\b(read_parquet|read_csv)\s*\(\s*(['\"])(.*?)\2",
    re.IGNORECASE | re.DOTALL,
)
_SQL_STORAGE_LITERAL_RE = re.compile(r"(['\"])(s3://.*?)(?<!\\)\1", re.IGNORECASE | re.DOTALL)
_DIRECT_STORAGE_SCAN_RE = re.compile(
    r"\b(?:from|join|table)\s+(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
_DUCKDB_SCHEMA_RE = re.compile(r'(?<![A-Za-z0-9_])"?(pgdb|pggold)"?\s*\.', re.IGNORECASE)


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


def _ctx_permissions(ctx: dict) -> set[str]:
    return {str(p) for p in (ctx.get("permissions") or [])}


def _is_admin_context(ctx: dict) -> bool:
    return bool(ctx.get("trusted")) and str(ctx.get("role") or "").lower() in _ADMIN_ROLES


def _prefix_allowed(ctx: dict, value: str) -> bool:
    value = (value or "").lstrip("/")
    prefixes = [str(p).lstrip("/") for p in (ctx.get("allowed_prefixes") or [])]
    return bool(value) and any(value.startswith(prefix) for prefix in prefixes)


def _require_context(ctx: dict, *permissions: str) -> None:
    if not ctx.get("trusted"):
        raise PermissionError("trusted security_context required")
    available = _ctx_permissions(ctx)
    if not any(permission in available for permission in permissions):
        raise PermissionError(f"permission required: {' or '.join(permissions)}")


def _require_cartridge_scope(ctx: dict, cartridge_id: str) -> None:
    cartridge_id = str(cartridge_id or "").strip()
    if not cartridge_id:
        raise PermissionError("cartridge_id is required")
    if _is_admin_context(ctx):
        return
    allowed = [str(c).strip() for c in (ctx.get("allowed_cartridges") or [])]
    if "*" in allowed or cartridge_id in allowed:
        return
    if (
        _prefix_allowed(ctx, f"raw/{cartridge_id}/")
        or _prefix_allowed(ctx, f"silver/{cartridge_id}/")
        or _prefix_allowed(ctx, f"gold/{cartridge_id}/")
        or _prefix_allowed(ctx, f"cartridges/{cartridge_id}/")
    ):
        return
    raise PermissionError("cartridge not allowed")


def _mask_single_quoted(sql: str) -> str:
    return _SINGLE_QUOTED_RE.sub("''", sql or "")


def _reader_storage_key(path: str) -> str:
    path = (path or "").strip()
    if not path.startswith("s3://"):
        raise PermissionError("cartridge SQL readers must use s3:// paths")
    rest = path[5:]
    key = rest.split("/", 1)[1] if "/" in rest else ""
    if not key or ".." in key.split("/"):
        raise PermissionError("cartridge SQL path is not allowed")
    return key


def _require_direct_cartridge_sql_path(ctx: dict, cartridge_id: str, path: str) -> None:
    key = _reader_storage_key(path)
    if not (key.startswith(f"raw/{cartridge_id}/") or key.startswith(f"silver/{cartridge_id}/")):
        raise PermissionError("cartridge SQL must stay inside its cartridge prefix")
    if not _prefix_allowed(ctx, key):
        raise PermissionError("cartridge SQL path not allowed")


def _validate_direct_cartridge_sql(ctx: dict, cartridge_id: str, sql: str) -> None:
    sql = sql or ""
    masked = _mask_single_quoted(sql)
    if not _SQL_START_RE.search(masked):
        raise PermissionError("cartridge SQL must be read-only SELECT/WITH")
    if ";" in masked or _SQL_COMMENT_RE.search(masked) or _SQL_FORBIDDEN_RE.search(masked):
        raise PermissionError("cartridge SQL contains unsafe statements or comments")
    if _DUCKDB_SCHEMA_RE.search(masked):
        raise PermissionError("cartridge SQL cannot read service database schemas")
    reader_calls = list(_SQL_READER_CALL_RE.finditer(sql))
    direct_readers = list(_SCOPED_READER_RE.finditer(sql))
    if not direct_readers or len(reader_calls) != len(direct_readers):
        raise PermissionError("cartridge SQL must read only direct s3:// file literals")
    for match in direct_readers:
        _require_direct_cartridge_sql_path(ctx, cartridge_id, match.group(3))
    for match in _SQL_STORAGE_LITERAL_RE.finditer(sql):
        _require_direct_cartridge_sql_path(ctx, cartridge_id, match.group(2))
    for match in _DIRECT_STORAGE_SCAN_RE.finditer(sql):
        _require_direct_cartridge_sql_path(ctx, cartridge_id, match.group(2))


def _enforce_outbound_scope(server_id: str, category: str, tool: str, args: dict, ctx: dict) -> None:
    if category != "cartridge":
        return
    if tool in _CARTRIDGE_EXECUTE_TOOLS:
        _require_context(ctx, "cartridges.execute", "pipelines.run")
    elif tool in _CARTRIDGE_DATA_TOOLS:
        _require_context(ctx, "datasets.read")
    elif tool in _CARTRIDGE_READ_TOOLS:
        _require_context(ctx, "cartridges.read")
    else:
        _require_context(ctx, "cartridges.read")

    _require_cartridge_scope(ctx, server_id)
    if tool == "query_kb" and not _is_admin_context(ctx):
        _validate_direct_cartridge_sql(ctx, server_id, str((args or {}).get("sql") or ""))


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
    tools = await _fetch_tools(server["url"], server.get("id"))
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
    return await _fetch_tools(row["url"], server_id)


def _headers_for(server_id: str, url: str) -> dict[str, str]:
    key_env = ""
    normalized = server_id.replace("-", "_").upper()
    if server_id in {"infra", "mcp-infra"} or "mcp-infra" in url:
        key_env = "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA"
    elif server_id == "refinement" or "refinement" in url:
        key_env = "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT"
    elif normalized.startswith("SAP_") or server_id == "replicon":
        key_env = "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE"
    pair_key = os.environ.get(key_env) if key_env else None
    if key_env and os.environ.get("APP_ENV", "production").lower() in {"production", "prod"} and not pair_key:
        raise RuntimeError(f"Missing {key_env}; legacy fallback disabled in production")
    return {
        "x-api-key": pair_key or get_internal_api_key(),
        "x-internal-service": "console",
    }


async def invoke(
    server_id: str,
    tool: str,
    args: dict,
    *,
    user: dict | None = None,
    security_context: dict | None = None,
) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT url, category FROM mcp_servers WHERE id=$1", server_id)
    if not row:
        return {"error": f"Server '{server_id}' not found"}
    try:
        _validate_mcp_url(row["url"])
    except ValueError as exc:
        return {"error": "mcp_host_not_allowlisted", "detail": str(exc)}
    try:
        payload = {"tool": tool, "args": args or {}}
        ctx = security_context if security_context is not None else build_security_context(user)
        if ctx:
            payload["security_context"] = ctx
        _enforce_outbound_scope(server_id, str(row["category"] or ""), tool, args or {}, ctx or {})
        async with httpx.AsyncClient(headers=_headers_for(server_id, row["url"]), timeout=120) as client:
            r = await client.post(
                f"{row['url']}/mcp/invoke",
                json=payload,
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
            tools = await _fetch_tools(row["url"], row["id"])
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


async def _fetch_tools(url: str, server_id: str | None = None) -> list[dict]:
    _validate_mcp_url(url)
    try:
        async with httpx.AsyncClient(headers=_headers_for(server_id or "", url), timeout=10) as client:
            r = await client.get(f"{url}/mcp/tools")
            if r.status_code < 400:
                return r.json().get("tools", [])
    except Exception:
        pass
    return []
