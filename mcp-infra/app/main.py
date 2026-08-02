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
import hmac
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from app.pipeline_run_authority import validate_pipeline_run_authority
from app.scheduled_effect_authority import validate_scheduled_effect_authority
from app.dataset_refresh_admission import build_dataset_refresh_admission

from app import registry
from app.rag.embeddings import EmbeddingProviderError
from app.rag.ingest import (
    RagIngestError,
    extract_pdf_text_with_capacity,
    read_ingest_body,
    validate_text_content,
)
from app.security import get_internal_api_key
from app.config import settings
from app.logging_config import setup_logging  # noqa: E402
from app.response_redaction import redact_response_value  # noqa: E402
from app.storage_scope import canonical_storage_key, scoped_storage_key

setup_logging(service_name="mcp-infra")
logger = logging.getLogger(__name__)

# ── Import tool modules so decorators register themselves ──────────────────────
import app.tools.airflow  # noqa: F401
import app.tools.admin_request  # noqa: F401
import app.tools.agents  # noqa: F401
import app.tools.cartridges  # noqa: F401
import app.tools.control_room  # noqa: F401
import app.tools.minio  # noqa: F401
import app.tools.market_context  # noqa: F401
import app.tools.pipeline  # noqa: F401
import app.tools.postgres  # noqa: F401
import app.tools.rag  # noqa: F401
import app.tools.superset  # noqa: F401
import app.tools.vault  # noqa: F401

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
_DAG_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,250}$")
_SHARED_PLATFORM_DAGS = {
    "file_ingest",
    "entity_scheduler",
    "dataset_refresh_chain",
    "agent_runner",
}
_SECURITY_CONTEXT_SIGNATURE_FIELD = "_signature"
_SECURITY_CONTEXT_SIGNED_AT_FIELD = "_signed_at"
_SECURITY_CONTEXT_SIGNATURE_VERSION_FIELD = "_signature_version"
_SECURITY_CONTEXT_SIGNATURE_VERSION = "hmac-sha256-v1"
_SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS = 300
_SECURITY_CONTEXT_SIGNATURE_FUTURE_SKEW_SECONDS = 30
_SECURITY_CONTEXT_MIN_SIGNING_KEY_LEN = 32


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _security_context_signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _SECURITY_CONTEXT_MIN_SIGNING_KEY_LEN:
        raise ValueError("SECURITY_CONTEXT_SIGNING_KEY is required")
    for env_name, value in os.environ.items():
        if not (
            env_name == "INTERNAL_API_KEY" or env_name.startswith("INTERNAL_API_KEY_")
        ):
            continue
        transport_key = (value or "").strip()
        if transport_key and hmac.compare_digest(key, transport_key):
            raise ValueError(
                f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {env_name}"
            )
    return key


def _canonical_security_context(ctx: dict[str, Any]) -> bytes:
    payload = {
        key: value
        for key, value in ctx.items()
        if key != _SECURITY_CONTEXT_SIGNATURE_FIELD
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _security_context_signature_valid(ctx: dict[str, Any]) -> bool:
    if not ctx.get("trusted"):
        return True
    try:
        key = _security_context_signing_key()
    except ValueError:
        return False
    signature = str(ctx.get(_SECURITY_CONTEXT_SIGNATURE_FIELD) or "")
    if not signature:
        return False
    if (
        ctx.get(_SECURITY_CONTEXT_SIGNATURE_VERSION_FIELD)
        != _SECURITY_CONTEXT_SIGNATURE_VERSION
    ):
        return False
    try:
        signed_at = int(ctx.get(_SECURITY_CONTEXT_SIGNED_AT_FIELD))
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if signed_at > now + _SECURITY_CONTEXT_SIGNATURE_FUTURE_SKEW_SECONDS:
        return False
    if now - signed_at > _SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS:
        return False
    expected = hmac.new(
        key.encode("utf-8"), _canonical_security_context(ctx), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# Sprint v1.12: mcp-infra is called by console, workspace and airflow. Each
# pair has its own INTERNAL_API_KEY_*_TO_MCP_INFRA secret. The legacy shared
# key keeps working during the migration window and is dropped in a follow-up.
_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "console": "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
    "workspace": "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
    "airflow": "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
    "refinement": "INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA",
    # The old whitelist allowed this; we keep it via legacy key only outside prod.
    "mcp-infra": None,
}


def verify_api_key(
    x_api_key: str = Header(None), x_internal_service: str = Header(None)
):
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


def _internal_error_request_id(request: Request | None = None) -> str:
    candidate = getattr(getattr(request, "state", None), "request_id", None)
    try:
        return str(uuid.UUID(str(candidate)))
    except Exception:
        return str(uuid.uuid4())


def _log_internal_error(
    exc: Exception, message: str, request: Request | None = None
) -> str:
    request_id = _internal_error_request_id(request)
    logger.exception(
        "%s request_id=%s",
        message,
        request_id,
        extra={"request_id": request_id, "exception_type": type(exc).__name__},
    )
    return request_id


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    request_id = _log_internal_error(exc, "unhandled mcp-infra exception", request)
    return JSONResponse(
        {"error": "Internal Server Error", "request_id": request_id},
        status_code=500,
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 500:
        request_id = _internal_error_request_id(request)
        logger.error(
            "mcp-infra HTTPException sanitized request_id=%s status=%s",
            request_id,
            exc.status_code,
            exc_info=exc.__cause__ is not None,
            extra={"request_id": request_id, "exception_type": type(exc).__name__},
        )
        return JSONResponse(
            {"error": "Internal Server Error", "request_id": request_id},
            status_code=exc.status_code,
            headers=exc.headers,
        )
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


# Sprint v1.41.1 — correlation IDs.
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)


class InvokeRequest(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    security_context: dict[str, Any] | None = None

    class Config:
        extra = "forbid"


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
    "list_cartridges",
    "minio_list_cartridge_specs",
    "minio_read_spec",
    "cartridge_get_semantic",
    "cartridge_search_term",
    "cartridge_get_manifest",
    "cartridge_get_hints",
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
_CARTRIDGE_CONTEXT_TOOLS = {
    "cartridge_get_semantic",
    "cartridge_search_term",
    "cartridge_get_manifest",
    "cartridge_get_hints",
    "cartridge_list_entities",
    "cartridge_get_schema",
    "cartridge_list_jobs",
    "cartridge_list_kbs",
    "cartridge_preview",
    "cartridge_query_kb",
    *_CARTRIDGE_EXECUTE_TOOLS,
}
_AIRFLOW_READ_TOOLS = {
    "airflow_list_dags",
    "airflow_get_run_status",
    "airflow_get_task_logs",
    "airflow_list_task_instances",
    "airflow_list_dag_runs",
}
_AIRFLOW_RUN_TOOLS = {"airflow_trigger_dag"}
_AIRFLOW_WRITE_TOOLS = {
    "airflow_create_dag",
    "airflow_delete_dag",
    "airflow_set_variable",
}
_SUPERSET_TOOLS = {
    "superset_list_databases",
    "superset_list_datasets",
    "superset_list_charts",
    "superset_list_dashboards",
    "superset_export_dashboard",
    "superset_create_database",
    "superset_create_dataset",
    "superset_create_chart",
    "superset_create_dashboard",
    "superset_import_dashboard",
}
_PIPELINE_READ_TOOLS = {"dag_get_source", "watermark_get"}
_PIPELINE_WRITE_TOOLS = {"dag_save_source", "watermark_set"}
_PIPELINE_TELEMETRY_TOOLS = {"pipeline_run_save"}
_VAULT_READ_TOOLS = {
    "vault_list_connections",
    "vault_get_connection",
    "vault_list_secrets",
}
_VAULT_WRITE_TOOLS = {"vault_set_connection", "vault_set_secret"}
_VAULT_DESTRUCTIVE_TOOLS = {"vault_delete_connection"}
_AGENT_READ_TOOLS = {"agent_list", "agent_get"}
_AGENT_WRITE_TOOLS = {"agent_create", "agent_update"}
_AGENT_DESTRUCTIVE_TOOLS = {"agent_delete"}
_CONTROL_ROOM_ALERT_TOOLS = {
    "control_room__raise_alert",
    "control_room__raise_analysis_alert",
}
_CONTROL_ROOM_ANALYSIS_TOOLS = {
    "simulation__monte_carlo_run",
    "decision__orchestrate",
    "wisdom_bits__run",
}
_CONTROL_ROOM_READ_TOOLS = {
    "calibration__bayesian_state",
    "control_room__summary_read",
    "control_room__sap_successfactors_gold_kpis_read",
    "control_room__talent_kpis_read",
    "control_room__talent_overview_read",
    "control_room__talent_9box_read",
    "control_room__talent_metadata_readiness_read",
}
_CONTROL_ROOM_OPERATIONAL_READ_TOOLS = {
    "control_room__dashboard_read",
    "control_room__ops_summary_read",
    "control_room__alerts_read",
    "control_room__agents_ops_read",
    "control_room__decision_intelligence_runs_read",
}
_MARKET_CONTEXT_READ_TOOLS = {"market_context_read"}
_ADMIN_ROLES = {"admin", "owner", "super_admin"}
_SECURITY_SOURCE_BY_SERVICE = {
    "console": {"console", "agent_runner"},
    "workspace": {"workspace"},
    "airflow": {"airflow", "agent_runner"},
    "refinement": {"refinement"},
    "mcp-infra": {"mcp-infra"},
}
_SENSITIVE_TABLES = {
    "activation_tokens",
    "audit_events",
    "decision_actions",
    "decisions",
    "login_attempts",
    "password_reset_tokens",
    "refresh_tokens",
    "roles",
    "system_settings",
    "tenants",
    "users",
    "user_sessions",
    "user_tokens",
    "user_workspace_roles",
    "vault_access_log",
    "vault_entries",
    "workspaces",
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
_SQL_STORAGE_LITERAL_RE = re.compile(
    r"(['\"])(s3://.*?)(?<!\\)\1", re.IGNORECASE | re.DOTALL
)
_DIRECT_STORAGE_SCAN_RE = re.compile(
    r"\b(?:from|join|table)\s+(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
_DUCKDB_SCHEMA_RE = re.compile(
    r'(?<![A-Za-z0-9_])"?(pgdb|pggold)"?\s*\.', re.IGNORECASE
)
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
    if not _security_context_signature_valid(ctx):
        if ctx.get("trusted"):
            raise HTTPException(403, detail="Invalid signed security_context")
        return {}
    if ctx.get("trusted") and internal_service:
        allowed_sources = _SECURITY_SOURCE_BY_SERVICE.get(
            internal_service, {internal_service}
        )
        if str(ctx.get("source") or "") not in allowed_sources:
            return {}
    return ctx


def _is_admin_context(ctx: dict[str, Any]) -> bool:
    return (
        bool(ctx.get("trusted")) and str(ctx.get("role") or "").lower() in _ADMIN_ROLES
    )


def _is_unscoped_admin_context(ctx: dict[str, Any]) -> bool:
    if not _is_admin_context(ctx):
        return False
    if ctx.get("tenant_id") or ctx.get("workspace_id"):
        return False
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    return "*" in allowed


def _allowed_prefix_matches(ctx: dict[str, Any], value: str) -> bool:
    """Match explicit prefixes without letting root prefixes grant all data."""
    prefixes = [
        str(p).lstrip("/").rstrip("/") for p in (ctx.get("allowed_prefixes") or [])
    ]
    for prefix in prefixes:
        if not prefix:
            continue
        if len(prefix.split("/")) < 2:
            continue
        if value == prefix or value.startswith(prefix + "/"):
            return True
    return False


def _require_context_permission(
    req: InvokeRequest, permission: str, internal_service: str | None = None
) -> dict[str, Any]:
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
    return _key_has_exact_scope(value, tenant_id, workspace_id)


def _canonical_storage_key(value: str) -> str:
    try:
        return canonical_storage_key(value)
    except ValueError as exc:
        raise HTTPException(403, detail="storage path is not allowed") from exc


def _key_has_exact_scope(value: str, tenant_id: str, workspace_id: str) -> bool:
    try:
        scoped_storage_key(
            value,
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "allowed_cartridges": ["*"],
            },
        )
        return True
    except (PermissionError, ValueError):
        return False


def _storage_scope_markers(key: str) -> tuple[str | None, str | None]:
    tenant: str | None = None
    workspace: str | None = None
    for part in _canonical_storage_key(key).split("/"):
        if part.startswith("tenant_id="):
            tenant = part.split("=", 1)[1]
        elif part.startswith("workspace_id="):
            workspace = part.split("=", 1)[1]
    return tenant, workspace


def _is_physical_storage_key(key: str) -> bool:
    parts = _canonical_storage_key(key).split("/")
    if not parts:
        return False
    root = parts[0]
    if root in {"raw", "silver", "gold"}:
        return len(parts) > 3
    if root == "uploads":
        return len(parts) > 2
    return False


def _has_foreign_storage_scope(ctx: dict[str, Any], key: str) -> bool:
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not (tenant_id and workspace_id):
        return False
    tenant, workspace = _storage_scope_markers(key)
    if tenant is None and workspace is None:
        return False
    return tenant != tenant_id or workspace != workspace_id


def _has_tenant_workspace_scope(ctx: dict[str, Any]) -> bool:
    return bool(
        str(ctx.get("tenant_id") or "").strip()
        and str(ctx.get("workspace_id") or "").strip()
    )


def _has_invalid_scoped_storage_path(ctx: dict[str, Any], key: str) -> bool:
    if not _has_tenant_workspace_scope(ctx) or not _is_physical_storage_key(key):
        return False
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    tenant, workspace = _storage_scope_markers(key)
    return tenant != tenant_id or workspace != workspace_id


def _require_scoped_object_path(ctx: dict[str, Any], value: str) -> None:
    """Prevent scoped callers from listing/downloading whole cartridge object trees."""
    value = str(value or "").strip()
    if not value:
        return
    key = _canonical_storage_key(value)
    parts = key.split("/")
    if not parts:
        return
    root = parts[0]
    if root == "cartridges":
        return
    if root not in {"raw", "silver", "gold", "uploads"}:
        return
    try:
        scoped_storage_key(key, ctx)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            403, detail="tenant/workspace object scope required"
        ) from exc


def _require_cartridge_scope(ctx: dict[str, Any], cartridge_id: str) -> None:
    cartridge_id = str(cartridge_id or "").strip()
    if not cartridge_id:
        raise HTTPException(403, detail="cartridge_id is required")
    if _is_unscoped_admin_context(ctx):
        return
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if cartridge_id in allowed:
        return
    if not (
        _prefix_allowed(ctx, f"raw/{cartridge_id}/")
        or _prefix_allowed(ctx, f"silver/{cartridge_id}/")
        or _prefix_allowed(ctx, f"gold/{cartridge_id}/")
        or _prefix_allowed(ctx, f"cartridges/{cartridge_id}/")
    ):
        raise HTTPException(403, detail="cartridge not allowed")


def _cartridge_scope_allowed(ctx: dict[str, Any], cartridge_id: str) -> bool:
    try:
        _require_cartridge_scope(ctx, cartridge_id)
        return True
    except HTTPException:
        return False


def _market_context_provider_scope(ctx: dict[str, Any], args: dict[str, Any]) -> None:
    if not _has_tenant_workspace_scope(ctx):
        raise HTTPException(
            403, detail="market context requires tenant/workspace scope"
        )
    supplied = sorted(
        key
        for key in {
            "tenant_id",
            "workspace_id",
            "security_context",
            "allowed_providers",
        }
        if key in args
    )
    if supplied:
        raise HTTPException(
            403, detail=f"backend-owned arg is not allowed: {', '.join(supplied)}"
        )
    provider_to_cartridge = {
        "banxico": "banxico",
        "inegi": "inegi",
        "sec_edgar": "sec_edgar",
    }
    raw_requested = args.get("providers")
    requested = {str(args["provider"])} if args.get("provider") else set()
    if isinstance(raw_requested, list):
        requested.update(str(item) for item in raw_requested)
    unknown = requested - set(provider_to_cartridge)
    if unknown:
        raise HTTPException(400, detail="unknown market context provider")
    allowed: list[str] = []
    for provider, cartridge_id in provider_to_cartridge.items():
        if requested and provider not in requested:
            continue
        if _cartridge_scope_allowed(ctx, cartridge_id):
            allowed.append(provider)
        elif requested:
            raise HTTPException(403, detail="market context provider not allowed")
    if not allowed:
        raise HTTPException(403, detail="no market context providers allowed")
    args["allowed_providers"] = allowed
    args["security_context"] = ctx


def _validate_pipeline_run_save_scope(
    ctx: dict[str, Any], args: dict[str, Any]
) -> None:
    cartridge_id = str(args.get("cartridge_id") or "").strip()
    if not cartridge_id:
        raise HTTPException(403, detail="pipeline run cartridge_id is required")
    if cartridge_id == "platform":
        return
    tenant_id = str(args.get("tenant_id") or "").strip()
    workspace_id = str(args.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(
            403, detail="pipeline run tenant/workspace scope is required"
        )
    if _has_tenant_workspace_scope(ctx):
        if tenant_id != str(ctx.get("tenant_id") or "") or workspace_id != str(
            ctx.get("workspace_id") or ""
        ):
            raise HTTPException(403, detail="pipeline run scope mismatch")
        _require_cartridge_scope(ctx, cartridge_id)


def _require_dag_registered_for_cartridge(dag_id: str, cartridge_id: str) -> None:
    if not _DAG_ID_RE.fullmatch(dag_id or ""):
        raise HTTPException(403, detail="DAG id is not allowed")
    safe_cartridge = str(cartridge_id or "").strip()
    normalized = safe_cartridge.replace("-", "_")
    generated_dags = {
        f"{normalized}_extract",
        f"{normalized}_extract_all",
        f"{normalized}_ses_inbox_import",
        f"{normalized}_outlook_audit_report_import",
    }
    if dag_id in generated_dags:
        return
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
            cur.execute(
                """
                SELECT 1
                  FROM cartridge_dags
                 WHERE cartridge_id = %s AND dag_id = %s
                UNION
                SELECT 1
                  FROM entity_config
                 WHERE cartridge_id = %s AND dag_id = %s
                LIMIT 1
                """,
                (safe_cartridge, dag_id, safe_cartridge, dag_id),
            )
            owned = cur.fetchone() is not None
        conn.close()
    except Exception as exc:
        raise HTTPException(
            503, detail="could not verify DAG cartridge ownership"
        ) from exc
    if not owned:
        raise HTTPException(403, detail="DAG is not registered for cartridge")


def _allowed_cartridges(ctx: dict[str, Any]) -> set[str]:
    return {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }


def _dag_prefix_allowed(dag_id: str, cartridge_id: str) -> bool:
    normalized = str(cartridge_id or "").strip().replace("-", "_")
    if not normalized:
        return False
    generated_dags = {
        f"{normalized}_extract",
        f"{normalized}_extract_all",
        f"{normalized}_ses_inbox_import",
        f"{normalized}_outlook_audit_report_import",
    }
    return dag_id in generated_dags or dag_id.startswith(f"{normalized}_")


def _registered_cartridges_for_dag(dag_id: str) -> set[str]:
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
            cur.execute(
                """
                SELECT cartridge_id FROM cartridge_dags WHERE dag_id = %s
                UNION
                SELECT cartridge_id FROM entity_config WHERE dag_id = %s
                """,
                (dag_id, dag_id),
            )
            rows = cur.fetchall()
        conn.close()
        return {str(row[0]) for row in rows if row and row[0]}
    except Exception:
        return set()


def _dag_allowed_for_context(ctx: dict[str, Any], dag_id: str) -> bool:
    if not _DAG_ID_RE.fullmatch(dag_id or ""):
        return False
    if _is_unscoped_admin_context(ctx):
        return True
    if dag_id in _SHARED_PLATFORM_DAGS:
        return bool(ctx.get("tenant_id") and ctx.get("workspace_id"))
    allowed = _allowed_cartridges(ctx)
    if "*" in allowed:
        return True
    for cartridge_id in allowed:
        if _dag_prefix_allowed(dag_id, cartridge_id):
            return True
    registered = _registered_cartridges_for_dag(dag_id)
    return bool(registered and registered.intersection(allowed))


def _require_airflow_read_scope(ctx: dict[str, Any], args: dict[str, Any]) -> None:
    if _is_unscoped_admin_context(ctx):
        return
    dag_id = str(args.get("dag_id") or "").strip()
    if dag_id and not _dag_allowed_for_context(ctx, dag_id):
        raise HTTPException(403, detail="DAG not allowed")
    dag_run_id = str(args.get("dag_run_id") or "").strip()
    if dag_run_id:
        _require_pipeline_run_scope(ctx, dag_run_id)


def _require_pipeline_run_scope(ctx: dict[str, Any], run_id: str) -> None:
    if _is_unscoped_admin_context(ctx):
        return
    run_id = str(run_id or "").strip()
    if not run_id:
        raise HTTPException(403, detail="run_id is required")
    ctx_tenant = str(ctx.get("tenant_id") or "").strip()
    ctx_workspace = str(ctx.get("workspace_id") or "").strip()
    if not ctx_tenant or not ctx_workspace:
        raise HTTPException(
            403, detail="tenant/workspace scope required for run access"
        )
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
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.workspace_id', %s, true)",
                (ctx_tenant, ctx_workspace),
            )
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name='pipeline_runs'
                   AND column_name IN ('tenant_id', 'workspace_id')
                """
            )
            available = {str(row[0]) for row in cur.fetchall()}
            select_scope = []
            if "tenant_id" in available:
                select_scope.append("tenant_id::text")
            else:
                select_scope.append("NULL::text AS tenant_id")
            if "workspace_id" in available:
                select_scope.append("workspace_id::text")
            else:
                select_scope.append("NULL::text AS workspace_id")
            cur.execute(
                f"""
                SELECT
                    cartridge_id,
                    {select_scope[0]},
                    {select_scope[1]}
                  FROM pipeline_runs
                 WHERE run_id = %s
                 LIMIT 1
                """,
                (run_id,),
            )
            row = cur.fetchone()
        conn.close()
    except Exception as exc:
        raise HTTPException(503, detail="could not verify run scope") from exc
    if not row:
        raise HTTPException(403, detail="run_id not found or not allowed")
    cartridge_id, tenant_id, workspace_id = row
    if not tenant_id or not workspace_id:
        raise HTTPException(403, detail="run tenant/workspace scope missing")
    if ctx_tenant != tenant_id:
        raise HTTPException(403, detail="run tenant not allowed")
    if ctx_workspace != workspace_id:
        raise HTTPException(403, detail="run workspace not allowed")
    _require_cartridge_scope(ctx, str(cartridge_id or ""))


def _pipeline_run_allowed(ctx: dict[str, Any], run_id: str) -> bool:
    try:
        _require_pipeline_run_scope(ctx, run_id)
        return True
    except HTTPException:
        return False


def _validate_airflow_trigger_scope(ctx: dict[str, Any], args: dict[str, Any]) -> None:
    dag_id = str(args.get("dag_id") or "").strip()
    conf = args.get("conf") if isinstance(args.get("conf"), dict) else {}
    cartridge_id = str(
        conf.get("cartridge_id") or args.get("cartridge_id") or ""
    ).strip()
    if not _is_unscoped_admin_context(ctx):
        tenant_id = str(ctx.get("tenant_id") or "").strip()
        workspace_id = str(ctx.get("workspace_id") or "").strip()
        if not tenant_id or not workspace_id:
            raise HTTPException(
                403, detail="DAG trigger requires tenant/workspace scope"
            )
        for key, value in (("tenant_id", tenant_id), ("workspace_id", workspace_id)):
            supplied = str(conf.get(key) or "").strip()
            if supplied and supplied != value:
                raise HTTPException(403, detail=f"DAG {key} scope mismatch")
            conf[key] = value
        conf["security_context"] = ctx
        args["conf"] = conf

    if dag_id in _SHARED_PLATFORM_DAGS and not _is_unscoped_admin_context(ctx):
        if not cartridge_id:
            raise HTTPException(
                403,
                detail="shared DAG trigger requires cartridge_id outside admin context",
            )
        _require_cartridge_scope(ctx, cartridge_id)
        if dag_id == "dataset_refresh_chain":
            dag_run_id = str(args.get("dag_run_id") or "").strip()
            if not dag_run_id:
                dag_run_id = f"mcp__dataset_refresh_chain__{uuid.uuid4().hex}"
                args["dag_run_id"] = dag_run_id
            conf["security_context"] = build_dataset_refresh_admission(
                ctx, conf, dag_run_id
            )
            args["conf"] = conf
        return

    if cartridge_id:
        _require_cartridge_scope(ctx, cartridge_id)
        if not _is_unscoped_admin_context(ctx):
            _require_dag_registered_for_cartridge(dag_id, cartridge_id)
        return

    if not _is_unscoped_admin_context(ctx):
        raise HTTPException(
            403, detail="DAG trigger requires cartridge_id outside admin context"
        )


def _inject_cartridge_execution_scope(
    ctx: dict[str, Any], args: dict[str, Any]
) -> None:
    args["security_context"] = ctx


def _reject_client_owned_scope_args(args: dict[str, Any]) -> None:
    forbidden = {"tenant_id", "workspace_id", "security_context"}
    supplied = sorted(key for key in forbidden if key in args)
    if supplied:
        raise HTTPException(
            403,
            detail=f"backend-owned arg is not allowed: {', '.join(supplied)}",
        )


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
    try:
        rest = canonical_storage_key(path[5:], allow_glob=True)
    except ValueError as exc:
        raise HTTPException(403, detail="cartridge SQL path is not allowed") from exc
    key = rest.split("/", 1)[1] if "/" in rest else ""
    if not key:
        raise HTTPException(403, detail="cartridge SQL path is not allowed")
    return key


def _require_cartridge_sql_path(
    ctx: dict[str, Any], cartridge_id: str, path: str
) -> None:
    key = _reader_storage_key(path)
    if not (
        key.startswith(f"raw/{cartridge_id}/")
        or key.startswith(f"silver/{cartridge_id}/")
        or key.startswith(f"gold/{cartridge_id}/")
    ):
        raise HTTPException(
            403, detail="cartridge SQL must stay inside its cartridge prefix"
        )
    try:
        scoped_storage_key(key, ctx, allow_glob=True)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403, detail="cartridge SQL path not allowed") from exc


def _validate_cartridge_query_sql(
    ctx: dict[str, Any], cartridge_id: str, sql: str
) -> None:
    sql = sql or ""
    masked = _mask_single_quoted(sql)
    if not _SQL_START_RE.search(masked):
        raise HTTPException(403, detail="cartridge SQL must be read-only SELECT/WITH")
    if (
        ";" in masked
        or _SQL_COMMENT_RE.search(masked)
        or _SQL_FORBIDDEN_RE.search(masked)
    ):
        raise HTTPException(
            403, detail="cartridge SQL contains unsafe statements or comments"
        )
    if _DUCKDB_SCHEMA_RE.search(masked):
        raise HTTPException(
            403, detail="cartridge SQL cannot read service database schemas"
        )

    reader_calls = list(_SQL_READER_CALL_RE.finditer(sql))
    direct_readers = list(_SCOPED_READER_RE.finditer(sql))
    if not direct_readers or len(reader_calls) != len(direct_readers):
        raise HTTPException(
            403, detail="cartridge SQL must read only direct s3:// file literals"
        )
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


_RAG_SCOPE_SUFFIX_RE = re.compile(r":tenant:([^:]+):workspace:([^:]+)$")
_RAG_HEAD_SUFFIX_RE = re.compile(r":head:([0-9a-f]{64})$")


def _rag_scope_suffix(ctx: dict[str, Any]) -> str:
    if _is_unscoped_admin_context(ctx) or not _has_tenant_workspace_scope(ctx):
        return ""
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    return f":tenant:{tenant_id}:workspace:{workspace_id}"


def _rag_base_name_for_context(ctx: dict[str, Any], name: str) -> tuple[str, bool]:
    if _is_unscoped_admin_context(ctx) or not _has_tenant_workspace_scope(ctx):
        match = _RAG_SCOPE_SUFFIX_RE.search(name)
        return (name[: match.start()] if match else name, True)
    match = _RAG_SCOPE_SUFFIX_RE.search(name)
    if not match:
        return name, False
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    return name[: match.start()], match.group(1) == tenant_id and match.group(
        2
    ) == workspace_id


def _rag_source_allowed(ctx: dict[str, Any], name: str) -> bool:
    if _is_unscoped_admin_context(ctx):
        return True
    name = str(name or "")
    name, scope_ok = _rag_base_name_for_context(ctx, name)
    if not scope_ok:
        return False
    head = _RAG_HEAD_SUFFIX_RE.search(name)
    if name.startswith("_semantic_"):
        if not head:
            return False
        from app.publication_heads import publication_epoch

        if head.group(1) != publication_epoch(ctx):
            return False
        name = name[: head.start()]
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
        # Pass ctx so the lookup is forward-compatible with tenant-aware
        # dataset filtering. Today _cartridge_of treats ctx as a reserved
        # arg; the cartridge scope is still enforced by _prefix_allowed.
        cartridge = _cartridge_of(dataset, ctx)
        return bool(
            cartridge
            and (
                _prefix_allowed(ctx, f"silver/{cartridge}/{dataset}/")
                or _prefix_allowed(ctx, f"gold/{cartridge}/{dataset}/")
            )
        )
    return False


def _rag_is_reserved_source_name(name: str) -> bool:
    name = str(name or "")
    return name.startswith(("raw:", "dataset:", "_semantic_"))


def _scoped_rag_document_name(ctx: dict[str, Any], name: str) -> str:
    name = str(name or "").strip()
    if _is_unscoped_admin_context(ctx) or not _has_tenant_workspace_scope(ctx):
        return name
    match = _RAG_SCOPE_SUFFIX_RE.search(name)
    if match:
        tenant_id = str(ctx.get("tenant_id") or "").strip()
        workspace_id = str(ctx.get("workspace_id") or "").strip()
        if match.group(1) != tenant_id or match.group(2) != workspace_id:
            raise HTTPException(403, detail="RAG source is outside caller scope")
        return name
    return f"{name}{_rag_scope_suffix(ctx)}"


def _rag_row_allowed(ctx: dict[str, Any], row: Any) -> bool:
    if _is_unscoped_admin_context(ctx):
        return True
    if not isinstance(row, dict):
        return _rag_source_allowed(ctx, "")
    row_tenant = str(row.get("tenant_id") or "").strip()
    row_workspace = str(row.get("workspace_id") or "").strip()
    if row_tenant or row_workspace:
        return (
            row_tenant == str(ctx.get("tenant_id") or "").strip()
            and row_workspace == str(ctx.get("workspace_id") or "").strip()
        )
    return _rag_source_allowed(
        ctx, str(row.get("name") or row.get("source_name") or "")
    )


def _require_rag_context_scope(ctx: dict[str, Any]) -> None:
    if _is_unscoped_admin_context(ctx):
        return
    if not _has_tenant_workspace_scope(ctx):
        raise HTTPException(403, detail="RAG requires tenant/workspace scope")


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
            cur.execute(
                "SELECT cartridge_id FROM pipeline_runs WHERE run_id = %s", (run_id,)
            )
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
        out["sources"] = [s for s in out["sources"] if _rag_row_allowed(ctx, s)]
    if isinstance(out.get("results"), list):
        out["results"] = [r for r in out["results"] if _rag_row_allowed(ctx, r)]
    return out


def _airflow_run_allowed(ctx: dict[str, Any], run: dict[str, Any]) -> bool:
    if _is_unscoped_admin_context(ctx):
        return True
    conf = run.get("conf") if isinstance(run, dict) else {}
    conf = conf if isinstance(conf, dict) else {}
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    cartridge_id = str(conf.get("cartridge_id") or "").strip()
    if tenant_id and tenant_id != str(ctx.get("tenant_id") or ""):
        return False
    if workspace_id and workspace_id != str(ctx.get("workspace_id") or ""):
        return False
    if cartridge_id:
        try:
            _require_cartridge_scope(ctx, cartridge_id)
        except HTTPException:
            return False
    return bool(tenant_id or workspace_id or cartridge_id)


def _filter_airflow_payload(tool: str, payload: Any, ctx: dict[str, Any]) -> Any:
    if not isinstance(payload, dict) or _is_unscoped_admin_context(ctx):
        return payload
    out = dict(payload)
    if tool == "airflow_list_dags" and isinstance(out.get("dags"), list):
        out["dags"] = [
            dag
            for dag in out["dags"]
            if isinstance(dag, dict)
            and _dag_allowed_for_context(ctx, str(dag.get("dag_id") or ""))
        ]
    elif tool == "airflow_list_dag_runs" and isinstance(out.get("runs"), list):
        dag_id = str(out.get("dag_id") or "")
        if dag_id in _SHARED_PLATFORM_DAGS:
            out["runs"] = [run for run in out["runs"] if _airflow_run_allowed(ctx, run)]
    return out


def _redact_tool_result(payload: Any) -> Any:
    """Redact secrets from tool return payloads before MCP responds.

    Tools sometimes return third-party error text instead of raising. That
    text may contain Authorization headers, API keys, SAP passwords or Vault
    values echoed by SDKs. Logging redaction does not protect JSON responses,
    so the final invoke boundary sanitizes recursively.
    """
    return redact_response_value(payload)


def _enforce_data_scope(
    req: InvokeRequest, internal_service: str | None = None
) -> dict[str, Any] | None:
    tool = req.tool
    args = req.args if isinstance(req.args, dict) else {}
    req.args = args
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
    elif tool in _SUPERSET_TOOLS:
        ctx = _require_context_permission(req, "studio.write", internal_service)
    elif tool in _PIPELINE_READ_TOOLS:
        ctx = _require_context_permission(req, "pipelines.read", internal_service)
    elif tool in _PIPELINE_WRITE_TOOLS:
        ctx = _require_context_permission(req, "pipelines.write", internal_service)
    elif tool in _PIPELINE_TELEMETRY_TOOLS:
        try:
            ctx = validate_pipeline_run_authority(
                req.security_context,
                body={"tool": tool, "args": args},
                internal_service=str(internal_service or ""),
                consume=True,
            )
        except ValueError as exc:
            raise HTTPException(
                403, detail="pipeline telemetry authority required"
            ) from exc
    elif tool in {"vault_list_connections", "vault_get_connection"}:
        ctx = _require_context_permission(
            req, "vault.connections.read", internal_service
        )
    elif tool == "vault_list_secrets":
        ctx = _require_context_permission(
            req, "vault.secrets.read_masked", internal_service
        )
    elif tool in {
        "vault_set_connection",
        "vault_delete_connection",
        "vault_set_secret",
    }:
        ctx = _require_context_permission(
            req, "vault.connections.write", internal_service
        )
    elif tool in _AGENT_READ_TOOLS:
        ctx = _require_context_permission(req, "studio.read", internal_service)
    elif tool in _AGENT_WRITE_TOOLS:
        ctx = _require_context_permission(req, "studio.write", internal_service)
    elif tool in _AGENT_DESTRUCTIVE_TOOLS:
        ctx = _require_context_permission(req, "copilot.execute", internal_service)
    elif tool in _CONTROL_ROOM_ALERT_TOOLS | _CONTROL_ROOM_ANALYSIS_TOOLS:
        ctx = _require_context_permission(req, "control_room.write", internal_service)
    elif tool in _CONTROL_ROOM_OPERATIONAL_READ_TOOLS:
        ctx = _require_context_permission(req, "operations.read", internal_service)
    elif tool in _CONTROL_ROOM_READ_TOOLS:
        ctx = _require_context_permission(req, "datasets.read", internal_service)
    elif tool in _MARKET_CONTEXT_READ_TOOLS:
        ctx = _require_context_permission(req, "datasets.read", internal_service)
    elif tool.startswith("cartridge_"):
        raise HTTPException(403, detail="cartridge tool lacks tenancy metadata")
    else:
        return None

    if tool in _SUPERSET_TOOLS:
        if "studio.write" not in set(
            ctx.get("permissions") or []
        ) or not _is_admin_context(ctx):
            raise HTTPException(
                403, detail="superset tools require admin studio.write context"
            )

    if tool in _RAG_READ_TOOLS | _RAG_WRITE_TOOLS:
        _require_rag_context_scope(ctx)
        if tool == "ingest_document":
            source_name = str(args.get("name") or "").strip()
            if not source_name:
                raise HTTPException(400, detail="RAG source name is required")
            if _rag_is_reserved_source_name(source_name):
                if not _rag_source_allowed(ctx, source_name):
                    raise HTTPException(
                        403, detail="RAG source is outside caller scope"
                    )
            else:
                args["name"] = _scoped_rag_document_name(ctx, source_name)
        args["security_context"] = ctx

    if tool in _AIRFLOW_WRITE_TOOLS | _PIPELINE_WRITE_TOOLS:
        if not _is_admin_context(ctx):
            raise HTTPException(
                403, detail="airflow and pipeline write tools require admin context"
            )

    if tool in _PIPELINE_TELEMETRY_TOOLS:
        _validate_pipeline_run_save_scope(ctx, args)

    if tool in _PIPELINE_READ_TOOLS | _PIPELINE_WRITE_TOOLS:
        cartridge_id = str(args.get("cartridge_id") or "").strip()
        _require_cartridge_scope(ctx, cartridge_id)
        if tool in {
            "watermark_get",
            "watermark_set",
        } and not _is_unscoped_admin_context(ctx):
            if not _has_tenant_workspace_scope(ctx):
                raise HTTPException(
                    403, detail="pipeline tools require tenant/workspace scope"
                )
            args["tenant_id"] = str(ctx.get("tenant_id") or "")
            args["workspace_id"] = str(ctx.get("workspace_id") or "")

    if tool == "airflow_trigger_dag":
        _validate_airflow_trigger_scope(ctx, args)

    if tool in _AIRFLOW_READ_TOOLS:
        _require_airflow_read_scope(ctx, args)

    if tool in _AGENT_READ_TOOLS | _AGENT_WRITE_TOOLS | _AGENT_DESTRUCTIVE_TOOLS:
        source = str(ctx.get("source") or "")
        if source == "agent_runner":
            raise HTTPException(403, detail="scheduled agents cannot manage agents")
        if (
            tool in _AGENT_WRITE_TOOLS | _AGENT_DESTRUCTIVE_TOOLS
            and not _is_admin_context(ctx)
        ):
            raise HTTPException(403, detail="agent management requires admin context")
        if tool in _AGENT_READ_TOOLS and not _is_unscoped_admin_context(ctx):
            cartridge_id = str(args.get("cartridge_id") or "").strip()
            if not cartridge_id:
                raise HTTPException(
                    403, detail="agent reads require cartridge_id outside admin context"
                )
            _require_cartridge_scope(ctx, cartridge_id)
        args["security_context"] = ctx

    if tool in _VAULT_READ_TOOLS | _VAULT_WRITE_TOOLS | _VAULT_DESTRUCTIVE_TOOLS:
        if (
            tool in _VAULT_WRITE_TOOLS | _VAULT_DESTRUCTIVE_TOOLS
            and not _is_admin_context(ctx)
        ):
            raise HTTPException(403, detail="vault writes require admin context")
        vault_scope = str(args.get("cartridge_id") or args.get("scope") or "").strip()
        if not _is_unscoped_admin_context(ctx):
            _require_cartridge_scope(ctx, vault_scope)
        args["security_context"] = ctx

    if tool in (
        _CONTROL_ROOM_ALERT_TOOLS
        | _CONTROL_ROOM_ANALYSIS_TOOLS
        | _CONTROL_ROOM_READ_TOOLS
        | _CONTROL_ROOM_OPERATIONAL_READ_TOOLS
    ):
        if not _has_tenant_workspace_scope(ctx):
            detail = (
                "control room analysis requires tenant/workspace scope"
                if tool
                in _CONTROL_ROOM_READ_TOOLS | _CONTROL_ROOM_OPERATIONAL_READ_TOOLS
                else "control room alerts require tenant/workspace scope"
            )
            raise HTTPException(403, detail=detail)
        forbidden_scope_args = {
            "tenant_id",
            "workspace_id",
            "user_id",
            "security_context",
        }
        supplied = sorted(key for key in forbidden_scope_args if key in args)
        if supplied:
            raise HTTPException(
                403, detail=f"backend-owned arg is not allowed: {', '.join(supplied)}"
            )
        effect_authority = args.get("effect_authority")
        if (
            str(ctx.get("source") or "") == "agent_runner"
            and tool in (_CONTROL_ROOM_ALERT_TOOLS | _CONTROL_ROOM_ANALYSIS_TOOLS)
            and effect_authority is None
        ):
            raise HTTPException(403, detail="scheduled effect authority is required")
        if effect_authority is not None:
            try:
                validate_scheduled_effect_authority(
                    effect_authority, tool=tool, args=args, context=ctx
                )
            except ValueError as exc:
                raise HTTPException(
                    403, detail="scheduled effect authority mismatch"
                ) from exc
        if tool in _CONTROL_ROOM_ALERT_TOOLS or args.get("cartridge_id"):
            _require_cartridge_scope(ctx, str(args.get("cartridge_id") or ""))
        if tool == "wisdom_bits__run":
            _require_cartridge_scope(
                ctx, str(args.get("cartridge_id") or "sap_successfactors")
            )
        args["security_context"] = ctx

    if tool in _MARKET_CONTEXT_READ_TOOLS:
        _market_context_provider_scope(ctx, args)

    if tool.startswith("postgres_"):
        mentioned_tables = _postgres_mentioned_tables(
            str(args.get("sql") or args.get("query") or ""),
            str(args.get("table") or args.get("table_name") or args.get("name") or ""),
        )
        if mentioned_tables & _SENSITIVE_TABLES:
            raise HTTPException(
                403, detail="sensitive internal tables are not readable through MCP"
            )
        if not _is_unscoped_admin_context(ctx):
            raise HTTPException(
                403,
                detail="main database access requires explicit unscoped admin context",
            )

    if tool.startswith("minio_"):
        if tool in _DATA_READ_TOOLS:
            args["security_context"] = ctx
        bucket = args.get("bucket")
        allowed_buckets = set(ctx.get("allowed_buckets") or [])
        if bucket and allowed_buckets and bucket not in allowed_buckets:
            raise HTTPException(403, detail="bucket not allowed")
        path = args.get("prefix") or args.get("object_path") or ""
        if tool in {"minio_upload_spec", "minio_read_spec"}:
            cartridge_id = str(args.get("cartridge_id") or "").strip()
            filename = str(args.get("filename") or "").strip()
            if (
                not cartridge_id
                or not filename
                or "/" in filename
                or "\\" in filename
                or ".." in filename
            ):
                raise HTTPException(403, detail="invalid cartridge spec path")
            path = f"cartridges/{cartridge_id}/specs/{filename}"
        if (
            tool == "minio_list_objects"
            and not path
            and not _is_unscoped_admin_context(ctx)
        ):
            raise HTTPException(403, detail="object prefix is required")
        if not _prefix_allowed(ctx, str(path)):
            raise HTTPException(403, detail="object prefix not allowed")
        _require_scoped_object_path(ctx, str(path))
        cartridge_id = args.get("cartridge_id")
        if cartridge_id and not _prefix_allowed(ctx, f"cartridges/{cartridge_id}/"):
            raise HTTPException(403, detail="cartridge not allowed")

    if tool in _CARTRIDGE_DATA_TOOLS:
        _reject_client_owned_scope_args(args)
        cartridge_id = str(args.get("cartridge_id") or "").strip()
        _require_cartridge_scope(ctx, cartridge_id)
        if tool == "cartridge_preview":
            if not _is_unscoped_admin_context(ctx) and not _has_tenant_workspace_scope(
                ctx
            ):
                raise HTTPException(
                    403, detail="cartridge preview requires tenant/workspace scope"
                )
            _inject_cartridge_execution_scope(ctx, args)
        elif tool == "cartridge_query_kb":
            _validate_cartridge_query_sql(ctx, cartridge_id, str(args.get("sql") or ""))
            _inject_cartridge_execution_scope(ctx, args)

    if tool in _RUN_ID_SCOPED_TOOLS:
        _require_pipeline_run_scope(ctx, str(args.get("run_id") or ""))
    elif tool in _CARTRIDGE_READ_TOOLS | _CARTRIDGE_EXECUTE_TOOLS:
        _reject_client_owned_scope_args(args)
        if tool != "list_cartridges":
            _require_cartridge_scope(
                ctx, str(args.get("cartridge_id") or args.get("id") or "")
            )
        if tool in _CARTRIDGE_CONTEXT_TOOLS and not _is_unscoped_admin_context(ctx):
            if not _has_tenant_workspace_scope(ctx):
                raise HTTPException(
                    403, detail="cartridge tools require tenant/workspace scope"
                )
        if tool == "cartridge_list_entities" and not _is_unscoped_admin_context(ctx):
            if not _has_tenant_workspace_scope(ctx):
                raise HTTPException(
                    403, detail="cartridge entities require tenant/workspace scope"
                )
            args["security_context"] = ctx
        if tool in _CARTRIDGE_CONTEXT_TOOLS:
            args["security_context"] = ctx
        if tool in _CARTRIDGE_EXECUTE_TOOLS:
            _inject_cartridge_execution_scope(ctx, args)

    return ctx


# ── MCP endpoints ──────────────────────────────────────────────────────────────


@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
def get_tools():
    return {"tools": registry.list_tools()}


@app.post("/mcp/invoke")
async def invoke_tool(
    req: InvokeRequest, internal_service: str = Depends(verify_api_key)
):
    ctx = _enforce_data_scope(req, internal_service)
    try:
        result = await registry.invoke(req.tool, req.args)
        if req.tool in _RAG_READ_TOOLS and ctx is not None:
            result = _filter_rag_payload(result, ctx)
        if req.tool in _AIRFLOW_READ_TOOLS and ctx is not None:
            result = _filter_airflow_payload(req.tool, result, ctx)
        if (
            req.tool == "list_cartridges"
            and ctx is not None
            and not _is_unscoped_admin_context(ctx)
        ):
            allowed = set(
                str(item).strip()
                for item in (ctx.get("allowed_cartridges") or [])
                if str(item).strip()
            )
            if "*" not in allowed:
                result = [
                    row for row in (result or []) if str(row.get("id") or "") in allowed
                ]
        if (
            req.tool == "cartridge_list_jobs"
            and ctx is not None
            and not _is_unscoped_admin_context(ctx)
        ):
            result = [
                row
                for row in (result or [])
                if isinstance(row, dict)
                and _pipeline_run_allowed(ctx, str(row.get("run_id") or ""))
            ]
        result = _redact_tool_result(result)
        return {"result": result}
    except HTTPException:
        raise
    except EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=503, detail="Embedding provider unavailable"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Resource not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Tool invocation failed") from exc


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/healthz")
def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    Same contract as the legacy /health below — minimal payload, no
    auth — but uses the /healthz path the rest of the platform pins
    its healthchecks on."""
    return {"ok": True, "service": "mcp-infra"}


def _postgres_ready(host: str, port: int, db: str, user: str, password: str) -> bool:
    import psycopg2

    with psycopg2.connect(
        host=host,
        port=port,
        dbname=db,
        user=user,
        password=password,
        connect_timeout=2,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return True


def _duckdb_ready() -> bool:
    from app.duckdb_runtime import connect_duckdb_runtime

    connection = connect_duckdb_runtime()
    connection.close()
    return True


def _readiness_checks() -> dict[str, str]:
    checks: dict[str, str] = {}

    try:
        checks["registry"] = "up" if registry.list_tools() else "down"
    except Exception:
        logger.exception("mcp-infra readyz registry check failed")
        checks["registry"] = "down"

    try:
        _postgres_ready(
            settings.pg_host,
            settings.pg_port,
            settings.pg_db,
            settings.pg_user,
            settings.pg_password,
        )
        checks["postgres"] = "up"
    except Exception:
        logger.exception("mcp-infra readyz postgres check failed")
        checks["postgres"] = "down"

    if settings.pg_gold_user and settings.pg_gold_password:
        try:
            _postgres_ready(
                settings.pg_gold_host,
                settings.pg_gold_port,
                settings.pg_gold_db,
                settings.pg_gold_user,
                settings.pg_gold_password,
            )
            checks["postgres_gold"] = "up"
        except Exception:
            logger.exception("mcp-infra readyz postgres_gold check failed")
            checks["postgres_gold"] = "down"
    else:
        checks["postgres_gold"] = "degraded"

    try:
        checks["duckdb"] = "up" if _duckdb_ready() else "down"
    except Exception:
        logger.exception("mcp-infra readyz duckdb check failed")
        checks["duckdb"] = "down"

    return checks


@app.get("/readyz")
def readyz():
    """Dependency readiness with sanitized public response."""
    checks = _readiness_checks()
    blocking = {name: status for name, status in checks.items() if status == "down"}
    ok = not blocking
    return JSONResponse(
        {"ok": ok, "service": "mcp-infra"},
        status_code=200 if ok else 503,
    )


@app.get("/health")
def health():
    # Public endpoint used by Docker healthchecks and load balancers — keep
    # the response minimal so unauthenticated callers can't fingerprint how
    # many tools / integrations this instance has loaded. Internal callers
    # that need that detail use GET /mcp/tools behind x-api-key.
    return {"status": "ok"}


# ── RAG REST endpoints (used by Studio UI) ─────────────────────────────────────

from app.rag.store import (
    list_sources as _rag_list_sources,
    delete_source as _rag_delete_source,
)
from app.tools.rag import _do_ingest as _rag_do_ingest, _do_search as _rag_do_search


def _rest_security_context(
    internal_service: str,
    body: dict | None = None,
    header_value: str | None = None,
    *,
    allow_refinement_system: bool = False,
) -> dict[str, Any]:
    sec = (body or {}).get("security_context")
    if not isinstance(sec, dict) and header_value:
        try:
            sec = json.loads(header_value)
        except Exception:
            sec = {}
    if (
        allow_refinement_system
        and internal_service == "refinement"
        and isinstance(sec, dict)
        and sec
    ):
        fake_req = InvokeRequest(tool="search_rag", args={}, security_context=sec)
        return _require_context_permission(fake_req, "datasets.read", internal_service)
    if allow_refinement_system and internal_service == "refinement":
        return {
            "trusted": True,
            "source": "refinement",
            "role": "admin",
            "permissions": ["datasets.read", "datasets.write", "cartridges.read"],
            "allowed_buckets": [os.environ.get("MINIO_BUCKET", "lakehouse")],
            "allowed_prefixes": ["raw/", "silver/", "gold/", "cartridges/"],
        }
    fake_req = InvokeRequest(
        tool="search_rag",
        args={},
        security_context=sec if isinstance(sec, dict) else {},
    )
    return _require_context_permission(fake_req, "datasets.read", internal_service)


@app.get("/rag/sources")
async def rag_rest_list_sources(
    kinds: str | None = None,
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    ctx = _rest_security_context(internal_service, header_value=x_security_context)
    _require_rag_context_scope(ctx)
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    sources = await _rag_list_sources(kinds=kind_list, scope=ctx)
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
        security_context=json.loads(x_security_context or "{}")
        if x_security_context
        else {},
    )
    ctx = _require_context_permission(fake_req, "datasets.write", internal_service)
    _require_rag_context_scope(ctx)
    sources = await _rag_list_sources(scope=ctx)
    source = next((s for s in sources if int(s.get("id") or 0) == source_id), None)
    if not source:
        raise HTTPException(404, "Source not found")
    if not _rag_source_allowed(ctx, str(source.get("name") or "")):
        raise HTTPException(403, "RAG source is outside caller scope")
    ok = await _rag_delete_source(source_id, scope=ctx)
    if not ok:
        raise HTTPException(404, "Source not found")
    return {"deleted": True, "source_id": source_id}


@app.post("/rag/search")
async def rag_rest_search(body: dict, internal_service: str = Depends(verify_api_key)):
    ctx = _rest_security_context(internal_service, body)
    _require_rag_context_scope(ctx)
    try:
        result = {
            "results": await _rag_do_search(
                query=body["query"],
                top_k=body.get("top_k", 5),
                source_ids=body.get("source_ids"),
                kinds=body.get("kinds"),
                security_context=ctx,
            )
        }
    except EmbeddingProviderError as exc:
        raise HTTPException(503, detail="Embedding provider unavailable") from exc
    return _filter_rag_payload(result, ctx)


@app.post("/rag/ingest")
async def rag_rest_ingest(
    request: Request, internal_service: str = Depends(verify_api_key)
):
    try:
        body = await read_ingest_body(request)
    except RagIngestError as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=exc.headers) from exc
    fake_req = InvokeRequest(
        tool="ingest_document",
        args={},
        security_context=body.get("security_context") or {},
    )
    ctx = _require_context_permission(fake_req, "datasets.write", internal_service)
    _require_rag_context_scope(ctx)
    source_name = str(body.get("name") or "").strip()
    if not source_name:
        raise HTTPException(400, "RAG source name is required")
    if _rag_is_reserved_source_name(source_name) and not _rag_source_allowed(
        ctx, source_name
    ):
        raise HTTPException(403, "RAG source is outside caller scope")
    if not _rag_is_reserved_source_name(source_name):
        source_name = _scoped_rag_document_name(ctx, source_name)
    content = body.get("content", "")
    if body.get("mime_type") == "application/pdf":
        try:
            content = await extract_pdf_text_with_capacity(content)
        except RagIngestError as exc:
            raise HTTPException(
                exc.status_code, exc.detail, headers=exc.headers
            ) from exc
    else:
        try:
            content = validate_text_content(content)
        except RagIngestError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
    try:
        return await _rag_do_ingest(
            name=source_name,
            content=content,
            description=body.get("description", ""),
            mime_type=body.get("mime_type", "text/plain"),
            kind=body.get("kind", "document"),
            security_context=ctx,
        )
    except EmbeddingProviderError as exc:
        raise HTTPException(503, detail="Embedding provider unavailable") from exc


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
        content = _build_raw_doc(cartridge, name, ctx)
        source = f"raw:{cartridge}:{name}{_rag_scope_suffix(ctx)}"
        desc = f"raw bronze entity {name} ({cartridge})"
    else:
        cartridge_for_dataset = _cartridge_of(name, ctx)
        requested_cartridge = str(body.get("cartridge") or "").strip()
        if (
            requested_cartridge
            and cartridge_for_dataset
            and requested_cartridge != cartridge_for_dataset
        ):
            raise HTTPException(
                404,
                f"dataset '{name}' not found for cartridge '{requested_cartridge}'",
            )
        if cartridge_for_dataset:
            _require_cartridge_scope(ctx, cartridge_for_dataset)
        content, desc = _build_dataset_doc(name, ctx)
        source = f"dataset:{name}{_rag_scope_suffix(ctx)}"

    try:
        r = await _rag_do_ingest(
            name=source,
            content=content,
            description=desc,
            mime_type="text/plain",
            kind="schema",
            security_context=ctx,
        )
    except EmbeddingProviderError as exc:
        raise HTTPException(503, detail="Embedding provider unavailable") from exc
    semantic_result = None
    rebuild_semantic = str(body.get("rebuild_semantic") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if rebuild_semantic and kind == "dataset":
        cartridge_for_semantic = (
            _safe_rag_segment(body.get("cartridge"), "cartridge")
            if body.get("cartridge")
            else _cartridge_of(name, ctx)
        )
    elif rebuild_semantic:
        cartridge_for_semantic = _safe_rag_segment(
            body.get("cartridge") or "replicon", "cartridge"
        )
    else:
        cartridge_for_semantic = None
    if rebuild_semantic and cartridge_for_semantic:
        try:
            semantic_result = await _rebuild_semantic_doc(cartridge_for_semantic, ctx)
        except Exception as exc:  # noqa: BLE001
            request_id = _log_internal_error(exc, "semantic reindex failed")
            semantic_result = {
                "rebuilt": False,
                "error": "Internal Server Error",
                "request_id": request_id,
            }
    return {
        "reindexed": True,
        "source": source,
        **(r if isinstance(r, dict) else {}),
        "semantic": semantic_result,
    }


@app.post("/rag/rebuild-semantic")
async def rag_rest_rebuild_semantic(
    body: dict, internal_service: str = Depends(verify_api_key)
):
    cartridge = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
    ctx = _rest_security_context(internal_service, body, allow_refinement_system=True)
    _require_cartridge_scope(ctx, cartridge)
    return await _rebuild_semantic_doc(cartridge, ctx)


def _cartridge_of(dataset_name: str, ctx: dict[str, Any] | None = None) -> str | None:
    """Resolve cartridge_id for a dataset inside the caller scope."""
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
            if (
                ctx
                and _has_tenant_workspace_scope(ctx)
                and not _is_unscoped_admin_context(ctx)
            ):
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                    (
                        str(ctx.get("tenant_id") or ""),
                        str(ctx.get("workspace_id") or ""),
                    ),
                )
            sql = "SELECT cartridge FROM datasets WHERE name = %s"
            params: list[Any] = [dataset_name]
            if (
                ctx
                and _has_tenant_workspace_scope(ctx)
                and not _is_unscoped_admin_context(ctx)
            ):
                sql += " AND workspace_id = %s::uuid"
                params.append(ctx.get("workspace_id"))
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _duckdb_s3_settings(con, endpoint: str, region: str) -> None:
    import os

    url_style = "vhost" if "amazonaws.com" in endpoint else "path"
    secure = os.environ.get("MINIO_SECURE", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    access_key = (
        os.environ.get("MINIO_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or ""
    )
    secret_key = (
        os.environ.get("MINIO_SECRET_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or ""
    )
    session_token = os.environ.get("AWS_SESSION_TOKEN") or ""
    if access_key and secret_key:
        con.execute(f"""
            SET s3_access_key_id='{access_key}';
            SET s3_secret_access_key='{secret_key}';
        """)
        if session_token:
            con.execute(f"SET s3_session_token='{session_token}';")
    else:
        try:
            con.execute("CALL load_aws_credentials();")
        except Exception as load_exc:  # noqa: BLE001
            try:
                con.execute(f"""
                    CREATE OR REPLACE SECRET omega_s3 (
                        TYPE S3,
                        PROVIDER CREDENTIAL_CHAIN,
                        REGION '{region}'
                    );
                """)
            except Exception as secret_exc:  # noqa: BLE001
                raise RuntimeError(
                    "DuckDB S3 credentials unavailable; configure MINIO/AWS credentials or IAM role"
                ) from (secret_exc or load_exc)
    con.execute(f"""
        SET s3_endpoint='{endpoint}';
        SET s3_url_style='{url_style}';
        SET s3_use_ssl={'true' if secure else 'false'};
        SET s3_region='{region}';
    """)


async def _rebuild_semantic_doc(
    cartridge: str, ctx: dict[str, Any] | None = None
) -> dict:
    """Rebuild `_semantic_<cartridge>` from live schemas + data_catalog."""
    import os
    import duckdb
    import psycopg2
    from datetime import datetime, timezone
    from app.config import settings as s
    from app.publication_heads import publication_epoch, published_heads

    bucket = os.environ.get("MINIO_BUCKET", "")
    cartridge = _safe_rag_segment(cartridge, "cartridge")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    ctx = ctx or {}
    try:
        heads = published_heads(ctx)
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(503, "published catalog is unavailable") from exc
    scope_suffix = _rag_scope_suffix(ctx)
    scoped_raw_glob = ""
    scoped_raw_read = ""
    if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
        tenant_id = str(ctx.get("tenant_id") or "").strip()
        workspace_id = str(ctx.get("workspace_id") or "").strip()
        scoped_raw_glob = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
        scoped_raw_read = scoped_raw_glob

    conn = psycopg2.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
            (str(ctx.get("tenant_id") or ""), str(ctx.get("workspace_id") or "")),
        )
        catalog_scope_sql = ""
        catalog_params: tuple[Any, ...] = ()
        if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
            catalog_scope_sql = (
                " AND scope_status = 'scoped' "
                "AND workspace_id = %s::uuid "
                "AND tenant_id = %s::uuid"
            )
            catalog_params = (
                str(ctx.get("workspace_id") or ""),
                str(ctx.get("tenant_id") or ""),
            )
        dataset_sql = "SELECT name, layer FROM datasets WHERE cartridge = %s"
        dataset_params: list[Any] = [cartridge]
        if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
            dataset_sql += " AND workspace_id = %s::uuid"
            dataset_params.append(str(ctx.get("workspace_id") or ""))
        dataset_sql += " ORDER BY layer, name"
        cur.execute(dataset_sql, tuple(dataset_params))
        datasets = [
            row for row in cur.fetchall() if (str(row[0]), str(row[1])) in heads
        ]
        lineage_by_name = {
            dataset: str(value.get("object_uri") or "")
            for (dataset, layer), value in heads.items()
            if layer == "silver" and value.get("object_uri")
        }
        cur.execute(
            """
            SELECT dataset, column_name, COALESCE(description,''), COALESCE(tags, '{}')
              FROM data_catalog
             WHERE cartridge = %s
        """
            + catalog_scope_sql,
            (cartridge, *catalog_params),
        )
        desc_rows = cur.fetchall()
    conn.close()
    desc_by = {
        (r[0], r[1]): {"description": r[2], "tags": r[3] or []}
        for r in desc_rows
        if any(dataset == str(r[0]) for dataset, _layer in heads)
    }

    from app.duckdb_runtime import connect_duckdb_runtime

    con = connect_duckdb_runtime()
    _duckdb_s3_settings(con, endpoint, region)
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

    def catalog_fields(dataset: str) -> list[tuple[str, str]]:
        return [
            (str(field.get("name") or ""), str(field.get("type") or ""))
            for field in heads.get((dataset, "gold"), {}).get("catalog", [])
            if isinstance(field, dict) and field.get("name") and field.get("type")
        ]

    # restrict file discovery to their tenant/workspace partition. Otherwise
    # the entity list would include entities that only exist in other tenants'
    # data — leaking their presence even if their rows aren't read.
    if scoped_raw_glob:
        glob_pattern = f"s3://{bucket}/raw/{cartridge}/*/{scoped_raw_glob}**/*.parquet"
    else:
        glob_pattern = f"s3://{bucket}/raw/{cartridge}/*/**/*.parquet"
    try:
        raw_rows = con.execute(f"""
            SELECT DISTINCT regexp_extract(file, 'raw/{cartridge}/([^/]+)/', 1) AS entity
            FROM glob('{glob_pattern}')
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
                    f"DESCRIBE SELECT * FROM read_parquet('s3://{bucket}/raw/{cartridge}/{ent}/{scoped_raw_read}**/*.parquet',"
                    f" hive_partitioning=true, union_by_name=true) LIMIT 0"
                ).fetchall()
            except Exception as exc:  # noqa: BLE001
                _log_internal_error(exc, "semantic raw schema lookup failed")
                fields = []
                out.append("_(schema unavailable)_\n")
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
                    fields = catalog_fields(name)
                    if not fields:
                        out.append(
                            "_(schema unavailable; Gold schema must be queried through Refinement)_\n"
                        )
                else:
                    parquet = lineage_by_name.get(name)
                    if not parquet and scoped_raw_read:
                        parquet = (
                            f"s3://{bucket}/silver/{cartridge}/{name}/"
                            f"{scoped_raw_read}data.parquet"
                        )
                    elif not parquet:
                        parquet = (
                            f"s3://{bucket}/silver/{cartridge}/{name}/data.parquet"
                        )
                    fields = con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0"
                    ).fetchall()
            except Exception as exc:  # noqa: BLE001
                _log_internal_error(exc, "semantic dataset schema lookup failed")
                fields = []
                out.append("_(schema unavailable)_\n")
            for col, ty, *_ in fields:
                out.append(fmt_col(name, col, ty))
            out.append("\n")

    content = "".join(out)
    source_name = f"_semantic_{cartridge}:head:{publication_epoch(ctx)}{scope_suffix}"
    ingest = await _rag_do_ingest(
        name=source_name,
        content=content,
        description=f"Modelo semántico consolidado del cartucho {cartridge}",
        mime_type="text/plain",
        kind="document",
        security_context=ctx,
    )
    return {
        "rebuilt": True,
        "source": source_name,
        "raws": len(raws),
        "datasets": len(datasets),
        "chars": len(content),
        **(ingest if isinstance(ingest, dict) else {}),
    }


def _build_raw_doc(
    cartridge: str, entity: str, ctx: dict[str, Any] | None = None
) -> str:
    import os

    bucket = os.environ.get("MINIO_BUCKET", "")
    cartridge = _safe_rag_segment(cartridge, "cartridge")
    entity = _safe_rag_segment(entity, "entity")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    ctx = ctx or {}
    # When the caller is scoped to a tenant/workspace, read ONLY the partition
    # that belongs to that tenant. Otherwise the RAG document would index raw
    # data from every tenant and leak it through semantic search.
    if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
        tenant_id = _safe_rag_segment(str(ctx.get("tenant_id") or ""), "tenant_id")
        workspace_id = _safe_rag_segment(
            str(ctx.get("workspace_id") or ""), "workspace_id"
        )
        path = (
            f"s3://{bucket}/raw/{cartridge}/{entity}/"
            f"tenant_id={tenant_id}/workspace_id={workspace_id}/**/*.parquet"
        )
    else:
        path = f"s3://{bucket}/raw/{cartridge}/{entity}/**/*.parquet"
    from app.duckdb_runtime import connect_duckdb_runtime

    con = connect_duckdb_runtime()
    _duckdb_s3_settings(con, endpoint, region)
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=true, union_by_name=true) LIMIT 0"
        ).fetchall()
        fields = "\n".join(f"- `{r[0]}` : {r[1]}" for r in rows)
    except Exception as exc:  # noqa: BLE001
        _log_internal_error(exc, "raw doc schema lookup failed")
        fields = "(schema unavailable)"
    return (
        f"# Raw entity: {entity}\nLayer: bronze (raw)\nCartridge: {cartridge}\n\n"
        f"## Storage\n`{path}`\n\n## Schema\n{fields}\n"
    )


def _build_dataset_doc(name: str, ctx: dict[str, Any] | None = None) -> tuple[str, str]:
    import psycopg2
    from app.config import settings as s

    name = _safe_rag_segment(name, "dataset")
    ctx = ctx or {}
    conn = psycopg2.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
    )
    try:
        with conn.cursor() as cur:
            if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                    (
                        str(ctx.get("tenant_id") or ""),
                        str(ctx.get("workspace_id") or ""),
                    ),
                )
            sql = (
                "SELECT layer, cartridge, COALESCE(sql_def,''), COALESCE(description,'') "
                "FROM datasets WHERE name = %s"
            )
            params: list[Any] = [name]
            if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
                sql += " AND workspace_id = %s::uuid"
                params.append(ctx.get("workspace_id"))
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, f"dataset '{name}' not found")
    layer, cartridge, sql_def, description = row
    # Reject indexing a dataset whose cartridge is not in the caller's scope.
    if cartridge and not _is_unscoped_admin_context(ctx):
        allowed = {
            str(item).strip()
            for item in (ctx.get("allowed_cartridges") or [])
            if str(item).strip()
        }
        if allowed and "*" not in allowed and cartridge not in allowed:
            raise HTTPException(
                403, f"dataset '{name}' is outside caller cartridge scope"
            )
    scope_note = ""
    if _has_tenant_workspace_scope(ctx) and not _is_unscoped_admin_context(ctx):
        scope_note = (
            f"\n## Scope\n"
            f"- tenant_id: `{ctx.get('tenant_id')}`\n"
            f"- workspace_id: `{ctx.get('workspace_id')}`\n"
        )
    body = (
        f"# Dataset: {name}\nLayer: {layer}\nCartridge: {cartridge}\n\n"
        f"## Description\n{description or '(none)'}\n"
        f"{scope_note}\n"
        f"## SQL\n```sql\n{sql_def}\n```\n"
    )
    return body, f"{layer} dataset {name}"
