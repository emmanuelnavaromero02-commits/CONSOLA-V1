"""
MCP Server Registry — console side
Stores registered MCP servers in postgres and periodically health-checks them.

Contract each MCP server must implement:
  GET  {url}/mcp/tools   → {"tools": [{name, description, input_schema}]}
  POST {url}/mcp/invoke  → body: {tool, args} → {result} | {error}
"""
from __future__ import annotations

import ipaddress
import asyncio
import json
import os
import re
from urllib.parse import urlparse

import asyncpg
import httpx
from fastapi import HTTPException

from app.security import get_internal_api_key
from app.middleware.request_id import request_id_var
from app.services import egress_guard
from app.services.security_context import build_security_context

_pool: asyncpg.Pool | None = None
DATABASE_URL = os.environ.get("DATABASE_URL", "")

ALLOWED_MCP_HOSTS = {
    "hubspot",
    "mcp-infra",
    "replicon",
    "salesforce",
    "sap-hcm",
    "sap-s4hana",
    "sap-successfactors",
    "console",
    "refinement",
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


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _loopback_allowed() -> bool:
    return not _is_production_env()


def _mcp_allowed_private_hosts() -> set[str]:
    hosts = set(ALLOWED_MCP_HOSTS) | _configured_allowed_hosts()
    if _loopback_allowed():
        hosts.update({"localhost"})
    return hosts


def _mcp_allowed_private_cidrs() -> list[str]:
    return [str(cidr) for cidr in _configured_allowed_cidrs()]


def _validate_mcp_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("mcp URL must use http or https")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("mcp URL host is required")
    allowed_hosts = _mcp_allowed_private_hosts()
    allowed_cidrs = _configured_allowed_cidrs()

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host in {"localhost"}:
            if _loopback_allowed():
                egress_guard.validate_url(
                    url,
                    label="mcp URL",
                    allow_private_hosts=allowed_hosts,
                    allow_private_cidrs=_mcp_allowed_private_cidrs(),
                    allow_http_hosts=allowed_hosts,
                )
                return
            raise ValueError("loopback MCP hosts are disabled in production")
        if host not in allowed_hosts:
            raise ValueError(f"mcp host not allowlisted: {host}")
        egress_guard.validate_url(
            url,
            label="mcp URL",
            allow_private_hosts=allowed_hosts,
            allow_private_cidrs=_mcp_allowed_private_cidrs(),
            allow_http_hosts=allowed_hosts,
        )
        return

    if ip.is_loopback:
        if not _loopback_allowed():
            raise ValueError("loopback MCP hosts are disabled in production")
        egress_guard.validate_url(
            url,
            label="mcp URL",
            allow_private_hosts=allowed_hosts,
            allow_private_cidrs=_mcp_allowed_private_cidrs(),
            allow_private=True,
            allow_http_hosts=allowed_hosts,
            require_https_in_prod=False,
        )
        return
    if ip.is_link_local or host.startswith("169.254.") or ip in _BLOCKED_SHARED_ADDRESS_SPACE:
        raise ValueError("metadata/link-local MCP hosts are blocked")
    if not any(ip in cidr for cidr in allowed_cidrs):
        raise ValueError(f"mcp IP host not allowlisted: {host}")
    egress_guard.validate_url(
        url,
        label="mcp URL",
        allow_private_cidrs=_mcp_allowed_private_cidrs(),
        allow_http_hosts={host},
    )


def _enforce_mcp_url(url: str) -> None:
    try:
        _validate_mcp_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _ctx_permissions(ctx: dict) -> set[str]:
    return {str(p) for p in (ctx.get("permissions") or [])}


def _is_admin_context(ctx: dict) -> bool:
    return bool(ctx.get("trusted")) and str(ctx.get("role") or "").lower() in _ADMIN_ROLES


def _is_unscoped_admin_context(ctx: dict) -> bool:
    if not _is_admin_context(ctx):
        return False
    if ctx.get("tenant_id") or ctx.get("workspace_id"):
        return False
    allowed = [str(c).strip() for c in (ctx.get("allowed_cartridges") or [])]
    return "*" in allowed


def _allowed_prefix_matches(ctx: dict, value: str) -> bool:
    """Match explicit prefixes without letting root prefixes grant all data."""
    prefixes = [str(p).lstrip("/").rstrip("/") for p in (ctx.get("allowed_prefixes") or [])]
    for prefix in prefixes:
        if not prefix:
            continue
        if len(prefix.split("/")) < 2:
            continue
        if value == prefix or value.startswith(prefix + "/"):
            return True
    return False


def _prefix_allowed(ctx: dict, value: str) -> bool:
    value = (value or "").lstrip("/")
    if not value:
        return False
    if _has_invalid_scoped_storage_path(ctx, value):
        return False
    if _allowed_prefix_matches(ctx, value):
        return True
    if _is_unscoped_admin_context(ctx):
        return True

    allowed = {
        str(item).strip().strip("/")
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if "*" in allowed:
        return True
    parts = value.strip("/").split("/")
    if len(parts) < 2:
        return False
    root, cartridge = parts[0], parts[1]
    if cartridge not in allowed:
        return False
    if root == "cartridges":
        return True
    if root not in {"raw", "silver", "gold", "uploads"}:
        return False

    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        return True
    if len(parts) <= 3 and root in {"raw", "silver", "gold"}:
        return True
    scoped_marker = f"tenant_id={tenant_id}/workspace_id={workspace_id}"
    normalized = value.rstrip("/")
    return f"/{scoped_marker}/" in f"/{normalized}/" or normalized.endswith(f"/{scoped_marker}")


def _storage_scope_markers(key: str) -> tuple[str | None, str | None]:
    tenant: str | None = None
    workspace: str | None = None
    for part in str(key or "").strip("/").split("/"):
        if part.startswith("tenant_id="):
            tenant = part.split("=", 1)[1]
        elif part.startswith("workspace_id="):
            workspace = part.split("=", 1)[1]
    return tenant, workspace


def _has_tenant_workspace_scope(ctx: dict) -> bool:
    return bool(str(ctx.get("tenant_id") or "").strip() and str(ctx.get("workspace_id") or "").strip())


def _is_physical_storage_key(key: str) -> bool:
    parts = str(key or "").strip("/").split("/")
    if not parts:
        return False
    root = parts[0]
    if root in {"raw", "silver", "gold"}:
        return len(parts) > 3
    if root == "uploads":
        return len(parts) > 2
    return False


def _has_foreign_storage_scope(ctx: dict, key: str) -> bool:
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not (tenant_id and workspace_id):
        return False
    tenant, workspace = _storage_scope_markers(key)
    if tenant is None and workspace is None:
        return False
    return tenant != tenant_id or workspace != workspace_id


def _has_invalid_scoped_storage_path(ctx: dict, key: str) -> bool:
    if not _has_tenant_workspace_scope(ctx) or not _is_physical_storage_key(key):
        return False
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    tenant, workspace = _storage_scope_markers(key)
    return tenant != tenant_id or workspace != workspace_id


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
    if _is_unscoped_admin_context(ctx):
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
    if not (
        key.startswith(f"raw/{cartridge_id}/")
        or key.startswith(f"silver/{cartridge_id}/")
        or key.startswith(f"gold/{cartridge_id}/")
    ):
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
    if tool == "query_kb":
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
        # v1.43.1 (Codex P0-3): register the built-in cartridges so the
        # copilot's tool_manifest sees their /mcp/tools at boot.
        # ``register`` HTTP-fetches /mcp/tools and stores the result in
        # the tools JSONB column, so refreshing the console picks up
        # newly-added tools automatically. Migration 42 also seeds the
        # rows so fresh installs have them even before console boots.
        {
            "id":          "hubspot",
            "name":        "HubSpot CRM",
            "url":         os.environ.get("HUBSPOT_URL", "http://hubspot:8210"),
            "category":    "cartridge",
            "description": "Connector for HubSpot CRM.",
        },
        {
            "id":          "replicon",
            "name":        "Replicon Time & Attendance",
            "url":         os.environ.get("REPLICON_URL", "http://replicon:8201"),
            "category":    "cartridge",
            "description": "Connector for Replicon workforce management platform.",
        },
        {
            "id":          "salesforce",
            "name":        "Salesforce Sales Cloud",
            "url":         os.environ.get("SALESFORCE_URL", "http://salesforce:8205"),
            "category":    "cartridge",
            "description": "Connector for Salesforce Sales Cloud.",
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
    elif server_id in {"monitoring", "studio_ops"} or "console" in url:
        key_env = "INTERNAL_API_KEY_CONSOLE_TO_CONSOLE"
    elif normalized.startswith("SAP_") or server_id in {"replicon", "hubspot", "salesforce"}:
        key_env = "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE"
    pair_key = os.environ.get(key_env) if key_env else None
    if key_env and os.environ.get("APP_ENV", "production").lower() in {"production", "prod"} and not pair_key:
        raise RuntimeError(f"Missing {key_env}; legacy fallback disabled in production")
    headers = {
        "x-api-key": pair_key or get_internal_api_key(),
        "x-internal-service": "console",
    }
    rid = request_id_var.get()
    if rid:
        headers["x-request-id"] = rid
    return headers


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
        raise HTTPException(404, f"Server '{server_id}' not found")
    try:
        _validate_mcp_url(row["url"])
    except ValueError as exc:
        raise HTTPException(403, f"mcp_host_not_allowlisted: {exc}") from exc
    try:
        payload = {"tool": tool, "args": args or {}}
        ctx = security_context if security_context is not None else build_security_context(user)
        if ctx:
            payload["security_context"] = ctx
        _enforce_outbound_scope(server_id, str(row["category"] or ""), tool, args or {}, ctx or {})
        response = await egress_guard.pinned_request(
            "POST",
            f"{str(row['url']).rstrip('/')}/mcp/invoke",
            label="mcp invoke URL",
            headers=_headers_for(server_id, row["url"]),
            json_body=payload,
            max_bytes=10 * 1024 * 1024,
            timeout=120,
            allow_private_hosts=_mcp_allowed_private_hosts(),
            allow_private_cidrs=_mcp_allowed_private_cidrs(),
            allow_http_hosts=_mcp_allowed_private_hosts(),
        )
        if response.is_redirect:
            raise HTTPException(403, "MCP redirects are blocked")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail") or response.text
            except Exception:
                detail = response.text
            status = response.status_code if response.status_code < 500 else 502
            raise HTTPException(status, detail or f"MCP tool failed: {server_id}/{tool}")
        data = response.json()
        return data.get("result", data)
    except HTTPException:
        raise
    except (egress_guard.EgressGuardError, asyncio.TimeoutError) as exc:
        raise HTTPException(403, f"MCP egress blocked: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"MCP tool timeout: {server_id}/{tool}") from exc
    except httpx.HTTPStatusError as exc:
        response = exc.response
        try:
            detail = response.json().get("detail") or response.text
        except Exception:
            detail = response.text
        status = response.status_code if response.status_code < 500 else 502
        raise HTTPException(status, detail or f"MCP tool failed: {server_id}/{tool}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"MCP transport failed: {server_id}/{tool}") from exc
    except Exception as exc:
        raise HTTPException(502, f"MCP invoke failed: {server_id}/{tool} ({type(exc).__name__})") from exc


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
        response = await egress_guard.pinned_request(
            "GET",
            f"{url.rstrip('/')}/mcp/tools",
            label="mcp tools URL",
            headers=_headers_for(server_id or "", url),
            max_bytes=2 * 1024 * 1024,
            timeout=10,
            allow_private_hosts=_mcp_allowed_private_hosts(),
            allow_private_cidrs=_mcp_allowed_private_cidrs(),
            allow_http_hosts=_mcp_allowed_private_hosts(),
        )
        if response.status_code < 400 and not response.is_redirect:
            return response.json().get("tools", [])
    except Exception:
        pass
    return []
