"""
MODecissionsPaaS — Refinement Engine
Servicio transversal: cualquier cartucho deposita en Bronze, el engine
transforma a Silver/Gold con términos de negocio y trazabilidad de lineage.
"""
from __future__ import annotations

import logging
import hmac
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp as sql_exp
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse

# Sprint v1.18: structured JSON logs to stdout, with secret redaction.
# Wired up before any other module-level import that might log so the
# first record this service emits is already in JSON format.
from app.logging_config import setup_logging

setup_logging(service_name="refinement")
logger = logging.getLogger(__name__)

from app.duckdb_engine import DuckDBEngine
from app.dataset_store import DatasetStore
from app.llm_sql import GeneratedSQLValidationError, generate_sql
from app.security import get_internal_api_key

DATASETS_DIR = Path("/app/datasets")
engine = DuckDBEngine()
store  = DatasetStore(DATASETS_DIR)
DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SECURITY_CONTEXT_SIGNATURE_FIELD = "_signature"
_SECURITY_CONTEXT_SIGNED_AT_FIELD = "_signed_at"
_SECURITY_CONTEXT_SIGNATURE_VERSION_FIELD = "_signature_version"
_SECURITY_CONTEXT_SIGNATURE_VERSION = "hmac-sha256-v1"
_SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS = 300
_SECURITY_CONTEXT_SIGNATURE_FUTURE_SKEW_SECONDS = 30
_SECURITY_CONTEXT_MIN_SIGNING_KEY_LEN = 32


def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _postgres_dsn() -> str:
    return _normalize_postgres_dsn(os.environ.get("DATABASE_URL", ""))


def _security_context_signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _SECURITY_CONTEXT_MIN_SIGNING_KEY_LEN:
        raise ValueError("SECURITY_CONTEXT_SIGNING_KEY is required")
    for env_name, value in os.environ.items():
        if not (env_name == "INTERNAL_API_KEY" or env_name.startswith("INTERNAL_API_KEY_")):
            continue
        transport_key = (value or "").strip()
        if transport_key and hmac.compare_digest(key, transport_key):
            raise ValueError(f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {env_name}")
    return key


def _canonical_security_context(ctx: dict) -> bytes:
    payload = {key: value for key, value in ctx.items() if key != _SECURITY_CONTEXT_SIGNATURE_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sign_security_context(ctx: dict) -> dict:
    signed = dict(ctx)
    signed["_signed_at"] = int(time.time())
    signed["_signature_version"] = _SECURITY_CONTEXT_SIGNATURE_VERSION
    key = _security_context_signing_key()
    signed["_signature"] = hmac.new(
        key.encode("utf-8"),
        _canonical_security_context(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def _security_context_signature_valid(ctx: dict) -> bool:
    if not ctx.get("trusted"):
        return True
    try:
        key = _security_context_signing_key()
    except ValueError:
        return False
    signature = str(ctx.get(_SECURITY_CONTEXT_SIGNATURE_FIELD) or "")
    if not signature:
        return False
    if ctx.get(_SECURITY_CONTEXT_SIGNATURE_VERSION_FIELD) != _SECURITY_CONTEXT_SIGNATURE_VERSION:
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
    expected = hmac.new(key.encode("utf-8"), _canonical_security_context(ctx), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _validate_dataset_name(name: str) -> None:
    if not DATASET_NAME_RE.fullmatch(name or ""):
        raise HTTPException(400, "Invalid dataset name")


def _migrate_yaml_datasets():
    """One-time migration: import YAML dataset definitions into Postgres if missing."""
    import yaml
    yaml_dir = DATASETS_DIR
    if not yaml_dir.exists():
        return
    for f in yaml_dir.glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not data or not data.get("name"):
                continue
            if data.get("cartridge") == "unknown" or str(data["name"]).startswith("test_"):
                continue
            existing = store.get_dataset(data["name"])
            if existing:
                continue  # already in Postgres
            store.save_dataset({
                "name":        data["name"],
                "layer":       data.get("layer", "silver"),
                "cartridge":   data.get("cartridge", "replicon"),
                "sources":     data.get("sources", []),
                "sql":         data.get("sql", data.get("sql_def", "")),
                "description": data.get("description", ""),
            })
        except Exception:
            logger.exception("failed migrating YAML dataset %s", f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.setup()
    # Sprint v1.21.1 hotfix: the previous `_ensure_semantic_catalog_tables()`
    # call ran CREATE TABLE IF NOT EXISTS for data_catalog / data_relationships
    # at startup. After v1.19 the refinement service connects as
    # `omega_refinement`, which has SELECT/INSERT/UPDATE on those tables but
    # NOT `CREATE ON SCHEMA public` — Postgres checks the CREATE privilege
    # BEFORE evaluating IF NOT EXISTS, so the call fails with
    # InsufficientPrivilege and crashes refinement at boot. Removed: the
    # tables are provisioned by infra/init/19_operational_stability_hotfix.sql
    # (data_catalog, data_relationships) and infra/init/08_workspace_ownership.sql
    # (analytic_apps), with the v1.20 GRANTs in 25_service_roles.sql.
    _migrate_yaml_datasets()
    _seed_catalog_from_existing()
    _seed_relationships()
    yield

INTERNAL_API_KEY = get_internal_api_key()  # legacy fallback, still accepted


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}

# Sprint v1.12: refinement is called by console, workspace, airflow and the
# four cartridges. Each pair now has its own INTERNAL_API_KEY_*_TO_REFINEMENT
# (the 4 cartridges share INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT). The
# legacy shared INTERNAL_API_KEY keeps working during the migration window —
# it gets dropped in a follow-up sprint once every client is verified.
_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str] = {
    "console":   "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
    "workspace": "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
    "airflow":   "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT",
    # All cartridges (replicon, sap_hcm, sap_s4hana, sap_successfactors,
    # salesforce) share one key — they play the same role from refinement's side.
    "replicon":             "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "cartridge-replicon":   "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "hubspot":              "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "cartridge-hubspot":    "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "cartridge-sap_hcm":    "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "cartridge-sap_s4hana": "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "cartridge-sap_successfactors": "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "cartridge-salesforce": "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "salesforce":           "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    # refinement and mcp-infra don't call refinement today, but the old
    # whitelist allowed them so we keep them accepted via legacy key only.
    "refinement": None,
    "mcp-infra":  None,
}

_ADMIN_ROLES = {"admin", "owner", "super_admin"}
_SECURITY_SOURCE_BY_SERVICE = {
    "console": {"console", "agent_runner"},
    "workspace": {"workspace"},
    "airflow": {"airflow", "agent_runner"},
    "hubspot": {"cartridge-hubspot", "hubspot"},
    "cartridge-hubspot": {"cartridge-hubspot", "hubspot"},
    "cartridge-replicon": {"cartridge-replicon", "replicon"},
    "cartridge-sap_hcm": {"cartridge-sap_hcm"},
    "cartridge-sap_s4hana": {"cartridge-sap_s4hana"},
    "cartridge-sap_successfactors": {"cartridge-sap_successfactors"},
    "refinement": {"refinement"},
    "mcp-infra": {"mcp-infra"},
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
_PGDB_SCHEMA_RE = re.compile(r'(?<![A-Za-z0-9_])"?pgdb"?\s*\.', re.IGNORECASE)
_PGGOLD_SCHEMA_TABLE_RE = re.compile(
    r'(?<![A-Za-z0-9_])"?pggold"?\s*\.\s*"?([A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)
_PGGOLD_TABLE_RE = re.compile(
    r'\b(?:from|join)\s+(?:(?:"?pggold"?\s*\.\s*)?)"?([A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)


def _trusted_user_context(body: dict, args: dict) -> dict:
    """Return the only RLS context refinement should trust.

    Preferred path: console/workspace sends a top-level security_context built
    from its authenticated session. Legacy callers may still pass
    args.user_context, but any admin-bypass flag is stripped unless the
    top-level server-owned context is present.
    """
    sec = _security_context(body)
    if sec.get("trusted"):
        role = str(sec.get("role") or "").lower()
        return {
            "id": sec.get("user_id"),
            "email": sec.get("email", ""),
            "role": role,
            "tenant_id": sec.get("tenant_id"),
            "workspace_id": sec.get("workspace_id"),
            "workspace_role": sec.get("workspace_role"),
            "_server_trusted_context": True,
        }

    return {
        "id": None,
        "email": "",
        "role": "anonymous",
        "tenant_id": None,
        "workspace_id": None,
        "workspace_role": None,
        "_server_trusted_context": False,
    }


def _security_context(body: dict) -> dict:
    sec = body.get("security_context") or {}
    if not isinstance(sec, dict):
        return {}
    if not _security_context_signature_valid(sec):
        if sec.get("trusted"):
            raise HTTPException(403, "Invalid signed security_context")
        return {}
    if sec.get("trusted"):
        service = body.get("_verified_internal_service")
        allowed_sources = _SECURITY_SOURCE_BY_SERVICE.get(str(service or ""))
        if not allowed_sources or str(sec.get("source") or "") not in allowed_sources:
            return {}
    return sec


def _body_from_security_header(internal_service: str, header_value: str | None) -> dict:
    sec = {}
    if header_value:
        try:
            sec = json.loads(header_value)
        except Exception:
            sec = {}
    return {"security_context": sec, "_verified_internal_service": internal_service}


def _require_security_permission(body: dict, permission: str) -> dict:
    sec = _security_context(body)
    if not sec.get("trusted"):
        raise HTTPException(403, "trusted security_context required")
    if permission not in set(sec.get("permissions") or []):
        raise HTTPException(403, f"permission required: {permission}")
    return sec


def _is_admin_security_context(sec: dict) -> bool:
    return bool(sec.get("trusted")) and str(sec.get("role") or "").lower() in _ADMIN_ROLES


def _can_read_workspace_wide(sec: dict) -> bool:
    if _is_admin_security_context(sec):
        return True
    workspace_role = str(sec.get("workspace_role") or "").lower()
    return workspace_role in {"workspace_admin", "tenant_admin"}


def _is_unscoped_admin_security_context(sec: dict) -> bool:
    if not _is_admin_security_context(sec):
        return False
    if sec.get("tenant_id") or sec.get("workspace_id"):
        return False
    allowed = {
        str(item).strip()
        for item in (sec.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    return "*" in allowed


def _allowed_prefix_matches(sec: dict, value: str) -> bool:
    """Match explicit prefixes without letting root prefixes grant all data."""
    prefixes = [str(p).lstrip("/").rstrip("/") for p in (sec.get("allowed_prefixes") or [])]
    for prefix in prefixes:
        if not prefix:
            continue
        if len(prefix.split("/")) < 2:
            continue
        if value == prefix or value.startswith(prefix + "/"):
            return True
    return False


def _prefix_allowed(sec: dict, value: str) -> bool:
    value = (value or "").lstrip("/")
    if not value:
        return False
    if _has_invalid_scoped_storage_path(sec, value):
        return False
    if _allowed_prefix_matches(sec, value):
        return True
    if _is_unscoped_admin_security_context(sec):
        return True

    allowed_cartridges = {
        str(item).strip().strip("/")
        for item in (sec.get("allowed_cartridges") or [])
        if str(item).strip().strip("/")
    }
    parts = value.split("/")
    if len(parts) < 2:
        return False
    layer, cartridge = parts[0], parts[1]
    if "*" not in allowed_cartridges and cartridge not in allowed_cartridges:
        return False
    tenant = str(sec.get("tenant_id") or "").strip()
    workspace = str(sec.get("workspace_id") or "").strip()
    scoped = bool(tenant and workspace)

    if layer == "cartridges":
        return True
    if layer == "uploads":
        if not scoped:
            return value.startswith(f"uploads/{cartridge}/")
        return value.startswith(f"uploads/{cartridge}/tenant_id={tenant}/workspace_id={workspace}/")
    if layer == "raw":
        if len(parts) == 3:
            return True
        if scoped and len(parts) >= 5:
            return parts[3] == f"tenant_id={tenant}" and parts[4] == f"workspace_id={workspace}"
        return not scoped and value.startswith(f"raw/{cartridge}/")
    if layer in {"silver", "gold"}:
        if len(parts) == 3:
            return True
        # Logical dataset prefix "layer/cartridge/name/": the trailing slash
        # yields a 4th empty segment. Cartridge is already gated above and
        # workspace isolation is enforced by the caller (_dataset_allowed),
        # so this is the same logical-identity grant as the 3-part form.
        if len(parts) == 4 and parts[3] == "":
            return True
        if scoped and len(parts) >= 5:
            return parts[3] == f"tenant_id={tenant}" and parts[4] == f"workspace_id={workspace}"
        return not scoped and value.startswith(f"{layer}/{cartridge}/")
    return False


def _storage_scope_markers(key: str) -> tuple[str | None, str | None]:
    tenant: str | None = None
    workspace: str | None = None
    for part in str(key or "").strip("/").split("/"):
        if part.startswith("tenant_id="):
            tenant = part.split("=", 1)[1]
        elif part.startswith("workspace_id="):
            workspace = part.split("=", 1)[1]
    return tenant, workspace


def _has_tenant_workspace_scope(sec: dict) -> bool:
    return bool(str(sec.get("tenant_id") or "").strip() and str(sec.get("workspace_id") or "").strip())


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


def _has_foreign_storage_scope(sec: dict, key: str) -> bool:
    """Reject physical S3 paths pinned to a different tenant/workspace.

    Logical paths such as ``raw/replicon/TimeEntry`` deliberately have no
    physical scope marker and are later rewritten by DuckDBEngine. Once a
    caller supplies explicit ``tenant_id=.../workspace_id=...`` markers,
    those markers must match the trusted backend context exactly.
    """
    expected_tenant = str(sec.get("tenant_id") or "").strip()
    expected_workspace = str(sec.get("workspace_id") or "").strip()
    if not (expected_tenant and expected_workspace):
        return False
    tenant, workspace = _storage_scope_markers(key)
    if tenant is None and workspace is None:
        return False
    return tenant != expected_tenant or workspace != expected_workspace


def _has_invalid_scoped_storage_path(sec: dict, key: str) -> bool:
    if not _has_tenant_workspace_scope(sec) or not _is_physical_storage_key(key):
        return False
    expected_tenant = str(sec.get("tenant_id") or "").strip()
    expected_workspace = str(sec.get("workspace_id") or "").strip()
    tenant, workspace = _storage_scope_markers(key)
    return tenant != expected_tenant or workspace != expected_workspace


def _require_cartridge_scope(sec: dict, cartridge_id: str) -> None:
    cartridge_id = str(cartridge_id or "").strip()
    if not cartridge_id:
        raise HTTPException(403, "cartridge_id is required")
    if _is_unscoped_admin_security_context(sec):
        return
    if not _prefix_allowed(sec, f"cartridges/{cartridge_id}/"):
        raise HTTPException(403, "cartridge not allowed")


def _require_source_scope(body: dict, source: str) -> dict:
    sec = _require_security_permission(body, "datasets.read")
    if not _prefix_allowed(sec, source):
        raise HTTPException(403, "source prefix not allowed")
    return sec


def _dataset_allowed(sec: dict, ds: dict) -> bool:
    if _is_unscoped_admin_security_context(sec):
        return True
    workspace_id = str(ds.get("workspace_id") or "")
    sec_workspace = str(sec.get("workspace_id") or "")
    source = str(sec.get("source") or "")
    service_materializer = (
        not sec_workspace
        and (
            source == "airflow"
            or source == "agent_runner"
            or source.startswith("cartridge-")
        )
    )
    if workspace_id and workspace_id != sec_workspace and not service_materializer:
        return False
    created_by_id = ds.get("created_by_id")
    if created_by_id is not None and not service_materializer and not _can_read_workspace_wide(sec):
        user_id = str(sec.get("user_id") or "")
        if str(created_by_id) != user_id:
            return False
    cartridge = str(ds.get("cartridge") or "").strip()
    layer = str(ds.get("layer") or "").strip().lower()
    name = str(ds.get("name") or "").strip()
    return bool(cartridge and layer and name and _prefix_allowed(sec, f"{layer}/{cartridge}/{name}/"))


def _dataset_store_scope(sec: dict) -> dict[str, str | None]:
    workspace_id = str(sec.get("workspace_id") or "").strip()
    if not workspace_id:
        return {}
    tenant_id = str(sec.get("tenant_id") or "").strip() or None
    return {"tenant_id": tenant_id, "workspace_id": workspace_id}


def _get_dataset_scoped(name: str, sec: dict) -> dict | None:
    """Read dataset metadata with tenant/workspace DB scope for RLS."""
    scope = _dataset_store_scope(sec)
    try:
        return store.get_dataset(name, **scope)
    except TypeError as exc:
        # Unit tests often monkeypatch store.get_dataset with a one-argument
        # fake; the production store supports scoped keyword args.
        if "unexpected keyword" in str(exc):
            return store.get_dataset(name)
        raise


def _require_dataset_scope(body: dict, ds: dict, permission: str = "datasets.read") -> dict:
    sec = _require_security_permission(body, permission)
    if not _dataset_allowed(sec, ds):
        raise HTTPException(403, "dataset not allowed")
    return sec


def _dataset_name_from_source(source: str) -> str:
    value = str(source or "").strip().strip("/")
    if "/" in value:
        return value.split("/")[-1]
    return value


def _schema_for_transform_source(body: dict, source: str, ctx: dict) -> dict:
    source = str(source or "").strip()
    if source.startswith("raw/"):
        _require_source_scope(body, source)
        return engine.get_source_schema(source, ctx)
    ds_name = _dataset_name_from_source(source)
    _validate_dataset_name(ds_name)
    sec = _require_security_permission(body, "datasets.read")
    ds = _get_dataset_scoped(ds_name, sec)
    if not ds:
        _require_source_scope(body, source)
        return engine.get_source_schema(source, ctx)
    _require_dataset_scope(body, ds)
    schema = engine.get_dataset_schema(ds, ctx)
    layer = str(ds.get("layer") or "").strip().lower()
    cartridge = str(ds.get("cartridge") or "").strip()
    out = {
        "source": source,
        "dataset": ds_name,
        "layer": layer,
        "cartridge": cartridge,
        "fields": schema.get("fields", []),
    }
    if layer in {"silver", "gold"} and cartridge:
        out["storage_path"] = f"s3://lakehouse/{layer}/{cartridge}/{ds_name}/data.parquet"
    if layer == "gold":
        out["gold_table"] = f"pggold.gold_{ds_name}"
    return out


def _require_transform_source_scope(body: dict, source: str, permission: str = "datasets.read") -> None:
    source = str(source or "").strip()
    sec = _require_security_permission(body, permission)
    if _prefix_allowed(sec, source):
        return
    ds_name = _dataset_name_from_source(source)
    if DATASET_NAME_RE.fullmatch(ds_name or ""):
        ds = _get_dataset_scoped(ds_name, sec)
        if ds and _dataset_allowed(sec, ds):
            return
    raise HTTPException(403, "source prefix not allowed")


def _sql_table_references(sql: str) -> list[tuple[str, str, str]]:
    try:
        tree = sqlglot.parse_one(sql or "", read="duckdb")
    except Exception as exc:
        raise HTTPException(403, "SQL could not be parsed for table scope validation") from exc
    ctes = {str(cte.alias_or_name).lower() for cte in tree.find_all(sql_exp.CTE) if cte.alias_or_name}
    refs: list[tuple[str, str, str]] = []
    for table in tree.find_all(sql_exp.Table):
        name = str(table.name or "").strip('"').strip()
        if not name:
            continue
        catalog = str(table.catalog or "").strip('"').strip().lower()
        db = str(table.db or "").strip('"').strip().lower()
        if not db and name.lower() in ctes:
            continue
        refs.append((catalog, db, name))
    return refs


def _registered_gold_table_allowed(sec: dict, table: str) -> bool:
    if not table.lower().startswith("gold_"):
        return False
    for candidate in (table[5:], table):
        ds = _get_dataset_scoped(candidate, sec)
        if ds and str(ds.get("layer") or "").strip().lower() == "gold" and _dataset_allowed(sec, ds):
            return True
    return False


def _s3_path_to_bucket_key(path: str) -> tuple[str, str]:
    path = (path or "").strip()
    path = path.replace("s3://{bucket}/", f"s3://{engine.minio_bucket}/", 1)
    if not path.startswith("s3://"):
        return "", ""
    rest = path[5:]
    bucket, sep, key = rest.partition("/")
    if not sep:
        return bucket, ""
    return bucket, key


def _s3_path_to_key(path: str) -> str:
    return _s3_path_to_bucket_key(path)[1]


def _mask_single_quoted(sql: str) -> str:
    return _SINGLE_QUOTED_RE.sub("''", sql or "")


def _strip_sql_comments(sql: str) -> str:
    text = sql or ""
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote:
            out.append(ch)
            if ch == quote:
                if nxt == quote:
                    out.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            out.append("\n")
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(text))
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _friendly_duckdb_error(exc: Exception, dataset_name: str) -> tuple[int, dict]:
    error_text = " ".join(str(part) for part in getattr(exc, "args", ()) or (type(exc).__name__,))
    lower = error_text.lower()
    request_id = _log_internal_error(exc, f"duckdb materialization failed for dataset {dataset_name}")
    missing_parquet = (
        ("no files found" in lower and ("read_parquet" in lower or "s3://" in lower))
        or ("404" in lower and ("lakehouse/" in lower or "http://minio" in lower or "minio:" in lower))
    )
    if missing_parquet:
        return 409, {
            "code": "source_files_missing",
            "message": (
                f"Dataset '{dataset_name}' no encuentra archivos Parquet para una fuente. "
                "Ejecuta primero la extracción o materializa la dependencia upstream."
            ),
            "detail": "Error interno",
            "request_id": request_id,
        }
    if "404 (not found)" in lower and ("read_parquet" in lower or "lakehouse/" in lower or "s3://" in lower):
        return 409, {
            "code": "dependency_not_materialized",
            "message": (
                f"Dataset '{dataset_name}' necesita una fuente Silver/Gold que todavía "
                "no existe. Materializa primero sus dependencias y vuelve a intentar."
            ),
            "detail": "Error interno",
            "request_id": request_id,
        }
    if "table with name" in lower and "does not exist" in lower:
        return 409, {
            "code": "dependency_table_missing",
            "message": (
                f"Dataset '{dataset_name}' referencia una tabla que no existe. "
                "Selecciona una fuente válida o materializa la tabla upstream."
            ),
            "detail": "Error interno",
            "request_id": request_id,
        }
    if "catalog error" in lower:
        return 422, {
            "code": "duckdb_catalog_error",
            "message": f"No se pudo resolver el catálogo para '{dataset_name}'.",
            "detail": "Error interno",
            "request_id": request_id,
        }
    return 422, {
        "code": "materialization_failed",
        "message": f"No se pudo materializar '{dataset_name}'.",
        "detail": "Error interno",
        "request_id": request_id,
    }


def _reader_storage_key(path: str) -> str:
    path = (path or "").strip()
    if not path.startswith("s3://"):
        raise HTTPException(403, "SQL storage readers must use s3:// paths")
    key = _s3_path_to_key(path)
    if not key or ".." in key.split("/"):
        raise HTTPException(403, "SQL storage path is not allowed")
    return key


def _storage_path_matches_declared_source(sec: dict, key: str, sources: list[str] | None) -> bool:
    if _has_invalid_scoped_storage_path(sec, key):
        return False
    parts = key.split("/")
    if len(parts) < 3:
        return False
    logical = "/".join(parts[:3])
    declared = {str(source).strip().strip("/") for source in (sources or [])}
    return logical in declared and _prefix_allowed(sec, logical)


def _storage_path_matches_registered_dataset(sec: dict, key: str, sources: list[str] | None = None) -> bool:
    parts = key.split("/")
    if len(parts) < 3 or parts[0] not in {"silver", "gold"}:
        return False
    layer, cartridge, name = parts[:3]
    declared = {str(source).strip().strip("/") for source in (sources or []) if str(source).strip()}
    if declared and name not in declared and f"{layer}/{cartridge}/{name}" not in declared:
        return False
    ds = _get_dataset_scoped(name, sec)
    if not ds:
        return False
    if str(ds.get("layer") or "").strip().lower() != layer:
        return False
    if str(ds.get("cartridge") or "").strip() != cartridge:
        return False
    if not _dataset_allowed(sec, ds):
        return False
    if _has_invalid_scoped_storage_path(sec, key):
        tenant, workspace = _storage_scope_markers(key)
        if not bool(declared) or tenant is not None or workspace is not None:
            return False
        # Registered legacy datasets may still be materialized under the old
        # unpartitioned snapshot layout. Only allow exact dataset-local parquet
        # readers for datasets explicitly declared as sources and already
        # authorized by _dataset_allowed; never allow arbitrary descendants.
        suffix = parts[3:]
        return suffix in (["data.parquet"], ["*.parquet"], ["**", "*.parquet"])
    return True


def _require_sql_path_scope(
    sec: dict,
    path: str,
    *,
    sources: list[str] | None = None,
    allow_registered_dataset_paths: bool = False,
) -> None:
    bucket, _key = _s3_path_to_bucket_key(path)
    key = _reader_storage_key(path)
    allowed_buckets = {str(b) for b in (sec.get("allowed_buckets") or []) if b}
    if allowed_buckets and bucket not in allowed_buckets:
        raise HTTPException(403, "SQL storage bucket not allowed")
    if not allowed_buckets and not _is_admin_security_context(sec) and bucket != engine.minio_bucket:
        raise HTTPException(403, "SQL storage bucket not allowed")
    if _prefix_allowed(sec, key):
        return
    if _storage_path_matches_declared_source(sec, key, sources):
        return
    if allow_registered_dataset_paths and _storage_path_matches_registered_dataset(sec, key, sources):
        return
    raise HTTPException(403, "SQL storage path not allowed")


def _require_sql_storage_scope(
    body: dict,
    sql: str,
    sources: list[str] | None = None,
    *,
    allow_registered_dataset_paths: bool = False,
) -> None:
    sec = _require_security_permission(body, "datasets.read")
    sql = _strip_sql_comments(sql or "")
    masked = _mask_single_quoted(sql)
    if not _SQL_START_RE.search(masked):
        raise HTTPException(403, "SQL must be a read-only SELECT/WITH statement")
    if ";" in masked or _SQL_COMMENT_RE.search(masked) or _SQL_FORBIDDEN_RE.search(masked):
        raise HTTPException(403, "SQL contains unsafe statements or comments")
    if _PGDB_SCHEMA_RE.search(masked):
        raise HTTPException(403, "pgdb schema is not readable through refinement")

    validation_sql = sql
    try:
        validation_sql = engine._scope_storage_sql(
            engine._inject_bucket(sql),
            sources or [],
            _trusted_user_context(body, {}),
        )
    except HTTPException:
        raise
    except Exception:
        validation_sql = sql

    for source in sources or []:
        if _prefix_allowed(sec, str(source)):
            continue
        ds_name = _dataset_name_from_source(str(source))
        ds = (
            _get_dataset_scoped(ds_name, sec)
            if DATASET_NAME_RE.fullmatch(ds_name or "")
            else None
        )
        if ds and _dataset_allowed(sec, ds):
            continue
        raise HTTPException(403, "source prefix not allowed")

    reader_calls = list(_SQL_READER_CALL_RE.finditer(validation_sql))
    direct_readers = list(_SCOPED_READER_RE.finditer(validation_sql))
    if len(reader_calls) != len(direct_readers):
        raise HTTPException(403, "SQL readers must use a direct string literal path")
    for match in direct_readers:
        _require_sql_path_scope(
            sec,
            match.group(3),
            sources=sources,
            allow_registered_dataset_paths=allow_registered_dataset_paths,
        )
    for match in _SQL_STORAGE_LITERAL_RE.finditer(validation_sql):
        _require_sql_path_scope(
            sec,
            match.group(2),
            sources=sources,
            allow_registered_dataset_paths=allow_registered_dataset_paths,
        )
    for match in _DIRECT_STORAGE_SCAN_RE.finditer(validation_sql):
        _require_sql_path_scope(
            sec,
            match.group(2),
            sources=sources,
            allow_registered_dataset_paths=allow_registered_dataset_paths,
        )

    for catalog, db, table in _sql_table_references(sql):
        if catalog:
            raise HTTPException(403, "SQL database/schema is not readable through refinement")
        if db == "pgdb":
            raise HTTPException(403, "pgdb schema is not readable through refinement")
        if db and db != "pggold":
            raise HTTPException(403, "SQL database/schema is not readable through refinement")
        if db == "pggold":
            if not _registered_gold_table_allowed(sec, table):
                raise HTTPException(403, "pggold table is not registered as an allowed dataset")
            continue
        raise HTTPException(
            403,
            "SQL table references must use pggold.gold_<dataset>, CTEs, or scoped read_* file readers",
        )


def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    if not x_internal_service or x_internal_service not in _ALLOWED_SERVICES_TO_KEY_ENV:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    accepted: list[str] = []
    pair_key_env = _ALLOWED_SERVICES_TO_KEY_ENV.get(x_internal_service)
    if pair_key_env:
        pair_key = os.environ.get(pair_key_env)
        if pair_key:
            accepted.append(pair_key)
    # Legacy shared key, still honored during migration.
    if INTERNAL_API_KEY and not _is_production():
        accepted.append(INTERNAL_API_KEY)

    if not any(secrets.compare_digest(x_api_key, k) for k in accepted if k):
        raise HTTPException(status_code=403, detail="Forbidden")
    return x_internal_service

app = FastAPI(title="MODecissionsPaaS Refinement", lifespan=lifespan)


def _internal_error_request_id(request: Request | None = None) -> str:
    candidate = getattr(getattr(request, "state", None), "request_id", None)
    try:
        return str(uuid.UUID(str(candidate)))
    except Exception:
        return str(uuid.uuid4())


def _log_internal_error(exc: Exception, message: str, request: Request | None = None) -> str:
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
    request_id = _log_internal_error(exc, "unhandled refinement exception", request)
    return JSONResponse(
        {"error": "Internal Server Error", "request_id": request_id},
        status_code=500,
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 500:
        request_id = _internal_error_request_id(request)
        logger.error(
            "refinement HTTPException sanitized request_id=%s status=%s",
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


@app.get("/healthz")
async def healthz():
    """Cheap liveness probe for Docker/Kubernetes health checks."""
    return {"ok": True, "service": "refinement"}


async def _readiness_checks() -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        import psycopg2

        with psycopg2.connect(_postgres_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        checks["postgres"] = "up"
    except Exception:
        logger.exception("refinement readyz postgres check failed")
        checks["postgres"] = "down"

    try:
        with engine._duckdb_lock:
            engine._conn().execute("SELECT 1").fetchone()
        checks["duckdb"] = "up"
    except Exception:
        logger.exception("refinement readyz duckdb check failed")
        checks["duckdb"] = "down"
    return checks


@app.get("/readyz")
async def readyz():
    """Dependency readiness with sanitized public response."""
    checks = await _readiness_checks()
    ok = all(status == "up" for status in checks.values())
    return JSONResponse(
        {"ok": ok, "service": "refinement"},
        status_code=200 if ok else 503,
    )


# ── MCP tools (consumidas por la consola y el LLM) ────────────────────────────

@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
async def mcp_tools():
    return {"tools": [

        # ── Bronze discovery ──────────────────────────────────────────────────
        {
            "name": "list_sources",
            "description": (
                "Lista todas las fuentes disponibles en Bronze (MinIO). "
                "Devuelve rutas del tipo raw/{cartridge}/{entity}."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_source_partitions",
            "description": (
                "Devuelve las particiones disponibles (load_date, batch_id) de una fuente Bronze. "
                "Úsala ANTES de save_dataset para conocer la fecha más reciente y obtener "
                "sql_latest — un SQL listo con el filtro correcto a la última carga."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string",
                               "description": "e.g. 'raw/replicon/TimeEntry'"},
                },
                "required": ["source"],
            },
        },
        {
            "name": "preview_source",
            "description": "Muestra schema y filas de muestra de una fuente Bronze.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "limit":  {"type": "integer", "default": 5},
                },
                "required": ["source"],
            },
        },

        # ── SQL generation & preview ──────────────────────────────────────────
        {
            "name": "generate_transform",
            "description": (
                "Usa LLM para generar SQL de transformación dado una descripción en lenguaje "
                "natural y las fuentes/datasets. Renombra columnas a términos de negocio. "
                "Siempre hacer preview_transform antes de save_dataset."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string",
                                    "description": "Qué debe contener el dataset en términos de negocio"},
                    "sources":     {"type": "array", "items": {"type": "string"},
                                    "description": "Fuentes o datasets a usar, e.g. ['raw/replicon/User'] o ['silver_dataset']"},
                    "cartridge":   {"type": "string",
                                    "description": "ID del cartucho origen, e.g. 'replicon'"},
                    "layer":       {"type": "string",
                                    "enum": ["silver", "gold"],
                                    "default": "silver",
                                    "description": "Capa objetivo. Silver exige read_parquet + {latest_date}; Gold lee rutas Silver registradas o pggold.gold_<dataset>."},
                },
                "required": ["description", "sources"],
            },
        },
        {
            "name": "preview_transform",
            "description": (
                "Ejecuta un SQL en DuckDB y devuelve schema + muestra de filas SIN materializar. "
                "Úsala para validar el SQL antes de guardar."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql":     {"type": "string"},
                    "limit":   {"type": "integer", "default": 20},
                    "sources": {"type": "array", "items": {"type": "string"}, "default": []},
                },
                "required": ["sql"],
            },
        },

        # ── Dataset lifecycle ─────────────────────────────────────────────────
        {
            "name": "save_dataset",
            "description": (
                "Guarda la definición de un dataset Silver o Gold. "
                "layer='silver': analítico — Parquet en MinIO. "
                "layer='gold': modelado de negocio y agregación — tabla Postgres. "
                "Incluye cartridge, source_load_date y column_mapping para trazabilidad."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":             {"type": "string",
                                         "description": "Nombre único del dataset, e.g. 'empleados_activos'"},
                    "description":      {"type": "string"},
                    "sql":              {"type": "string"},
                    "layer":            {"type": "string", "enum": ["silver", "gold"]},
                    "sources":          {"type": "array", "items": {"type": "string"}},
                    "cartridge":        {"type": "string",
                                         "description": "ID del cartucho origen, e.g. 'replicon'"},
                    "column_mapping":   {"type": "object",
                                         "description": "Mapeo de columnas origen a términos de negocio"},
                    "source_load_date": {"type": "string",
                                         "description": "Fecha de la partición bronze usada (YYYY-MM-DD)"},
                    "source_batch_id":  {"type": "string"},
                },
                "required": ["name", "sql", "layer"],
            },
        },
        {
            "name": "materialize",
            "description": (
                "Ejecuta el SQL del dataset guardado y escribe el resultado en Silver/Gold. "
                "Registra automáticamente el lineage en silver_lineage."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "list_datasets",
            "description": "Lista todos los datasets Silver/Gold definidos con su estado.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_dataset_definition",
            "description": "Devuelve la definicion completa de un dataset, incluyendo SQL y lineage declarado.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "get_schema",
            "description": "Devuelve schema (campos y tipos) de un dataset definido.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "query_dataset",
            "description": "Consulta un dataset Silver/Gold con filtros opcionales.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":    {"type": "string"},
                    "filters": {"type": "object"},
                    "limit":   {"type": "integer", "default": 100},
                },
                "required": ["name"],
            },
        },

        # ── Schema discovery (para el asistente IA) ──────────────────────────
        {
            "name": "describe_source",
            "description": (
                "Devuelve el schema completo (columnas y tipos) de una fuente Bronze "
                "junto con una muestra de filas. Útil para entender qué datos hay "
                "antes de escribir un SQL de transformación. "
                "source: ruta bronze, e.g. 'raw/replicon/TimeEntry'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "limit":  {"type": "integer", "default": 3},
                },
                "required": ["source"],
            },
        },
        {
            "name": "describe_silver",
            "description": (
                "Devuelve el schema (columnas y tipos) de un dataset Silver "
                "ya materializado leyendo su Parquet en MinIO. "
                "Más rápido que get_schema porque no re-ejecuta el SQL fuente. "
                "name: nombre del dataset, e.g. 'replicon_timeentry_latest'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string"},
                    "limit": {"type": "integer", "default": 3,
                              "description": "Filas de muestra (0 = solo schema)"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "list_datasets_with_schemas",
            "description": (
                "Devuelve todos los datasets registrados (silver/gold) con sus "
                "columnas. Llama esto primero para entender el modelo de datos completo "
                "antes de diseñar un dataset Gold o escribir SQL analítico."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "layer":     {"type": "string",
                                  "description": "Filtrar por capa: silver|gold (vacío=todos)"},
                    "cartridge": {"type": "string",
                                  "description": "Filtrar por cartucho, e.g. 'replicon'"},
                },
            },
        },

        # ── Semantic catalog ──────────────────────────────────────────────────
        {
            "name": "get_data_catalog",
            "description": (
                "Devuelve el catálogo semántico completo: schema con descripciones de negocio "
                "y relaciones entre datasets. UNA sola llamada reemplaza list_datasets_with_schemas "
                "+ múltiples describe_silver. Úsala como primer paso antes de generar cualquier SQL analítico. "
                "Filtra por layer, cartridge o tags para reducir el contexto."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "layer":     {"type": "string",
                                  "description": "Filtrar por capa: silver|gold"},
                    "cartridge": {"type": "string",
                                  "description": "Filtrar por cartucho, e.g. 'replicon'"},
                    "tags":      {"type": "array", "items": {"type": "string"},
                                  "description": "Filtrar por tags, e.g. ['pnl', 'tiempo']"},
                    "datasets":  {"type": "array", "items": {"type": "string"},
                                  "description": "Lista específica de dataset names a incluir"},
                },
            },
        },
        {
            "name": "upsert_catalog_entries",
            "description": (
                "Agrega o actualiza descripciones semánticas, tags y flags en data_catalog. "
                "Úsala para enriquecer el catálogo con contexto de negocio que no se puede "
                "inferir automáticamente del schema."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "Lista de entradas a upsert",
                        "items": {
                            "type": "object",
                            "properties": {
                                "dataset":     {"type": "string"},
                                "column_name": {"type": "string"},
                                "description": {"type": "string"},
                                "tags":        {"type": "array", "items": {"type": "string"}},
                                "is_key":      {"type": "boolean"},
                                "is_metric":   {"type": "boolean"},
                                "example_values": {"type": "array"},
                            },
                            "required": ["dataset", "column_name"],
                        },
                    },
                },
                "required": ["entries"],
            },
        },
        {
            "name": "register_relationship",
            "description": (
                "Registra una relación (JOIN) entre columnas de dos datasets. "
                "Úsala para documentar cómo se unen las tablas del modelo. "
                "El catálogo devuelve estas relaciones junto con el schema para "
                "que el LLM pueda generar JOINs correctos sin adivinar."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "from_dataset": {"type": "string", "description": "Dataset origen"},
                    "from_column":  {"type": "string", "description": "Columna origen"},
                    "to_dataset":   {"type": "string", "description": "Dataset destino"},
                    "to_column":    {"type": "string", "description": "Columna destino"},
                    "join_hint":    {"type": "string", "default": "LEFT",
                                     "description": "Tipo de JOIN sugerido: LEFT|INNER|COALESCE"},
                    "description":  {"type": "string",
                                     "description": "Descripción del significado de la relación"},
                    "transform":    {"type": "string",
                                     "description": "Transformación necesaria al hacer JOIN, e.g. "
                                                    "'CAST(TRY_CAST(from AS DOUBLE) AS BIGINT)::VARCHAR'"},
                },
                "required": ["from_dataset", "from_column", "to_dataset", "to_column"],
            },
        },

        # ── Analytic Apps ─────────────────────────────────────────────────────
        {
            "name": "publish_app",
            "description": (
                "Publica una aplicación analítica HTML generada. El HTML puede usar "
                "fetch('/api/data/{dataset}') para obtener datos del lakehouse en JSON. "
                "Devuelve la URL pública de la app: /apps/{name}. "
                "Úsala después de generar el HTML completo y auto-contenido. "
                "IMPORTANTE: pasa cartridge_id para que la app viaje en el ZIP de export "
                "del cartucho (si la app usa datasets de un cartucho específico)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":         {"type": "string",
                                     "description": "Slug de la app, e.g. 'pnl_revenue_manager'"},
                    "title":        {"type": "string",
                                     "description": "Título descriptivo de la app"},
                    "html":         {"type": "string",
                                     "description": "HTML completo de la app (incluyendo <style> y <script>)"},
                    "description":  {"type": "string",
                                     "description": "Descripción corta de qué muestra la app"},
                    "cartridge_id": {"type": "string",
                                     "description": "Cartridge al que pertenece la app, e.g. 'replicon'. "
                                                    "Determina con qué cartucho viaja en export/import."},
                },
                "required": ["name", "title", "html"],
            },
        },
        {
            "name": "list_apps",
            "description": "Lista todas las aplicaciones analíticas publicadas con sus datasets_used.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_app_details",
            "description": (
                "Devuelve la metadata completa de una app: title, description, "
                "cartridge_id, visibility, datasets_used (los datasets que la app "
                "consume vía /api/data/<name>). Úsala antes de explicar o modificar "
                "una app para saber con qué datos trabaja."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Slug de la app"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_app_html",
            "description": (
                "Devuelve el HTML completo (source) de una app publicada. "
                "USAR SIEMPRE antes de modificar una app: edita el HTML retornado "
                "y vuelve a publicarlo con publish_app. NUNCA generes HTML desde "
                "cero cuando el usuario pida un cambio sobre una app existente."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Slug de la app"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "delete_app",
            "description": "Elimina una aplicación analítica publicada por su slug.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Slug de la app a eliminar"},
                },
                "required": ["name"],
            },
        },

        # ── Dataset delete ────────────────────────────────────────────────────
        {
            "name": "delete_dataset",
            "description": (
                "Elimina un dataset: borra su registro de Postgres, el Parquet de MinIO "
                "para Silver o la tabla Postgres correspondiente para Gold."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },

        # ── Lineage ───────────────────────────────────────────────────────────
        {
            "name": "get_lineage",
            "description": (
                "Devuelve el historial de materializaciones de un dataset: "
                "qué SQL se usó, qué partición bronze fue la fuente, cuántas filas, cuándo."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "Nombre del dataset silver/gold"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["name"],
            },
        },
    ]}


@app.post("/mcp/invoke")
async def mcp_invoke(body: dict, internal_service: str = Depends(verify_api_key)):
    body = {**body, "_verified_internal_service": internal_service}
    tool = body.get("tool")
    args = body.get("args", {})

    if tool == "list_sources":
        sec = _require_security_permission(body, "datasets.read")
        return {"sources": [source for source in engine.list_sources() if _prefix_allowed(sec, source)]}

    if tool == "get_source_partitions":
        _require_source_scope(body, args["source"])
        return engine.get_source_partitions(args["source"], _trusted_user_context(body, args))

    if tool == "preview_source":
        _require_source_scope(body, args["source"])
        return engine.preview_source(args["source"], args.get("limit", 5), _trusted_user_context(body, args))

    if tool == "generate_transform":
        ctx = _trusted_user_context(body, args)
        layer = str(args.get("layer") or "silver").lower()
        schemas = {s: _schema_for_transform_source(body, s, ctx) for s in args["sources"]}
        try:
            sql, explanation = await generate_sql(args["description"], schemas, layer=layer)
        except GeneratedSQLValidationError as exc:
            raise HTTPException(422, f"LLM SQL generation failed validation: {exc}") from exc
        return {"sql": sql, "explanation": explanation, "cartridge": args.get("cartridge"), "layer": layer}

    if tool == "preview_transform":
        _require_sql_storage_scope(
            body,
            args["sql"],
            args.get("sources") or [],
            allow_registered_dataset_paths=True,
        )
        # params: externally-supplied positional parameters (? placeholders) from callers
        # that build parameterized SQL (e.g. api_data_query_filtered).  When params is
        # provided, preview_sql skips internal RLS filter injection.
        caller_params = args.get("params")  # None → apply RLS; list → use as-is
        return engine.preview_sql(args["sql"], args.get("limit", 20), args.get("sources"), _trusted_user_context(body, args), caller_params)

    if tool == "save_dataset":
        sec = _require_security_permission(body, "datasets.write")
        store_scope = _dataset_store_scope(sec)
        args = {
            **args,
            "created_by_id": sec.get("user_id"),
            "tenant_id": sec.get("tenant_id"),
            "workspace_id": sec.get("workspace_id"),
        }
        existing = store.get_dataset(args["name"], **store_scope)
        if existing:
            _require_dataset_scope(body, existing, "datasets.write")
        for source in args.get("sources") or []:
            _require_transform_source_scope(body, source, "datasets.read")
        _require_sql_storage_scope(
            body,
            args.get("sql") or args.get("sql_def") or "",
            args.get("sources") or [],
            allow_registered_dataset_paths=True,
        )
        store.save_dataset(args)
        return {"saved": True, "name": args["name"]}

    if tool == "delete_dataset":
        ds_name = args.get("name") or ""
        _validate_dataset_name(ds_name)
        sec = _require_security_permission(body, "datasets.delete")
        store_scope = _dataset_store_scope(sec)
        ds_existing = store.get_dataset(ds_name, **store_scope)
        if not ds_existing:
            raise HTTPException(404, f"Dataset '{ds_name}' not found")
        _require_dataset_scope(body, ds_existing, "datasets.delete")
        # Block deletion if any published app references this dataset (best-effort
        # via substring scan of the HTML — apps fetch via /api/data/<dataset>).
        try:
            blockers = _pg_exec(
                "SELECT name FROM analytic_apps WHERE html LIKE %s",
                (f"%/api/data/{ds_name}%",), fetch=True,
            ) or []
            if blockers and not args.get("force"):
                return {
                    "error": f"dataset '{ds_name}' is used by {len(blockers)} app(s): "
                             + ", ".join(b["name"] for b in blockers[:5])
                             + (" …" if len(blockers) > 5 else "")
                             + ". Delete those apps first, or pass force=true to override.",
                    "blocking_apps": [b["name"] for b in blockers],
                }
        except Exception:
            logger.exception("failed checking analytic app blockers before deleting dataset %s", ds_name)
        info = store.delete_dataset(ds_name, **store_scope)
        if not info.get("deleted"):
            raise HTTPException(404, info.get("error", "not found"))
        layer     = info["layer"]
        cartridge = info["cartridge"]
        name      = info["name"]
        _validate_dataset_name(name)
        steps     = []
        # Delete MinIO Parquet for Silver datasets.
        if layer == "silver":
            try:
                from minio import Minio
                mc = Minio(
                    engine.minio_endpoint,
                    access_key=engine.minio_access,
                    secret_key=engine.minio_secret,
                    secure=engine.minio_secure,
                )
                # Scope the physical path to the caller's tenant/workspace.
                # Without this a scoped user could delete another tenant's
                # silver parquet (which is also stored under
                # silver/<cart>/<name>/... but with a different tenant_id=
                # /workspace_id= partition).
                user_context = _trusted_user_context(body, args)
                scoped_path = engine._silver_path(cartridge, name, user_context)
                prefix = f"s3://{engine.minio_bucket}/"
                obj_path = (
                    scoped_path[len(prefix):]
                    if scoped_path.startswith(prefix)
                    else f"silver/{cartridge}/{name}/data.parquet"
                )
                mc.remove_object(engine.minio_bucket, obj_path)
                steps.append(f"parquet deleted: s3://{engine.minio_bucket}/{obj_path}")
            except Exception as exc:
                _log_internal_error(exc, "dataset parquet delete failed")
                steps.append("parquet not found or already deleted")
        # Drop Postgres table for Gold datasets.
        if layer == "gold":
            table  = f"gold_{name}"
            try:
                import psycopg2
                dsn  = engine.pg_gold_url.replace("postgresql+psycopg2://", "postgresql://")
                conn = psycopg2.connect(dsn)
                with conn.cursor() as cur:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}"')
                conn.commit()
                conn.close()
                steps.append(f"table dropped: {table}")
            except Exception as exc:
                _log_internal_error(exc, "dataset gold table drop failed")
                steps.append("table drop failed")
        return {"deleted": True, "name": name, "layer": layer, "steps": steps}

    if tool == "materialize":
        sec = _require_security_permission(body, "datasets.write")
        store_scope = _dataset_store_scope(sec)
        ds = store.get_dataset(args["name"], **store_scope)
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        _require_dataset_scope(body, ds, "datasets.write")
        try:
            result = engine.materialize(ds, _trusted_user_context(body, args))
        except (duckdb.Error, ValueError) as exc:
            status_code, detail = _friendly_duckdb_error(exc, args["name"])
            raise HTTPException(status_code=status_code, detail=detail) from exc
        store.update_refresh(args["name"], result["row_count"], **store_scope)
        _reindex_dataset_best_effort(args["name"], body)
        return result

    if tool == "list_datasets":
        sec = _require_security_permission(body, "datasets.read")
        return {
            "datasets": [
                ds
                for ds in store.list_datasets(**_dataset_store_scope(sec))
                if _dataset_allowed(sec, ds)
            ]
        }

    if tool == "get_dataset_definition":
        sec = _require_security_permission(body, "datasets.read")
        ds = store.get_dataset(args["name"], **_dataset_store_scope(sec))
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        _require_dataset_scope(body, ds)
        return ds

    if tool == "get_schema":
        sec = _require_security_permission(body, "datasets.read")
        ds = store.get_dataset(args["name"], **_dataset_store_scope(sec))
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        _require_dataset_scope(body, ds)
        return engine.get_dataset_schema(ds, _trusted_user_context(body, args))

    if tool == "query_dataset":
        sec = _require_security_permission(body, "datasets.read")
        ds = store.get_dataset(args["name"], **_dataset_store_scope(sec))
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        _require_dataset_scope(body, ds)
        _require_sql_storage_scope(
            body,
            ds.get("sql") or ds.get("sql_def") or "",
            ds.get("sources") or [],
            allow_registered_dataset_paths=True,
        )
        return engine.query_dataset(ds, args.get("filters", {}), args.get("limit", 100), _trusted_user_context(body, args))

    if tool == "get_lineage":
        sec = _require_security_permission(body, "datasets.read")
        ds = store.get_dataset(args["name"], **_dataset_store_scope(sec))
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        _require_dataset_scope(body, ds)
        return _get_lineage(args["name"], args.get("limit", 10))

    if tool == "describe_source":
        source = args["source"]
        _require_source_scope(body, source)
        limit  = args.get("limit", 3)
        ctx = _trusted_user_context(body, args)
        schema = engine.get_source_schema(source, ctx)
        preview = engine.preview_source(source, limit, ctx)
        return {
            "source":  source,
            "fields":  schema.get("fields", []),
            "sample":  preview.get("data", []),
            "error":   schema.get("error") or preview.get("error"),
        }

    if tool == "describe_silver":
        name  = args["name"]
        _validate_dataset_name(name)
        limit = args.get("limit", 3)
        sec = _require_security_permission(body, "datasets.read")
        ds    = _get_dataset_scoped(name, sec)
        if not ds:
            raise HTTPException(404, f"Dataset '{name}' not found")
        _require_dataset_scope(body, ds)
        cartridge = ds.get("cartridge", "unknown")
        _validate_dataset_name(cartridge)
        parquet   = engine._silver_path(cartridge, name, _trusted_user_context(body, args))
        try:
            with engine._duckdb_lock:
                con = engine._conn()
                schema_rows = con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0"
                ).fetchall()
                fields = [{"name": r[0], "type": r[1]} for r in schema_rows]
                sample = []
                if limit > 0:
                    cols = [r[0] for r in schema_rows]
                    rows = con.execute(
                        f"SELECT * FROM read_parquet('{parquet}') LIMIT {limit}"
                    ).fetchall()
                    sample = [dict(zip(cols, row)) for row in rows]
            return {"name": name, "layer": ds["layer"], "cartridge": cartridge,
                    "parquet": parquet, "fields": fields, "sample": sample}
        except Exception as exc:
            request_id = _log_internal_error(exc, "dataset schema preview failed")
            return {"name": name, "error": "Internal Error", "request_id": request_id,
                    "hint": "Dataset might not be materialized yet — run materialize first"}

    if tool == "list_datasets_with_schemas":
        sec = _require_security_permission(body, "datasets.read")
        layer_filter     = args.get("layer")
        cartridge_filter = args.get("cartridge")
        store_scope = _dataset_store_scope(sec)
        all_ds = store.list_datasets(**store_scope)
        result = []
        for ds_meta in all_ds:
            if not _dataset_allowed(sec, ds_meta):
                continue
            if layer_filter and ds_meta["layer"] != layer_filter:
                continue
            if cartridge_filter and ds_meta["cartridge"] != cartridge_filter:
                continue
            entry = {
                "name":      ds_meta["name"],
                "layer":     ds_meta["layer"],
                "cartridge": ds_meta["cartridge"],
                "sources":   ds_meta["sources"],
                "row_count": ds_meta["row_count"],
                "fields":    [],
            }
            ds_full = store.get_dataset(ds_meta["name"], **store_scope)
            if ds_full:
                schema = engine.get_dataset_schema(ds_full, _trusted_user_context(body, args))
                entry["fields"] = schema.get("fields", [])
                if schema.get("error"):
                    entry["schema_error"] = schema["error"]
            result.append(entry)
        return {"datasets": result}

    if tool == "get_data_catalog":
        sec = _require_security_permission(body, "datasets.read")
        return _get_data_catalog(
            layer=args.get("layer"),
            cartridge=args.get("cartridge"),
            tags=args.get("tags"),
            datasets=args.get("datasets"),
            security_context=sec,
        )

    if tool == "upsert_catalog_entries":
        sec = _require_security_permission(body, "datasets.write")
        store_scope = _dataset_store_scope(sec)
        for entry in args["entries"]:
            ds = store.get_dataset(entry["dataset"], **store_scope)
            if ds:
                _require_dataset_scope(body, ds, "datasets.write")
        return _upsert_catalog_entries(args["entries"], sec)

    if tool == "register_relationship":
        sec = _require_security_permission(body, "datasets.write")
        store_scope = _dataset_store_scope(sec)
        for dataset_name in (args["from_dataset"], args["to_dataset"]):
            ds = store.get_dataset(dataset_name, **store_scope)
            if ds:
                _require_dataset_scope(body, ds, "datasets.write")
        return _register_relationship(args, sec)

    if tool == "publish_app":
        sec = _require_security_permission(body, "apps.write")
        args = {**args, "created_by_id": sec.get("user_id")}
        cartridge_id = str(args.get("cartridge_id") or "").strip()
        if not cartridge_id and not _is_unscoped_admin_security_context(sec):
            return {"error": "cartridge_id is required for published apps outside admin context"}
        if cartridge_id:
            _require_cartridge_scope(sec, cartridge_id)
        return _publish_app(args, sec)

    if tool == "list_apps":
        sec = _require_security_permission(body, "apps.read")
        return _list_apps(sec)

    if tool == "get_app_details":
        sec = _require_security_permission(body, "apps.read")
        return _get_app_details(args, sec)

    if tool == "get_app_html":
        sec = _require_security_permission(body, "apps.read")
        return _get_app_html(args, sec)

    if tool == "delete_app":
        sec = _require_security_permission(body, "apps.write")
        return _delete_app(args, sec)

    raise HTTPException(400, f"Unknown tool: {tool}")


def _get_lineage(name: str, limit: int) -> dict:
    try:
        import psycopg2
        conn = psycopg2.connect(_postgres_dsn())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT silver_name, cartridge_id, source_entity,
                       source_load_date, source_batch_id, layer,
                       row_count, storage_uri, created_by, created_at
                FROM silver_lineage
                WHERE silver_name = %s
                ORDER BY created_at DESC LIMIT %s
            """, (name, min(limit, 50)))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            if r.get("source_load_date"):
                r["source_load_date"] = str(r["source_load_date"])
        return {"name": name, "lineage": rows}
    except Exception as exc:
        request_id = _log_internal_error(exc, "dataset lineage lookup failed")
        return {"name": name, "error": "Internal Error", "request_id": request_id}


def _pg_set_scope(cur, security_context: dict | None) -> None:
    if not isinstance(security_context, dict) or not security_context.get("trusted"):
        return
    tenant_id = str(security_context.get("tenant_id") or "")
    workspace_id = str(security_context.get("workspace_id") or "")
    cur.execute(
        "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
        (tenant_id, workspace_id),
    )


def _pg_exec(query: str, params=None, fetch=False, security_context: dict | None = None):
    import psycopg2
    conn = psycopg2.connect(_postgres_dsn())
    result = None
    with conn.cursor() as cur:
        _pg_set_scope(cur, security_context)
        cur.execute(query, params)
        if fetch:
            cols   = [d[0] for d in cur.description]
            result = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.commit()
    conn.close()
    return result


def _default_workspace_security_context() -> dict | None:
    rows = _pg_exec(
        """
        SELECT w.id::text AS workspace_id, w.tenant_id::text AS tenant_id
          FROM workspaces w
         ORDER BY w.created_at ASC
         LIMIT 1
        """,
        fetch=True,
    ) or []
    if not rows or not rows[0].get("workspace_id"):
        return None
    return {
        "trusted": True,
        "tenant_id": rows[0].get("tenant_id") or "",
        "workspace_id": rows[0]["workspace_id"],
    }


def _ensure_semantic_catalog_tables() -> None:
    """Sprint v1.21.1 hotfix: this helper used to run CREATE TABLE
    IF NOT EXISTS for data_catalog and data_relationships. After v1.19,
    refinement connects as ``omega_refinement`` which has SELECT/INSERT/
    UPDATE on those tables but no CREATE on schema ``public``. Postgres
    evaluates privileges BEFORE the IF NOT EXISTS branch, so the call
    fails with InsufficientPrivilege and crashes refinement at boot.

    The tables are now exclusively provisioned by:
      - infra/init/19_operational_stability_hotfix.sql (data_catalog,
        data_relationships — column-identical to the old DDL here)
      - infra/init/08_workspace_ownership.sql (analytic_apps; the
        _ensure_apps_table() helper below is also a no-op)

    This function is kept as a no-op for callers that still reference it
    (currently just the lifespan, which we've also cleaned up). The
    callers can be deleted in a follow-up sprint once the seed scripts
    catch up. Keeping a no-op preserves the API surface while removing
    the unsafe DDL."""
    return None


def _get_data_catalog(
    layer: str | None = None,
    cartridge: str | None = None,
    tags: list[str] | None = None,
    datasets: list[str] | None = None,
    security_context: dict | None = None,
) -> dict:
    import json as _json
    store_scope = _dataset_store_scope(security_context) if security_context else {}
    conditions = ["1=1"]
    params: list = []
    if layer:
        conditions.append("c.layer = %s"); params.append(layer)
    if cartridge:
        conditions.append("c.cartridge = %s"); params.append(cartridge)
    if tags:
        conditions.append("c.tags && %s"); params.append(tags)
    if datasets:
        conditions.append("c.dataset = ANY(%s)"); params.append(datasets)
    if security_context and not _is_unscoped_admin_security_context(security_context):
        workspace_id = str(security_context.get("workspace_id") or "").strip()
        tenant_id = str(security_context.get("tenant_id") or "").strip()
        if not workspace_id:
            return {"datasets": {}, "relationships": []}
        conditions.append("c.scope_status = 'scoped'")
        conditions.append("c.workspace_id = %s"); params.append(workspace_id)
        if tenant_id:
            conditions.append("c.tenant_id = %s"); params.append(tenant_id)

    where = " AND ".join(conditions)
    cols = _pg_exec(
        f"""SELECT c.dataset, c.layer, c.cartridge, c.column_name,
                   c.data_type, c.description, c.example_values,
                   c.tags, c.is_key, c.is_metric
            FROM data_catalog c
            WHERE {where}
            ORDER BY c.dataset, c.column_name""",
        params, fetch=True, security_context=security_context,
    ) or []

    # Group by dataset
    datasets_out: dict = {}
    ds_names: set = set()
    for row in cols:
        dn = row["dataset"]
        if security_context:
            ds_meta = store.get_dataset(dn, **store_scope) or {
                "name": dn,
                "layer": row["layer"],
                "cartridge": row["cartridge"],
            }
            if not _dataset_allowed(security_context, ds_meta):
                continue
        ds_names.add(dn)
        if dn not in datasets_out:
            # get dataset description from store
            ds_meta = store.get_dataset(dn, **store_scope) if security_context else store.get_dataset(dn)
            datasets_out[dn] = {
                "layer":       row["layer"],
                "cartridge":   row["cartridge"],
                "description": ds_meta.get("description", "") if ds_meta else "",
                "row_count":   ds_meta.get("row_count") if ds_meta else None,
                "last_refresh": ds_meta.get("last_refresh") if ds_meta else None,
                "columns":     [],
            }
        ev = row["example_values"]
        if isinstance(ev, str):
            try:
                ev = _json.loads(ev)
            except Exception:
                ev = None
        datasets_out[dn]["columns"].append({
            "name":           row["column_name"],
            "type":           row["data_type"],
            "description":    row["description"] or "",
            "tags":           row["tags"] or [],
            "is_key":         row["is_key"],
            "is_metric":      row["is_metric"],
            "example_values": ev,
        })

    # Some Gold datasets are materialized and registered in ``datasets`` but do
    # not yet have per-column rows in ``data_catalog``. They must still appear
    # in the UI catalog with metadata and preview links; schema enrichment can
    # catch up separately when columns are seeded.
    if not tags:
        for ds_meta in store.list_datasets(**store_scope):
            name = str(ds_meta.get("name") or "")
            if not name or name in datasets_out:
                continue
            if layer and str(ds_meta.get("layer") or "") != layer:
                continue
            if cartridge and str(ds_meta.get("cartridge") or "") != cartridge:
                continue
            if datasets and name not in datasets:
                continue
            if security_context and not _dataset_allowed(security_context, ds_meta):
                continue
            datasets_out[name] = {
                "layer":        ds_meta.get("layer") or "",
                "cartridge":    ds_meta.get("cartridge") or "",
                "description":  ds_meta.get("description") or "",
                "row_count":    ds_meta.get("row_count"),
                "last_refresh": ds_meta.get("last_refresh"),
                "columns":      [],
            }
            ds_names.add(name)

    # Fetch relevant relationships
    rels: list = []
    if ds_names:
        rels = _pg_exec(
            """SELECT from_dataset, from_column, to_dataset, to_column,
                      join_hint, description, transform
               FROM data_relationships
               WHERE (from_dataset = ANY(%s) OR to_dataset = ANY(%s))
                 AND (
                     %s::text = ''
                     OR (scope_status = 'scoped' AND workspace_id = %s::uuid)
                 )
               ORDER BY from_dataset, from_column""",
            [
                list(ds_names),
                list(ds_names),
                str((security_context or {}).get("workspace_id") or ""),
                str((security_context or {}).get("workspace_id") or ""),
            ],
            fetch=True,
            security_context=security_context,
        ) or []

    return {"datasets": datasets_out, "relationships": rels}


def _upsert_catalog_entries(entries: list[dict], security_context: dict) -> dict:
    import json as _json
    import psycopg2
    workspace_id = str(security_context.get("workspace_id") or "").strip()
    tenant_id = str(security_context.get("tenant_id") or "").strip()
    if not workspace_id:
        raise HTTPException(403, "workspace scope required for catalog writes")
    conn = psycopg2.connect(_postgres_dsn())
    updated = 0
    with conn.cursor() as cur:
        _pg_set_scope(cur, security_context)
        for e in entries:
            ev = e.get("example_values")
            cur.execute("""
                INSERT INTO data_catalog
                    (dataset, layer, cartridge, column_name, data_type, description,
                     example_values, tags, is_key, is_metric,
                     tenant_id, workspace_id, scope_status, updated_at)
                SELECT %s, COALESCE(d.layer,'silver'), COALESCE(d.cartridge,''),
                       %s, '', %s, %s::jsonb, %s, %s, %s,
                       %s::uuid, %s::uuid, 'scoped', NOW()
                  FROM datasets d
                 WHERE d.name = %s
                   AND d.workspace_id = %s::uuid
                ON CONFLICT (workspace_id, dataset, column_name) WHERE workspace_id IS NOT NULL
                DO UPDATE
                    SET description    = COALESCE(NULLIF(EXCLUDED.description,''), data_catalog.description),
                        example_values = COALESCE(EXCLUDED.example_values, data_catalog.example_values),
                        tags           = CASE WHEN EXCLUDED.tags != '{}' THEN EXCLUDED.tags
                                              ELSE data_catalog.tags END,
                        is_key         = COALESCE(EXCLUDED.is_key,   data_catalog.is_key),
                        is_metric      = COALESCE(EXCLUDED.is_metric, data_catalog.is_metric),
                        tenant_id      = EXCLUDED.tenant_id,
                        scope_status   = 'scoped',
                        updated_at     = NOW()
            """, (
                e["dataset"],
                e["column_name"],
                e.get("description", ""),
                _json.dumps(ev) if ev is not None else None,
                e.get("tags", []),
                e.get("is_key"),
                e.get("is_metric"),
                tenant_id or None,
                workspace_id,
                e["dataset"],
                workspace_id,
            ))
            updated += max(cur.rowcount, 0)
    conn.commit()
    conn.close()
    return {"updated": updated}


def _register_relationship(args: dict, security_context: dict) -> dict:
    workspace_id = str(security_context.get("workspace_id") or "").strip()
    tenant_id = str(security_context.get("tenant_id") or "").strip()
    if not workspace_id:
        raise HTTPException(403, "workspace scope required for catalog relationships")
    rows = _pg_exec("""
        INSERT INTO data_relationships
            (from_dataset, from_column, to_dataset, to_column, join_hint, description, transform,
             tenant_id, workspace_id, scope_status)
        SELECT %s,%s,%s,%s,%s,%s,%s,%s::uuid,%s::uuid,'scoped'
          FROM datasets from_ds
          JOIN datasets to_ds ON to_ds.name = %s
         WHERE from_ds.name = %s
           AND from_ds.workspace_id = %s::uuid
           AND to_ds.workspace_id = %s::uuid
        ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column)
        WHERE workspace_id IS NOT NULL
        DO UPDATE
            SET join_hint   = EXCLUDED.join_hint,
                description = EXCLUDED.description,
                transform   = EXCLUDED.transform,
                tenant_id   = EXCLUDED.tenant_id,
                scope_status = 'scoped'
        RETURNING id
    """, (
        args["from_dataset"],
        args["from_column"],
        args["to_dataset"],
        args["to_column"],
        args.get("join_hint", "LEFT"),
        args.get("description", ""),
        args.get("transform"),
        tenant_id or None,
        workspace_id,
        args["to_dataset"],
        args["from_dataset"],
        workspace_id,
        workspace_id,
    ), fetch=True, security_context=security_context) or []
    if not rows:
        return {"registered": False, "error": "relationship datasets not found in active workspace"}
    return {"registered": True,
            "relation": f"{args['from_dataset']}.{args['from_column']} → {args['to_dataset']}.{args['to_column']}"}


def _ensure_apps_table():
    """Sprint v1.21.1 hotfix: same story as _ensure_semantic_catalog_tables()
    above — ``analytic_apps`` is fully provisioned by:
      - infra/init/08_workspace_ownership.sql (the table itself, with
        cartridge_id / created_by_id / visibility columns)
      - infra/init/11_app_datasets_used.sql (datasets_used TEXT[])
    The runtime CREATE TABLE + ALTER TABLE here failed with
    InsufficientPrivilege under the v1.19 omega_refinement role.
    Kept as a no-op so existing callers don't need to be touched."""
    return None


def _publish_app(args: dict, sec: dict) -> dict:
    name  = (args.get("name")  or "").strip()
    title = (args.get("title") or "").strip()
    html  = args.get("html") or ""
    if not name:
        return {"error": "name is required (slug snake_case, e.g. 'pnl_revenue_manager')"}
    if not title:
        return {"error": "title is required (human-readable app title)"}
    if not html or not html.strip():
        return {"error": "html is required — full self-contained HTML of the app, "
                         "including <style> and <script>. Generate the complete page "
                         "before calling publish_app."}
    if len(html) < 200:
        return {"error": f"html looks too short ({len(html)} chars) — provide the "
                         "complete page, not a placeholder."}
    visibility = args.get("visibility") if args.get("visibility") in ("private", "shared") else "private"
    workspace_id = str(sec.get("workspace_id") or "").strip()
    tenant_id = str(sec.get("tenant_id") or "").strip()
    if not workspace_id:
        return {"error": "workspace scope required for private analytic apps"}
    # Auto-extract dataset names referenced via /api/data/<name>
    import re as _re
    datasets_used = sorted(set(_re.findall(r"/api/data/([a-zA-Z_][a-zA-Z0-9_]*)", html)))
    # Sprint v1.21.1 hotfix: dropped the inline `ALTER TABLE … ADD COLUMN
    # IF NOT EXISTS datasets_used` — omega_refinement has no ALTER on the
    # schema and the call would 500 here. The column is added by
    # infra/init/11_app_datasets_used.sql at DB init time.
    _pg_exec("""
        INSERT INTO analytic_apps (name, title, html, description, cartridge_id,
                                   created_by_id, visibility, datasets_used,
                                   tenant_id, workspace_id, scope_status, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s::uuid, 'scoped', NOW())
        ON CONFLICT (name) DO UPDATE
            SET title         = EXCLUDED.title,
                html          = EXCLUDED.html,
                description   = EXCLUDED.description,
                cartridge_id  = COALESCE(EXCLUDED.cartridge_id, analytic_apps.cartridge_id),
                created_by_id = COALESCE(EXCLUDED.created_by_id, analytic_apps.created_by_id),
                visibility    = EXCLUDED.visibility,
                datasets_used = EXCLUDED.datasets_used,
                tenant_id     = EXCLUDED.tenant_id,
                workspace_id  = EXCLUDED.workspace_id,
                scope_status  = 'scoped',
                updated_at    = NOW()
    """, (name, title, html, args.get("description", ""), args.get("cartridge_id"),
          args.get("created_by_id"), visibility, datasets_used,
          tenant_id or None, workspace_id), security_context=sec)
    return {"published": True, "name": name,
            "cartridge_id": args.get("cartridge_id"),
            "visibility": visibility,
            "datasets_used": datasets_used,
            "url": f"/apps/{name}"}


def _app_visible(sec: dict, row: dict) -> bool:
    if _is_unscoped_admin_security_context(sec):
        return True
    cartridge = str(row.get("cartridge_id") or "").strip()
    is_owner = row.get("created_by_id") is not None and str(row.get("created_by_id")) == str(sec.get("user_id"))
    if not cartridge:
        return is_owner
    if not _prefix_allowed(sec, f"cartridges/{cartridge}/"):
        return False
    visibility = str(row.get("visibility") or "private")
    if visibility in {"shared", "public"}:
        return True
    return is_owner


def _get_app_details(args: dict, sec: dict) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    rows = _pg_exec(
        "SELECT name, title, description, cartridge_id, visibility, datasets_used, created_by_id, updated_at "
        "FROM analytic_apps WHERE name = %s AND (%s::uuid IS NULL OR workspace_id = %s::uuid OR scope_status = 'platform_template')",
        (name, sec.get("workspace_id"), sec.get("workspace_id")), fetch=True, security_context=sec,
    ) or []
    if not rows:
        return {"error": f"app '{name}' not found"}
    r = dict(rows[0])
    if not _app_visible(sec, r):
        return {"error": f"app '{name}' not found"}
    if r.get("updated_at"):
        r["updated_at"] = r["updated_at"].isoformat()
    r["url"] = f"/apps/{r['name']}"
    return r


def _get_app_html(args: dict, sec: dict) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    rows = _pg_exec(
        "SELECT name, title, description, cartridge_id, visibility, datasets_used, created_by_id, html "
        "FROM analytic_apps WHERE name = %s AND (%s::uuid IS NULL OR workspace_id = %s::uuid OR scope_status = 'platform_template')",
        (name, sec.get("workspace_id"), sec.get("workspace_id")), fetch=True, security_context=sec,
    ) or []
    if not rows:
        return {"error": f"app '{name}' not found"}
    row = dict(rows[0])
    if not _app_visible(sec, row):
        return {"error": f"app '{name}' not found"}
    return row


def _list_apps(sec: dict) -> dict:
    try:
        rows = _pg_exec(
            "SELECT name, title, description, cartridge_id, datasets_used, visibility, created_by_id, updated_at "
            "FROM analytic_apps ORDER BY updated_at DESC",
            fetch=True,
            security_context=sec,
        ) or []
        rows = [r for r in rows if _app_visible(sec, r)]
        for r in rows:
            if r.get("updated_at"):
                r["updated_at"] = r["updated_at"].isoformat()
            r["url"] = f"/apps/{r['name']}"
        return {"apps": rows}
    except Exception:
        logger.exception("failed listing analytic apps")
        return {"apps": []}


def _delete_app(args: dict, sec: dict) -> dict:
    name = args["name"]
    existing = _pg_exec(
        "SELECT name, visibility, cartridge_id, created_by_id FROM analytic_apps "
        "WHERE name=%s AND (%s::uuid IS NULL OR workspace_id = %s::uuid OR scope_status = 'platform_template')",
        (name, sec.get("workspace_id"), sec.get("workspace_id")),
        fetch=True,
        security_context=sec,
    ) or []
    if not existing:
        return {"deleted": False, "name": name, "error": "App not found"}
    row = dict(existing[0])
    if not _app_visible(sec, row):
        return {"deleted": False, "name": name, "error": "App not found"}
    is_owner = str(row.get("created_by_id")) == str(sec.get("user_id"))
    if not (_is_unscoped_admin_security_context(sec) or is_owner):
        raise HTTPException(403, "app delete requires admin or owner")
    rows = _pg_exec(
        "DELETE FROM analytic_apps WHERE name=%s AND (%s::uuid IS NULL OR workspace_id = %s::uuid) RETURNING name",
        (name, sec.get("workspace_id"), sec.get("workspace_id")),
        fetch=True,
        security_context=sec,
    ) or []
    if not rows:
        return {"deleted": False, "name": name, "error": "App not found"}
    return {"deleted": True, "name": name}


def _seed_catalog_from_existing() -> int:
    """
    Seed data_catalog with schema from already-materialized datasets.
    Only inserts rows where (dataset, column_name) doesn't exist yet.
    """
    seeded = 0
    try:
        all_ds = store.list_datasets()
        for meta in all_ds:
            name      = meta["name"]
            layer     = meta.get("layer", "silver")
            cartridge = meta.get("cartridge", "")
            try:
                _validate_dataset_name(name)
                if cartridge:
                    _validate_dataset_name(cartridge)
            except HTTPException:
                continue
            ds_full   = store.get_dataset(name)
            col_map   = ds_full.get("column_mapping", {}) if ds_full else {}

            # Get schema
            if layer == "silver":
                parquet = (
                    f"s3://{engine.minio_bucket}/silver/{cartridge}/{name}/data.parquet"
                )
                try:
                    with engine._duckdb_lock:
                        con   = engine._conn()
                        rows  = con.execute(
                            f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0"
                        ).fetchall()
                    fields = [{"name": r[0], "type": r[1]} for r in rows]
                except Exception as exc:
                    logger.info(
                        "schema not materialized yet for %s/%s: %s",
                        layer, name, exc,
                    )
                    continue
            elif layer == "gold":
                try:
                    _validate_dataset_name(name)
                    import psycopg2
                    conn = psycopg2.connect(_normalize_postgres_dsn(
                        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
                    ))
                    with conn.cursor() as cur:
                        cur.execute(f"""
                            SELECT column_name, data_type
                            FROM information_schema.columns
                            WHERE table_name = 'gold_{name}'
                            ORDER BY ordinal_position
                        """)
                        fields = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
                    conn.close()
                except Exception as exc:
                    logger.info("gold schema not materialized yet for %s: %s", name, exc)
                    continue
            else:
                continue

            engine._update_catalog(name, layer, cartridge, fields, col_map)
            seeded += len(fields)
    except Exception:
        logger.exception("failed seeding data_catalog from existing datasets")
    return seeded


def _seed_relationships() -> int:
    """Register known Replicon model relationships if not already present."""
    security_context = _default_workspace_security_context()
    if not security_context:
        logger.info("skip relationship seed: no default workspace")
        return 0
    tenant_id = str(security_context.get("tenant_id") or "").strip() or None
    workspace_id = str(security_context["workspace_id"])
    relationships = [
        # TimeEntry → Proyectos
        ("replicon_timeentry_latest", "projectcode",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Proyecto del tiempo reportado — Project Type, Revenue Manager, % avance", None),
        ("replicon_timeentry_latest", "projectcode",
         "replicon_project_latest", "code",
         "LEFT", "Datos del proyecto: valor de contrato, cliente, estado", None),
        # TimeEntry → Personas
        ("replicon_timeentry_latest", "userid",
         "replicon_resourceallocation_latest", "userid",
         "LEFT", "Tarifa de facturación del consultor en este proyecto",
         "br.userid = te.userid AND br.projectcode = te.projectcode"),
        ("replicon_timeentry_latest", "username",
         "empleados_maestro", "usuario",
         "LEFT", "Costo/hora y tipo de contrato del consultor",
         "LOWER(TRIM(em.usuario)) = LOWER(TRIM(te.username))"),
        # ResourceAllocation → Proyectos
        ("replicon_resourceallocation_latest", "projectcode",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Proyecto de la asignación de recurso", None),
        ("replicon_resourceallocation_latest", "projectcode",
         "replicon_project_latest", "code",
         "LEFT", "Valor de contrato del proyecto asignado", None),
        # Progress History → Proyectos
        ("project_progress_history", "project_code",
         "replicon_project_latest", "code",
         "LEFT", "Valor de contrato para calcular revenue FPP por incremento de avance", None),
        ("project_progress_history", "project_code",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Revenue Manager y tipo de proyecto para el historial de avance", None),
        # Facturación → Proyectos (Project Code viene como float string: '29630595633.0')
        ("replicon_projectbilling_curated", "Project Code",
         "replicon_project_latest", "code",
         "LEFT", "Proyecto de la factura — normalizar Project Code: CAST(TRY_CAST(TRY_CAST(\"Project Code\" AS DOUBLE) AS BIGINT) AS VARCHAR)",
         "CAST(TRY_CAST(TRY_CAST(b.\"Project Code\" AS DOUBLE) AS BIGINT) AS VARCHAR) = p.code"),
        # BillingItem → Proyectos
        ("replicon_billingitem_latest", "projectcode",
         "replicon_project_latest", "code",
         "LEFT", "Proyecto del item de facturación", None),
        ("replicon_billingitem_latest", "projectcode",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Revenue Manager y tipo del proyecto facturado", None),
    ]
    seeded = 0
    for rel in relationships:
        try:
            _pg_exec("""
                INSERT INTO data_relationships
                    (from_dataset, from_column, to_dataset, to_column,
                     join_hint, description, transform, tenant_id, workspace_id, scope_status)
                SELECT %s,%s,%s,%s,%s,%s,%s,%s::uuid,%s::uuid,'scoped'
                  FROM datasets from_ds
                  JOIN datasets to_ds ON to_ds.name = %s
                 WHERE from_ds.name = %s
                   AND from_ds.workspace_id = %s::uuid
                   AND to_ds.workspace_id = %s::uuid
                ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column)
                WHERE workspace_id IS NOT NULL
                DO NOTHING
            """, (
                *rel,
                tenant_id,
                workspace_id,
                rel[2],
                rel[0],
                workspace_id,
                workspace_id,
            ), security_context=security_context)
            seeded += 1
        except Exception:
            logger.exception(
                "failed seeding relationship %s.%s -> %s.%s",
                rel[0], rel[1], rel[2], rel[3],
            )
    return seeded


# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/datasets")
async def list_datasets(
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    body = _body_from_security_header(internal_service, x_security_context)
    sec = _require_security_permission(body, "datasets.read")
    datasets = [
        d
        for d in store.list_datasets(**_dataset_store_scope(sec))
        if _dataset_allowed(sec, d)
    ]
    _annotate_staleness(datasets, sec)
    return {"datasets": datasets}


def _reindex_dataset_best_effort(name: str, auth_body: dict | None = None) -> None:
    """Keep RAG schema docs fresh without blocking materialization."""
    try:
        import httpx as _httpx

        mcp = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010").rstrip("/")
        key = os.environ.get("INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA") or ""
        if not key and not _is_production():
            key = os.environ.get("INTERNAL_API_KEY") or ""
        headers = {"x-internal-service": "refinement", "x-api-key": key} if key else {}
        payload: dict = {"kind": "dataset", "name": name}
        if auth_body and isinstance(auth_body.get("security_context"), dict):
            payload["security_context"] = _sign_security_context({
                **auth_body["security_context"],
                "source": "refinement",
            })
        with _httpx.Client(timeout=15, headers=headers) as client:
            response = client.post(f"{mcp}/rag/reindex", json=payload)
        if response.status_code == 404:
            logger.debug("dataset RAG reindex endpoint unavailable for %s", name)
            return
        if response.status_code >= 400:
            logger.debug(
                "dataset RAG reindex skipped for %s: status=%s",
                name,
                response.status_code,
            )
    except Exception:
        logger.debug("dataset RAG reindex skipped for %s", name, exc_info=True)


def _annotate_staleness(datasets: list[dict], sec: dict | None = None) -> None:
    """Compute is_stale + staleness_reason from source freshness."""
    import psycopg2

    by_name = {d["name"]: d for d in datasets}
    raw_loads: dict[str, str] = {}
    try:
        dsn = _postgres_dsn()
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            if sec:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (sec.get("tenant_id") or "",))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (sec.get("workspace_id") or "",))
            cur.execute(
                "SELECT cartridge_id, entity, MAX(finished_at) "
                "FROM pipeline_runs WHERE status='success' "
                "GROUP BY cartridge_id, entity"
            )
            for cartridge, entity, ts in cur.fetchall():
                raw_loads[f"raw/{cartridge}/{entity}".lower()] = ts.isoformat() if ts else None
    except Exception:
        raw_loads = {}

    def _src_last(src: str) -> str | None:
        source = (src or "").strip()
        source_lower = source.lower()
        if source_lower.startswith("raw/"):
            return raw_loads.get(source_lower)
        candidates = [source, source.replace("silver_", "", 1), source.replace("gold_", "", 1)]
        if "/" in source:
            candidates.append(source.rsplit("/", 1)[-1])
        for candidate in candidates:
            dataset = by_name.get(candidate)
            if dataset:
                return dataset.get("last_refresh")
        return None

    for dataset in datasets:
        last_refresh = dataset.get("last_refresh") or ""
        stale = False
        reason = None
        for source in (dataset.get("sources") or []):
            source_last = _src_last(source)
            if source_last and (not last_refresh or source_last > last_refresh):
                stale = True
                reason = f"{source} actualizado {source_last} (este: {last_refresh or 'nunca'})"
                break
        dataset["is_stale"] = stale
        if reason:
            dataset["staleness_reason"] = reason


@app.get("/datasets/{name}/definition")
async def dataset_definition(
    name: str,
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    body = _body_from_security_header(internal_service, x_security_context)
    sec = _require_security_permission(body, "datasets.read")
    ds = _get_dataset_scoped(name, sec)
    if not ds:
        raise HTTPException(404)
    _require_dataset_scope(body, ds)
    return ds


@app.get("/datasets/{name}/schema")
async def dataset_schema(
    name: str,
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    body = _body_from_security_header(internal_service, x_security_context)
    sec = _require_security_permission(body, "datasets.read")
    ds = _get_dataset_scoped(name, sec)
    if not ds:
        raise HTTPException(404)
    _require_dataset_scope(body, ds)
    return engine.get_dataset_schema(ds, _trusted_user_context(body, {}))


@app.get("/datasets/{name}/data", dependencies=[Depends(verify_api_key)])
async def dataset_data(name: str, limit: int = 100):
    # This GET endpoint cannot carry a per-user context, so it must not
    # return data — callers must use POST /mcp/invoke with tool=query_dataset
    # and a forwarded user_context. Returning data here would silently bypass
    # RLS for every dataset whose SQL doesn't reference the pggold schema.
    raise HTTPException(
        status_code=410,
        detail="Use POST /mcp/invoke with tool=query_dataset and user_context.",
    )


@app.post("/datasets/{name}/refresh")
async def refresh_dataset(
    name: str,
    x_security_context: str | None = Header(None, alias="x-security-context"),
    internal_service: str = Depends(verify_api_key),
):
    auth_body = _body_from_security_header(internal_service, x_security_context)
    sec = _require_security_permission(auth_body, "datasets.write")
    store_scope = _dataset_store_scope(sec)
    ds = store.get_dataset(name, **store_scope)
    if not ds:
        raise HTTPException(404)
    _require_dataset_scope(auth_body, ds, "datasets.write")
    result = engine.materialize(ds, _trusted_user_context(auth_body, {}))
    store.update_refresh(name, result["row_count"], **store_scope)
    _reindex_dataset_best_effort(name, auth_body)
    return result


@app.post("/refresh-by-source")
async def refresh_by_source(
    body: dict,
    internal_service: str = Depends(verify_api_key),
):
    """
    Re-materializa todos los datasets Silver/Master cuyas fuentes incluyen
    la ruta Bronze indicada. Llamado automáticamente por el job_runner
    tras completar una extracción.

    Body: {"source": "raw/replicon/TimeEntry"}
    """
    source = body.get("source", "").strip()
    if not source:
        raise HTTPException(400, "source is required")
    auth_body = {**body, "_verified_internal_service": internal_service}
    _require_source_scope(auth_body, source)
    _require_security_permission(auth_body, "datasets.write")

    sec = _require_security_permission(auth_body, "datasets.read")
    store_scope = _dataset_store_scope(sec)
    all_ds   = store.list_datasets(**store_scope)
    ctx = _trusted_user_context(auth_body, {})
    matched  = [d for d in all_ds if source in (d.get("sources") or []) and _dataset_allowed(sec, d)]
    results  = []

    for meta in matched:
        ds = store.get_dataset(meta["name"], **store_scope)
        if not ds or ds.get("layer") == "gold":
            continue  # gold depende de Silver, no de Bronze directamente
        try:
            result = engine.materialize(ds, ctx)
            store.update_refresh(meta["name"], result["row_count"], **store_scope)
            _reindex_dataset_best_effort(meta["name"], auth_body)
            results.append({"name": meta["name"], "status": "ok",
                             "row_count": result["row_count"],
                             "storage_uri": result["storage_uri"]})
        except Exception as exc:
            request_id = _log_internal_error(exc, "dataset materialize_all item failed")
            results.append({
                "name": meta["name"],
                "status": "error",
                "error": "Internal Error",
                "request_id": request_id,
            })

    errors = [r for r in results if r.get("status") == "error"]
    if errors and not body.get("allow_partial"):
        raise HTTPException(
            502,
            {
                "source": source,
                "error": "refresh-by-source failed",
                "results": results,
            },
        )

    return {"source": source, "refreshed": len(results), "results": results}
