"""
MCP Infrastructure Server
=========================
Single FastAPI service that exposes tools for Airflow, MinIO, PostgreSQL and Superset
via the standard MCP contract:

  GET  /mcp/tools          → { tools: [{name, description, input_schema}] }
  POST /mcp/invoke         → { tool, args } → { result } | { error }
  GET  /health             → { status }   (public; tool count intentionally redacted)
"""
from __future__ import annotations
import json
import os
import re
import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel

from app import registry
from app.security import get_internal_api_key
# Sprint v1.41.1 — structured JSON logs so request_id correlates here too.
from app.logging_config import setup_logging  # noqa: E402

setup_logging(service_name="mcp-infra")

# ── Import tool modules so decorators register themselves ──────────────────────
import app.tools.airflow     # noqa: F401
import app.tools.admin_request  # noqa: F401
import app.tools.agents      # noqa: F401
import app.tools.cartridges  # noqa: F401
import app.tools.minio       # noqa: F401
import app.tools.pipeline    # noqa: F401
import app.tools.postgres    # noqa: F401
import app.tools.rag         # noqa: F401
import app.tools.superset    # noqa: F401
import app.tools.vault       # noqa: F401

# ── App ────────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager
from app.rag.store import get_pool as _rag_get_pool


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        await _rag_get_pool()
    except Exception:
        pass  # RAG is optional — server starts even if pgvector is not ready
    yield

INTERNAL_API_KEY = get_internal_api_key()  # legacy fallback, still accepted
_RAG_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}

# Sprint v1.12: mcp-infra is called by console, workspace and airflow. Each
# pair has its own INTERNAL_API_KEY_*_TO_MCP_INFRA secret. The legacy shared
# key keeps working during the migration window and is dropped in a follow-up.
_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "console":   "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
    "workspace": "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
    "airflow":   "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
    "refinement": "INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA",
    # The old whitelist allowed this; we keep it via legacy key only outside prod.
    "mcp-infra":  None,
}


def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    # v1.42.1 auditor fix: distinguish "no auth presented" (401) from
    # "auth presented but invalid" (403). Matches the cartridge pattern
    # in app/api/deps.py and the wider HTTP convention.
    if not x_internal_service or not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )
    if x_internal_service not in _ALLOWED_SERVICES_TO_KEY_ENV:
        raise HTTPException(
            status_code=403,
            detail="Invalid internal service origin",
        )

    accepted: list[str] = []
    pair_key_env = _ALLOWED_SERVICES_TO_KEY_ENV.get(x_internal_service)
    if pair_key_env:
        pair_key = os.environ.get(pair_key_env)
        if pair_key:
            accepted.append(pair_key)
    if INTERNAL_API_KEY and not _is_production():
        accepted.append(INTERNAL_API_KEY)

    if not any(secrets.compare_digest(x_api_key, k) for k in accepted if k):
        raise HTTPException(status_code=403, detail="Forbidden")
    return x_internal_service


def _safe_rag_segment(value: str, label: str) -> str:
    value = (value or "").strip()
    if not _RAG_SEGMENT_RE.fullmatch(value):
        raise HTTPException(400, f"invalid {label}")
    return value

app = FastAPI(
    title="ΩMEGA by EPIUSE MCP Infra",
    description="MCP tools for Airflow, MinIO, PostgreSQL, Superset, RAG",
    version="1.1.0",
    lifespan=_lifespan,
)

# Sprint v1.41.1 — correlation IDs.
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)


class InvokeRequest(BaseModel):
    tool: str
    args: dict = {}
    security_context: dict[str, Any] | None = None


_DATA_READ_TOOLS = {
    "postgres_list_schemas",
    "postgres_list_tables",
    "postgres_get_table_schema",
    "postgres_get_sample",
    "postgres_describe_table",
    "postgres_select",
    "postgres_execute_query",
    "minio_list_objects",
    "minio_get_parquet_schema",
    "minio_get_sample_rows",
}
_DATA_WRITE_TOOLS = {
    "postgres_execute_ddl",
    "minio_upload_spec",
}
_RAG_READ_TOOLS = {"search_rag", "list_rag_sources"}
_RAG_WRITE_TOOLS = {"ingest_document"}
_CARTRIDGE_READ_TOOLS = {
    "minio_list_cartridge_specs",
    "minio_read_spec",
    "cartridge_get_semantic",
    "cartridge_search_term",
    "cartridge_get_manifest",
    "cartridge_list_entities",
    "cartridge_get_schema",
    "cartridge_get_run_logs",
    "cartridge_get_job_status",
    "cartridge_list_jobs",
    "cartridge_list_kbs",
}
_CARTRIDGE_DATA_TOOLS = {"cartridge_preview", "cartridge_query_kb"}
_RUN_ID_SCOPED_TOOLS = {"cartridge_get_run_logs", "cartridge_get_job_status"}
_CARTRIDGE_EXECUTE_TOOLS = {
    "cartridge_sync_semantic_to_rag",
    "cartridge_extract",
    "cartridge_extract_all",
    "cartridge_run_kb",
}
_AIRFLOW_READ_TOOLS = {"airflow_list_dags", "airflow_get_run_status", "airflow_get_task_logs", "airflow_list_task_instances", "airflow_list_dag_runs"}
_AIRFLOW_RUN_TOOLS = {"airflow_trigger_dag"}
_AIRFLOW_WRITE_TOOLS = {"airflow_create_dag", "airflow_delete_dag", "airflow_set_variable"}
_PIPELINE_READ_TOOLS = {"dag_get_source", "watermark_get"}
_PIPELINE_WRITE_TOOLS = {"dag_save_source", "watermark_set"}
_ADMIN_ROLES = {"admin", "owner", "super_admin"}
_SECURITY_SOURCE_BY_SERVICE = {
    "console": {"console", "agent_runner"},
    "workspace": {"workspace"},
    "airflow": {"airflow", "agent_runner"},
    "refinement": {"refinement"},
    "mcp-infra": {"mcp-infra"},
}
_SENSITIVE_TABLES = {
    "users",
    "user_sessions",
    "refresh_tokens",
    "user_tokens",
    "system_settings",
    "audit_events",
    "login_attempts",
    "password_reset_tokens",
    "activation_tokens",
}
_PUBLIC_METADATA_TABLES = {
    "cartridges",
    "entity_config",
    "cartridge_dags",
    "semantic_terms",
    "data_catalog",
    "data_relationships",
    "mcp_servers",
    "datasets",
}
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
_POSTGRES_FROM_CLAUSE_RE = re.compile(
    r"\bfrom\b(?P<body>.*?)(?:\bwhere\b|\bgroup\b|\border\b|\blimit\b|\boffset\b|\breturning\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_POSTGRES_TABLE_REF_RE = re.compile(
    r'^\s*(?:(?:"[A-Za-z_][A-Za-z0-9_]*"|[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*)?"?([A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)


def _ctx(req: InvokeRequest, internal_service: str | None = None) -> dict[str, Any]:
    ctx = req.security_context or {}
    if not isinstance(ctx, dict):
        return {}
    if ctx.get("trusted") and internal_service:
        allowed_sources = _SECURITY_SOURCE_BY_SERVICE.get(internal_service, {internal_service})
        if str(ctx.get("source") or "") not in allowed_sources:
            return {}
    return ctx


def _is_admin_context(ctx: dict[str, Any]) -> bool:
    return bool(ctx.get("trusted")) and str(ctx.get("role") or "").lower() in _ADMIN_ROLES


def _require_context_permission(req: InvokeRequest, permission: str, internal_service: str | None = None) -> dict[str, Any]:
    ctx = _ctx(req, internal_service)
    if not ctx.get("trusted"):
        raise HTTPException(403, detail="trusted security_context required")
    if permission not in set(ctx.get("permissions") or []):
        raise HTTPException(403, detail=f"permission required: {permission}")
    return ctx


def _prefix_allowed(ctx: dict[str, Any], value: str) -> bool:
    value = (value or "").lstrip("/")
    if not value:
        return True
    prefixes = [str(p).lstrip("/") for p in (ctx.get("allowed_prefixes") or [])]
    return any(value.startswith(p) for p in prefixes)


def _require_cartridge_scope(ctx: dict[str, Any], cartridge_id: str) -> None:
    cartridge_id = str(cartridge_id or "").strip()
    if not cartridge_id:
        raise HTTPException(403, detail="cartridge_id is required")
    if _is_admin_context(ctx):
        return
    if not (
        _prefix_allowed(ctx, f"raw/{cartridge_id}/")
        or _prefix_allowed(ctx, f"silver/{cartridge_id}/")
        or _prefix_allowed(ctx, f"gold/{cartridge_id}/")
        or _prefix_allowed(ctx, f"cartridges/{cartridge_id}/")
    ):
        raise HTTPException(403, detail="cartridge not allowed")


def _extract_s3_keys(sql: str) -> list[str]:
    keys: list[str] = []
    for match in re.finditer(r"s3://([^'\"\s)]+)", sql or "", re.IGNORECASE):
        rest = match.group(1)
        keys.append(rest.split("/", 1)[1] if "/" in rest else "")
    return keys


def _mask_single_quoted(sql: str) -> str:
    return _SINGLE_QUOTED_RE.sub("''", sql or "")


def _reader_storage_key(path: str) -> str:
    path = (path or "").strip()
    if not path.startswith("s3://"):
        raise HTTPException(403, detail="cartridge SQL readers must use s3:// paths")
    rest = path[5:]
    key = rest.split("/", 1)[1] if "/" in rest else ""
    if not key or ".." in key.split("/"):
        raise HTTPException(403, detail="cartridge SQL path is not allowed")
    return key


def _require_cartridge_sql_path(ctx: dict[str, Any], cartridge_id: str, path: str) -> None:
    key = _reader_storage_key(path)
    if not (key.startswith(f"raw/{cartridge_id}/") or key.startswith(f"silver/{cartridge_id}/")):
        raise HTTPException(403, detail="cartridge SQL must stay inside its cartridge prefix")
    if not _prefix_allowed(ctx, key):
        raise HTTPException(403, detail="cartridge SQL path not allowed")


def _validate_cartridge_query_sql(ctx: dict[str, Any], cartridge_id: str, sql: str) -> None:
    sql = sql or ""
    masked = _mask_single_quoted(sql)
    if not _SQL_START_RE.search(masked):
        raise HTTPException(403, detail="cartridge SQL must be read-only SELECT/WITH")
    if ";" in masked or _SQL_COMMENT_RE.search(masked) or _SQL_FORBIDDEN_RE.search(masked):
        raise HTTPException(403, detail="cartridge SQL contains unsafe statements or comments")
    if _DUCKDB_SCHEMA_RE.search(masked):
        raise HTTPException(403, detail="cartridge SQL cannot read service database schemas")

    reader_calls = list(_SQL_READER_CALL_RE.finditer(sql))
    direct_readers = list(_SCOPED_READER_RE.finditer(sql))
    if not direct_readers or len(reader_calls) != len(direct_readers):
        raise HTTPException(403, detail="cartridge SQL must read only direct s3:// file literals")
    for match in direct_readers:
        _require_cartridge_sql_path(ctx, cartridge_id, match.group(3))
    for match in _SQL_STORAGE_LITERAL_RE.finditer(sql):
        _require_cartridge_sql_path(ctx, cartridge_id, match.group(2))
    for match in _DIRECT_STORAGE_SCAN_RE.finditer(sql):
        _require_cartridge_sql_path(ctx, cartridge_id, match.group(2))


def _postgres_mentioned_tables(sql: str, table: str = "") -> set[str]:
    masked = _mask_single_quoted(sql or "").lower()
    mentioned = {
        (match[0] or match[1]).lower()
        for match in re.findall(
            r"\b(?:from|join)\s+(?:\"([a-zA-Z_][a-zA-Z0-9_]*)\"|([a-zA-Z_][a-zA-Z0-9_]*))",
            masked,
        )
    }
    if table:
        mentioned.add(table.strip().strip('"').lower())
    for clause in _POSTGRES_FROM_CLAUSE_RE.finditer(masked):
        body = clause.group("body") or ""
        for part in body.split(",")[1:]:
            match = _POSTGRES_TABLE_REF_RE.search(part)
            if match:
                mentioned.add(match.group(1).lower())
    return mentioned


def _rag_source_allowed(ctx: dict[str, Any], name: str) -> bool:
    if _is_admin_context(ctx):
        return True
    name = str(name or "")
    if name.startswith("raw:"):
        parts = name.split(":", 2)
        return len(parts) > 1 and _prefix_allowed(ctx, f"raw/{parts[1]}/")
    if name.startswith("_semantic_"):
        cartridge = name.removeprefix("_semantic_")
        return (
            _prefix_allowed(ctx, f"raw/{cartridge}/")
            or _prefix_allowed(ctx, f"silver/{cartridge}/")
            or _prefix_allowed(ctx, f"gold/{cartridge}/")
        )
    if name.startswith("dataset:"):
        dataset = name.split(":", 1)[1]
        cartridge = _cartridge_of(dataset)
        return bool(cartridge and (
            _prefix_allowed(ctx, f"silver/{cartridge}/{dataset}/")
            or _prefix_allowed(ctx, f"gold/{cartridge}/{dataset}/")
        ))
    return False


def _cartridge_for_run_id(run_id: str) -> str | None:
    import psycopg2
    from app.config import settings as s

    try:
        conn = psycopg2.connect(
            host=s.pg_host,
            port=s.pg_port,
            dbname=s.pg_db,
            user=s.pg_user,
            password=s.pg_password,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT cartridge_id FROM pipeline_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _filter_rag_payload(payload: Any, ctx: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    if isinstance(out.get("sources"), list):
        out["sources"] = [s for s in out["sources"] if _rag_source_allowed(ctx, s.get("name") if isinstance(s, dict) else "")]
    if isinstance(out.get("results"), list):
        out["results"] = [
            r for r in out["results"]
            if _rag_source_allowed(ctx, r.get("source_name") if isinstance(r, dict) else "")
        ]
    return out


def _enforce_data_scope(req: InvokeRequest, internal_service: str | None = None) -> dict[str, Any] | None:
    tool = req.tool
    args = req.args or {}
    if tool in _DATA_WRITE_TOOLS:
        ctx = _require_context_permission(req, "datasets.write", internal_service)
    elif tool in _RAG_WRITE_TOOLS:
        ctx = _require_context_permission(req, "datasets.write", internal_service)
    elif tool in _RAG_READ_TOOLS:
        ctx = _require_context_permission(req, "datasets.read", internal_service)
    elif tool in _CARTRIDGE_READ_TOOLS:
        ctx = _require_context_permission(req, "cartridges.read", internal_service)
    elif tool in _CARTRIDGE_EXECUTE_TOOLS:
        ctx = _require_context_permission(req, "cartridges.execute", internal_service)
    elif tool in _CARTRIDGE_DATA_TOOLS:
        ctx = _require_context_permission(req, "datasets.read", internal_service)
    elif tool in _DATA_READ_TOOLS:
        ctx = _require_context_permission(req, "datasets.read", internal_service)
    elif tool in _AIRFLOW_READ_TOOLS:
        ctx = _require_context_permission(req, "pipelines.read", internal_service)
    elif tool in _AIRFLOW_RUN_TOOLS:
        ctx = _require_context_permission(req, "pipelines.run", internal_service)
    elif tool in _AIRFLOW_WRITE_TOOLS:
        ctx = _require_context_permission(req, "pipelines.write", internal_service)
    elif tool in _PIPELINE_READ_TOOLS:
        ctx = _require_context_permission(req, "pipelines.read", internal_service)
    elif tool in _PIPELINE_WRITE_TOOLS:
        ctx = _require_context_permission(req, "pipelines.write", internal_service)
    else:
        return None

    if tool.startswith("postgres_") and not _is_admin_context(ctx):
        gold = bool(args.get("gold"))
        schema = str(args.get("schema") or "public").strip().strip('"').lower()
        table = str(args.get("table") or "").strip().strip('"').lower()
        sql = str(args.get("sql") or "").lower()
        if schema in {"pg_catalog", "information_schema"}:
            raise HTTPException(403, detail="system schemas are not readable through MCP")
        if table in _SENSITIVE_TABLES or any(re.search(rf"\b{name}\b", sql) for name in _SENSITIVE_TABLES):
            raise HTTPException(403, detail="sensitive internal tables are not readable through MCP")
        if gold:
            raise HTTPException(403, detail="direct gold SQL requires admin context; use refinement query_dataset")
        if not gold and tool not in {"postgres_execute_ddl"}:
            mentioned = _postgres_mentioned_tables(sql, table)
            if tool in {"postgres_list_schemas", "postgres_list_tables"}:
                raise HTTPException(403, detail="main database discovery requires admin context")
            if mentioned and not mentioned.issubset(_PUBLIC_METADATA_TABLES):
                raise HTTPException(403, detail="main database read is outside approved metadata scope")

    if tool.startswith("minio_"):
        bucket = args.get("bucket")
        allowed_buckets = set(ctx.get("allowed_buckets") or [])
        if bucket and allowed_buckets and bucket not in allowed_buckets:
            raise HTTPException(403, detail="bucket not allowed")
        path = args.get("prefix") or args.get("object_path") or ""
        if tool == "minio_list_objects" and not path and not _is_admin_context(ctx):
            raise HTTPException(403, detail="object prefix is required")
        if not _prefix_allowed(ctx, str(path)):
            raise HTTPException(403, detail="object prefix not allowed")
        cartridge_id = args.get("cartridge_id")
        if cartridge_id and not _prefix_allowed(ctx, f"cartridges/{cartridge_id}/"):
            raise HTTPException(403, detail="cartridge not allowed")

    if tool in _CARTRIDGE_DATA_TOOLS:
        cartridge_id = str(args.get("cartridge_id") or "").strip()
        _require_cartridge_scope(ctx, cartridge_id)
        if tool == "cartridge_query_kb" and not _is_admin_context(ctx):
            _validate_cartridge_query_sql(ctx, cartridge_id, str(args.get("sql") or ""))

    if tool in _RUN_ID_SCOPED_TOOLS:
        cartridge = _cartridge_for_run_id(str(args.get("run_id") or ""))
        if not cartridge:
            raise HTTPException(403, detail="run_id not found or not allowed")
        _require_cartridge_scope(ctx, cartridge)
    elif tool in _CARTRIDGE_READ_TOOLS | _CARTRIDGE_EXECUTE_TOOLS:
        _require_cartridge_scope(ctx, str(args.get("cartridge_id") or args.get("id") or ""))

    return ctx


# ── MCP endpoints ──────────────────────────────────────────────────────────────

@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
def get_tools():
    return {"tools": registry.list_tools()}


@app.post("/mcp/invoke")
async def invoke_tool(req: InvokeRequest, internal_service: str = Depends(verify_api_key)):
    ctx = _enforce_data_scope(req, internal_service)
    try:
        result = await registry.invoke(req.tool, req.args)
        if req.tool in _RAG_READ_TOOLS and ctx is not None:
            result = _filter_rag_payload(result, ctx)
        return {"result": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        # Return structured error so the LLM can reason about it
        return {"error": str(exc), "tool": req.tool}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    Same contract as the legacy /health below — minimal payload, no
    auth — but uses the /healthz path the rest of the platform pins
    its healthchecks on."""
    return {"ok": True, "service": "mcp-infra"}


@app.get("/health")
def health():
    # Public endpoint used by Docker healthchecks and load balancers — keep
    # the response minimal so unauthenticated callers can't fingerprint how
    # many tools / integrations this instance has loaded. Internal callers
    # that need that detail use GET /mcp/tools behind x-api-key.
    return {"status": "ok"}


# ── RAG REST endpoints (used by Studio UI) ─────────────────────────────────────

import io as _io
from app.rag.store import list_sources as _rag_list_sources, delete_source as _rag_delete_source
from app.tools.rag import _do_ingest as _rag_do_ingest, _do_search as _rag_do_search


def _rest_security_context(
    internal_service: str,
    body: dict | None = None,
    header_value: str | None = None,
    *,
    allow_refinement_system: bool = False,
) -> dict[str, Any]:
    if allow_refinement_system and internal_service == "refinement":
        return {
            "trusted": True,
            "source": "refinement",
            "role": "admin",
            "permissions": ["datasets.read", "datasets.write", "cartridges.read"],
            "allowed_buckets": [os.environ.get("MINIO_BUCKET", "lakehouse")],
            "allowed_prefixes": ["raw/", "silver/", "gold/", "cartridges/"],
            "_trusted_admin": True,
        }
    sec = (body or {}).get("security_context")
    if not isinstance(sec, dict) and header_value:
        try:
            sec = json.loads(header_value)
        except Exception:
            sec = {}
    fake_req = InvokeRequest(tool="search_rag", args={}, security_context=sec if isinstance(sec, dict) else {})
    return _require_context_permission(fake_req, "datasets.read", internal_service)


@app.get("/rag/sources")
async def rag_rest_list_sources(
    kinds: str | None = None,
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    ctx = _rest_security_context(internal_service, header_value=x_security_context)
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    sources = await _rag_list_sources(kinds=kind_list)
    return _filter_rag_payload({"sources": sources}, ctx)


@app.delete("/rag/sources/{source_id}")
async def rag_rest_delete_source(
    source_id: int,
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    fake_req = InvokeRequest(
        tool="ingest_document",
        args={},
        security_context=json.loads(x_security_context or "{}") if x_security_context else {},
    )
    ctx = _require_context_permission(fake_req, "datasets.write", internal_service)
    sources = await _rag_list_sources()
    source = next((s for s in sources if int(s.get("id") or 0) == source_id), None)
    if not source:
        raise HTTPException(404, "Source not found")
    if not _rag_source_allowed(ctx, str(source.get("name") or "")):
        raise HTTPException(403, "RAG source is outside caller scope")
    ok = await _rag_delete_source(source_id)
    if not ok:
        raise HTTPException(404, "Source not found")
    return {"deleted": True, "source_id": source_id}


@app.post("/rag/search")
async def rag_rest_search(body: dict, internal_service: str = Depends(verify_api_key)):
    ctx = _rest_security_context(internal_service, body)
    result = {"results": await _rag_do_search(
        query=body["query"],
        top_k=body.get("top_k", 5),
        source_ids=body.get("source_ids"),
        kinds=body.get("kinds"),
    )}
    return _filter_rag_payload(result, ctx)


@app.post("/rag/ingest")
async def rag_rest_ingest(body: dict, internal_service: str = Depends(verify_api_key)):
    fake_req = InvokeRequest(tool="ingest_document", args={}, security_context=body.get("security_context") or {})
    ctx = _require_context_permission(fake_req, "datasets.write", internal_service)
    if not _rag_source_allowed(ctx, str(body.get("name") or "")):
        raise HTTPException(403, "RAG source is outside caller scope")
    content = body.get("content", "")
    if body.get("mime_type") == "application/pdf":
        import base64
        from pypdf import PdfReader
        pdf_bytes = base64.b64decode(body["content"])
        reader = PdfReader(_io.BytesIO(pdf_bytes))
        content = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if not content:
            raise HTTPException(400, "Could not extract text from PDF")
    return await _rag_do_ingest(
        name=body["name"],
        content=content,
        description=body.get("description", ""),
        mime_type=body.get("mime_type", "text/plain"),
        kind=body.get("kind", "document"),
    )


# ── Re-index a single raw entity or dataset into the RAG ────────────────────

@app.post("/rag/reindex")
async def rag_rest_reindex(body: dict, internal_service: str = Depends(verify_api_key)):
    """
    Re-read the schema (and SQL for datasets) live, then re-ingest into the
    RAG so the assistant sees current columns. Idempotent — the source name
    gets ON CONFLICT replaced.

    Body:
      {kind: "raw" | "dataset", name: "...", cartridge: "replicon" (raw only)}
    """
    kind = (body.get("kind") or "").lower()
    raw_name = (body.get("name") or "").strip()
    if kind not in ("raw", "dataset") or not raw_name:
        raise HTTPException(400, "kind in {raw,dataset} and name are required")
    name = _safe_rag_segment(raw_name, "name")
    ctx = _rest_security_context(internal_service, body, allow_refinement_system=True)

    if kind == "raw":
        cartridge = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
        _require_cartridge_scope(ctx, cartridge)
        content = _build_raw_doc(cartridge, name)
        source = f"raw:{cartridge}:{name}"
        desc = f"raw bronze entity {name} ({cartridge})"
    else:
        cartridge_for_dataset = _cartridge_of(name)
        if cartridge_for_dataset:
            _require_cartridge_scope(ctx, cartridge_for_dataset)
        content, desc = _build_dataset_doc(name)
        source = f"dataset:{name}"

    r = await _rag_do_ingest(
        name=source,
        content=content,
        description=desc,
        mime_type="text/plain",
        kind="schema",
    )
    semantic_result = None
    if kind == "dataset":
        cartridge_for_semantic = (
            _safe_rag_segment(body.get("cartridge"), "cartridge")
            if body.get("cartridge")
            else _cartridge_of(name)
        )
    else:
        cartridge_for_semantic = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
    if cartridge_for_semantic:
        try:
            semantic_result = await _rebuild_semantic_doc(cartridge_for_semantic)
        except Exception as exc:                              # noqa: BLE001
            semantic_result = {"rebuilt": False, "error": str(exc)}
    return {
        "reindexed": True,
        "source": source,
        **(r if isinstance(r, dict) else {}),
        "semantic": semantic_result,
    }


@app.post("/rag/rebuild-semantic")
async def rag_rest_rebuild_semantic(body: dict, internal_service: str = Depends(verify_api_key)):
    cartridge = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
    ctx = _rest_security_context(internal_service, body, allow_refinement_system=True)
    _require_cartridge_scope(ctx, cartridge)
    return await _rebuild_semantic_doc(cartridge)


def _cartridge_of(dataset_name: str) -> str | None:
    import psycopg2
    from app.config import settings as s
    try:
        conn = psycopg2.connect(
            host=s.pg_host,
            port=s.pg_port,
            dbname=s.pg_db,
            user=s.pg_user,
            password=s.pg_password,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT cartridge FROM datasets WHERE name = %s", (dataset_name,))
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _duckdb_s3_settings(con, endpoint: str, region: str) -> None:
    import os

    url_style = "vhost" if "amazonaws.com" in endpoint else "path"
    secure = (os.environ.get("MINIO_SECURE", "false").lower() in {"1", "true", "yes", "on"})
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"""
        SET s3_endpoint='{endpoint}';
        SET s3_access_key_id='{os.environ.get('MINIO_ACCESS_KEY','')}';
        SET s3_secret_access_key='{os.environ.get('MINIO_SECRET_KEY','')}';
        SET s3_url_style='{url_style}';
        SET s3_use_ssl={'true' if secure else 'false'};
        SET s3_region='{region}';
    """)


async def _rebuild_semantic_doc(cartridge: str) -> dict:
    """Rebuild `_semantic_<cartridge>` from live schemas + data_catalog."""
    import os
    import duckdb
    import psycopg2
    from datetime import datetime, timezone
    from app.config import settings as s

    bucket = os.environ.get("MINIO_BUCKET", "")
    cartridge = _safe_rag_segment(cartridge, "cartridge")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    region = os.environ.get("AWS_REGION", "us-east-1")

    conn = psycopg2.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, layer FROM datasets WHERE cartridge = %s ORDER BY layer, name",
            (cartridge,),
        )
        datasets = cur.fetchall()
        cur.execute("""
            SELECT dataset, column_name, COALESCE(description,''), COALESCE(tags, '{}')
              FROM data_catalog
             WHERE cartridge = %s
        """, (cartridge,))
        desc_rows = cur.fetchall()
    conn.close()
    desc_by = {
        (r[0], r[1]): {"description": r[2], "tags": r[3] or []}
        for r in desc_rows
    }

    con = duckdb.connect()
    _duckdb_s3_settings(con, endpoint, region)
    pggold_dsn = (
        f"host={s.pg_gold_host} port={s.pg_gold_port} dbname={s.pg_gold_db} "
        f"user={s.pg_gold_user or s.pg_user} password={s.pg_gold_password or s.pg_password}"
    )
    try:
        con.execute(f"INSTALL postgres; LOAD postgres; ATTACH '{pggold_dsn}' AS pggold (TYPE postgres);")
    except Exception:
        pass

    out: list[str] = [
        f"# Modelo Semántico — Cartucho `{cartridge}`\n",
        "_Generado automáticamente desde `data_catalog` + schemas reales._\n",
        f"_Última recompilación: {datetime.now(timezone.utc).isoformat()[:19]}Z_\n\n",
    ]

    def fmt_col(dataset: str, col: str, ty: str) -> str:
        d = desc_by.get((dataset, col), {})
        text = d.get("description") or ""
        tags = d.get("tags") or []
        tail = f" [tags: {', '.join(tags)}]" if tags else ""
        return f"- `{col}` ({ty}){': ' + text if text else ''}{tail}\n"

    try:
        raw_rows = con.execute(f"""
            SELECT DISTINCT regexp_extract(file, 'raw/{cartridge}/([^/]+)/', 1) AS entity
            FROM glob('s3://{bucket}/raw/{cartridge}/*/**/*.parquet')
            WHERE regexp_extract(file, 'raw/{cartridge}/([^/]+)/', 1) != ''
            ORDER BY entity
        """).fetchall()
        raws = [r[0] for r in raw_rows]
    except Exception:
        raws = []
    if raws:
        out.append(f"## Bronze · raw — {len(raws)} entidades\n\n")
        for ent in raws:
            out.append(f"### {ent}\n")
            try:
                fields = con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('s3://{bucket}/raw/{cartridge}/{ent}/**/*.parquet',"
                    f" hive_partitioning=true, union_by_name=true) LIMIT 0"
                ).fetchall()
            except Exception as exc:                          # noqa: BLE001
                fields = []
                out.append(f"_(schema unavailable: {exc})_\n")
            for col, ty, *_ in fields:
                out.append(fmt_col(ent, col, ty))
            out.append("\n")

    by_layer: dict[str, list[str]] = {}
    for name, layer in datasets:
        by_layer.setdefault(layer or "silver", []).append(name)
    for layer in ("silver", "gold"):
        names = by_layer.get(layer, [])
        if not names:
            continue
        out.append(f"## {layer.capitalize()} — {len(names)} datasets\n\n")
        for name in names:
            out.append(f"### {name}\n")
            try:
                if layer == "gold":
                    fields = con.execute(f'DESCRIBE pggold."gold_{name}"').fetchall()
                else:
                    parquet = f"s3://{bucket}/silver/{cartridge}/{name}/data.parquet"
                    fields = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0").fetchall()
            except Exception as exc:                          # noqa: BLE001
                fields = []
                out.append(f"_(schema unavailable: {exc})_\n")
            for col, ty, *_ in fields:
                out.append(fmt_col(name, col, ty))
            out.append("\n")

    content = "".join(out)
    source_name = f"_semantic_{cartridge}"
    ingest = await _rag_do_ingest(
        name=source_name,
        content=content,
        description=f"Modelo semántico consolidado del cartucho {cartridge}",
        mime_type="text/plain",
        kind="document",
    )
    return {
        "rebuilt": True,
        "source": source_name,
        "raws": len(raws),
        "datasets": len(datasets),
        "chars": len(content),
        **(ingest if isinstance(ingest, dict) else {}),
    }


def _build_raw_doc(cartridge: str, entity: str) -> str:
    import os
    import duckdb

    bucket = os.environ.get("MINIO_BUCKET", "")
    cartridge = _safe_rag_segment(cartridge, "cartridge")
    entity = _safe_rag_segment(entity, "entity")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    con = duckdb.connect()
    _duckdb_s3_settings(con, endpoint, region)
    path = f"s3://{bucket}/raw/{cartridge}/{entity}/**/*.parquet"
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=true, union_by_name=true) LIMIT 0"
        ).fetchall()
        fields = "\n".join(f"- `{r[0]}` : {r[1]}" for r in rows)
    except Exception as exc:                                  # noqa: BLE001
        fields = f"(schema unavailable: {exc})"
    return (
        f"# Raw entity: {entity}\nLayer: bronze (raw)\nCartridge: {cartridge}\n\n"
        f"## Storage\n`{path}`\n\n## Schema\n{fields}\n"
    )


def _build_dataset_doc(name: str) -> tuple[str, str]:
    import psycopg2
    from app.config import settings as s

    name = _safe_rag_segment(name, "dataset")
    conn = psycopg2.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT layer, cartridge, COALESCE(sql_def,''), COALESCE(description,'') "
                "FROM datasets WHERE name = %s",
                (name,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, f"dataset '{name}' not found")
    layer, cartridge, sql_def, description = row
    body = (
        f"# Dataset: {name}\nLayer: {layer}\nCartridge: {cartridge}\n\n"
        f"## Description\n{description or '(none)'}\n\n"
        f"## SQL\n```sql\n{sql_def}\n```\n"
    )
    return body, f"{layer} dataset {name}"
