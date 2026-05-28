"""
ΩMEGA by EPIUSE — Console
MCP-first: descubre y orquesta MCP servers registrados.
UI minimalista: chat con asistente + estado de servidores MCP.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

# Sprint v1.18: structured JSON logs to stdout, with secret redaction
# applied to every record. Imported and called here (rather than at the
# bottom of imports) so the logger configured below is the JSON one
# from the very first record.
from app.logging_config import setup_logging  # noqa: E402

setup_logging(service_name="console")

import httpx
from fastapi import Body, FastAPI, HTTPException, UploadFile, File, Request, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _app_env() -> str:
    return os.environ.get("APP_ENV", "production").strip().lower()


def _is_production_env() -> bool:
    return _app_env() in {"production", "prod"}


def _public_url(
    env_name: str,
    *,
    fallback_env: str | None = None,
    development_default: str = "",
) -> str:
    raw = os.environ.get(env_name)
    if not raw and fallback_env:
        raw = os.environ.get(fallback_env)
    if raw:
        return raw.rstrip("/")
    if _is_production_env():
        logger.warning("%s is not configured in production; omitting localhost fallback", env_name)
        return ""
    return development_default.rstrip("/")


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _service_url(env_name: str, docker_default: str, local_default: str) -> str:
    raw = os.environ.get(env_name)
    if raw:
        return raw.rstrip("/")
    return docker_default.rstrip("/") if _running_in_container() else local_default.rstrip("/")


def _vault_url() -> str:
    return _service_url("VAULT_URL", "http://vault:8300", "http://127.0.0.1:8300")


from app.services import mcp_registry, assistant, studio_assistant, token_store, job_service, tool_manifest
from app.services import cartridge_service
from app.services import marketplace_service
from app.services import agent_service as _agents
from app.services import agent_runtime as _agent_runtime
from app.services import auth as _auth
from app.services import tokens as _tokens
from app.services import email_service as _email
from app.services import vpn_service as _vpn
from app.services.jwt_auth import JWTAuthError, create_access_token, decode_access_token, verify_access_token_async
from app.services.csrf import CSRF_COOKIE_NAME, require_csrf, set_csrf_cookie, clear_csrf_cookie
from app.security import get_internal_api_key, required_secret
from app.dependencies import (
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_WORKSPACE_ADMIN,
    _workspace_cartridges,
    _workspace_memberships,
    get_current_global_user,
    get_current_user as get_current_user_dependency,
    require_any_role,
    require_authenticated,
    require_global_any_role,
    require_role,
)
from app.services.auth import verify_internal_api_key
from app.services import audit_service as _audit
from app.services.permissions import ROLE_DEFINITIONS, get_effective_permissions, has_permission, require_permission
from app.services.security_context import build_security_context, rls_user_context
from app.middleware.request_id import request_id_var


async def _periodic_health_check():
    """Wait for cartridges to boot, then re-check every 60 s."""
    await asyncio.sleep(12)          # grace period for sibling containers
    while True:
        try:
            await mcp_registry.health_check_all()
        except Exception:
            logger.debug("Periodic health check failed", exc_info=True)
        await asyncio.sleep(60)


_MAIN_POOL: "asyncpg.Pool | None" = None


def _db_dsn() -> str:
    return (
        os.environ.get("DATABASE_URL", "")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgres+psycopg2://", "postgresql://")
    )


async def _get_db_pool() -> "asyncpg.Pool":
    global _MAIN_POOL
    import asyncpg as _asyncpg
    if _MAIN_POOL is None:
        dsn = _db_dsn()
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured (console)")
        _MAIN_POOL = await _asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=10)
    return _MAIN_POOL


async def _close_main_pool() -> None:
    global _MAIN_POOL
    if _MAIN_POOL is not None:
        await _MAIN_POOL.close()
        _MAIN_POOL = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_rate_limiter()
    await mcp_registry.startup()
    # Sprint v1.20: idempotent backfill of cartridge_dags.source_code from
    # on-disk .py files. Non-fatal — a seeding failure logs a warning but
    # never blocks startup (Studio just keeps showing "Fuente no encontrada"
    # for the affected cartridge until the next boot).
    try:
        from app.services.seed_dag_sources import seed_missing_dag_sources
        pool = await _get_db_pool()
        await seed_missing_dag_sources(pool)
    except Exception as e:
        logger.warning(
            "[startup] dag source seeding failed (non-fatal): %s", e, exc_info=True,
        )
    try:
        from app.services.seed_packaged_datasets import seed_packaged_datasets
        pool = await _get_db_pool()
        await seed_packaged_datasets(pool)
    except Exception as e:
        logger.warning(
            "[startup] packaged dataset seeding failed (non-fatal): %s", e, exc_info=True,
        )
    try:
        from app.services.seed_packaged_hints import seed_packaged_hints
        pool = await _get_db_pool()
        await seed_packaged_hints(pool)
    except Exception as e:
        logger.warning(
            "[startup] packaged hint seeding failed (non-fatal): %s", e, exc_info=True,
        )
    try:
        from app.services.seed_packaged_apps import seed_packaged_apps
        pool = await _get_db_pool()
        await seed_packaged_apps(pool)
    except Exception as e:
        logger.warning(
            "[startup] packaged app seeding failed (non-fatal): %s", e, exc_info=True,
        )
    task = asyncio.create_task(_periodic_health_check())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _auth.close_pool()
        await _tokens.close_pool()
        await job_service.close_pool()
        await token_store.close_pool()
        await mcp_registry.close_pool()
        await cartridge_service.close_pool()
        await _close_main_pool()
        await _close_dec_pool()



INTERNAL_API_KEY = get_internal_api_key()
# INTERNAL_API_KEY is cached at service startup. Rotating it requires restarting
# Console and peer services so all in-process values are refreshed together.


def _key_for(server: str) -> str:
    """Sprint v1.12: pick the per-pair INTERNAL_API_KEY_CONSOLE_TO_<SERVER>
    secret if present, falling back to the shared legacy INTERNAL_API_KEY so
    that a half-migrated stack keeps working. ``server`` must be one of
    ``REFINEMENT`` / ``VAULT`` / ``MCP_INFRA``."""
    pair = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        return pair
    if _is_production_env():
        raise RuntimeError(f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server}; legacy fallback disabled in production")
    if INTERNAL_API_KEY:
        return INTERNAL_API_KEY
    raise RuntimeError(f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server} (no legacy fallback either)")


def _hdr_for(server: str) -> dict[str, str]:
    """Headers for an outbound internal call from console to ``server``."""
    headers = {"x-api-key": _key_for(server), "x-internal-service": "console"}
    rid = request_id_var.get()
    if rid:
        headers["x-request-id"] = rid
    return headers


def _is_internal_request(request: Request) -> bool:
    supplied = (
        request.headers.get("x-api-key")
        or request.headers.get("x-internal-api-key")
        or ""
    )
    service = (request.headers.get("x-internal-service") or "").strip().lower()
    if service != "console" or not supplied:
        return False
    console_to_console = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CONSOLE", "")
    if console_to_console and secrets.compare_digest(str(supplied), str(console_to_console)):
        return True
    if _is_production_env():
        return False
    return secrets.compare_digest(str(supplied), str(INTERNAL_API_KEY))


def _is_security_admin_context(ctx: dict) -> bool:
    role = str(ctx.get("role") or "").lower()
    if not (bool(ctx.get("trusted")) and role in {ROLE_ADMIN, "owner", "super_admin"}):
        return False
    if ctx.get("tenant_id") or ctx.get("workspace_id"):
        return False
    allowed = {str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()}
    return "*" in allowed


def _is_replicon_vault_reveal_request(request: Request) -> bool:
    """Allow Replicon DAGs to reveal their own Vault connection at runtime."""
    if request.method != "GET":
        return False
    if not re.fullmatch(r"/api/vault/connections/replicon/[^/]+/reveal", request.url.path):
        return False

    service = (request.headers.get("x-internal-service") or "").strip().lower()
    if service != "replicon":
        return False
    supplied = (
        request.headers.get("x-api-key")
        or request.headers.get("x-internal-api-key")
        or ""
    )
    if not supplied:
        return False

    accepted = [os.environ.get("INTERNAL_API_KEY_REPLICON_TO_CONSOLE", "")]
    if not _is_production_env():
        accepted.append(INTERNAL_API_KEY)
    return any(secrets.compare_digest(str(supplied), key) for key in accepted if key)


def _internal_service_user() -> dict:
    return {
        "id": 0,
        "email": "internal@omega.local",
        "role": ROLE_ADMIN,
        "workspace_role": None,
        "active_workspace_id": None,
    }


def _user_payload(user: dict | None) -> dict | None:
    if not user:
        return None
    payload = dict(user)
    payload["permissions"] = sorted(get_effective_permissions(payload))
    return payload


async def _internal_or_authenticated(request: Request) -> dict:
    if _is_internal_request(request):
        return _internal_service_user()
    return await require_authenticated(request)


def _is_internal_service_actor(user: dict | None) -> bool:
    if not user:
        return False
    return int(user.get("id") or -1) == 0 and user.get("email") == "internal@omega.local"


def _require_effective_permission(user: dict | None, permission: str) -> None:
    if not has_permission(user, permission):
        raise HTTPException(status_code=403, detail=f"permission required: {permission}")


app = FastAPI(title="ΩMEGA by EPIUSE Console", lifespan=lifespan)


def _allowed_origins() -> list[str]:
    # Static console-next is served same-origin by FastAPI on :8000.
    # Keep workspace :8001 in the local default because it remains a
    # legitimate browser client for credentialed workspace flows.
    # Production MUST override via the env var (see prod fail-closed guard).
    raw_env = os.environ.get("ALLOWED_ORIGINS")
    app_env = os.environ.get("APP_ENV", "production").lower()

    # R-Mac-3 review (Security P1): a prod deployment that ships
    # without ALLOWED_ORIGINS used to silently whitelist localhost
    # for credentialed requests. Fail closed instead — mirror the
    # INTERNAL_API_KEY prod guard pattern in console/app/security.py.
    if raw_env is None and app_env in {"production", "prod"}:
        raise RuntimeError(
            "ALLOWED_ORIGINS must be set in production (APP_ENV="
            f"{app_env}). Refusing to fall back to the localhost "
            "default with allow_credentials=True."
        )

    raw = raw_env if raw_env is not None else "http://localhost:8000,http://localhost:8001"
    # Phase-0 P1 fix: ALLOWED_ORIGINS="" (env var set but empty) used to be
    # accepted silently and produced an empty allowlist. That combination
    # plus allow_credentials=True is exactly the misconfiguration the
    # production fail-closed guard above is meant to catch. Treat empty
    # string the same as unset in prod, and warn loudly in dev/test.
    if raw_env is not None and not raw_env.strip():
        if app_env in {"production", "prod"}:
            raise RuntimeError(
                "ALLOWED_ORIGINS is set to an empty value in production. "
                "Either unset it (we will refuse to start) or list the "
                "exact origins allowed for credentialed requests."
            )
        logger.warning(
            "ALLOWED_ORIGINS is set but empty; falling back to the "
            "localhost default. This is only safe in dev/test."
        )
        raw = "http://localhost:8000,http://localhost:8001"
    origins: list[str] = []
    for chunk in raw.split(","):
        origin = chunk.strip()
        if not origin:
            continue
        if origin == "*":
            # R-Mac-3 review (DevOps P2): wildcards are incompatible
            # with allow_credentials=True (browsers reject the combo
            # outright). Silently dropping the entry left operators
            # debugging an empty allowlist with no clue why — log so
            # the cause is visible in startup logs.
            logger.warning(
                "ALLOWED_ORIGINS=* is incompatible with "
                "allow_credentials=True; ignoring wildcard entry"
            )
            continue
        origins.append(origin)
    return origins


# v1.44.3.2.2 R-Mac-3 (CORS ordering hotfix): the CORSMiddleware
# registration USED to live here at module-load time, which made it
# the FIRST middleware on user_middleware and therefore the
# INNERMOST in Starlette's reversed stack. Symptom Codex curl'd on
# the Mac:
#   $ curl -i -H "Origin: http://localhost:8001" http://localhost:8000/auth/login
#   → access-control-allow-credentials: true   ✓
#   → access-control-allow-origin:    MISSING  ✗
#
# Root cause: with CORS innermost, the OUTER auth_middleware /
# security_headers_middleware can short-circuit responses (401 /
# redirect / preflight 405) before reaching CORS, so CORS never gets
# to add Allow-Origin. Even on public paths, OPTIONS preflights
# pass through auth first.
#
# The fix moves the registration to the END of the module (after
# RequestIDMiddleware), so CORS ends up OUTERMOST in the final
# ASGI stack. See the matching block near the bottom of this file.
# This call site stays as a no-op so existing line-number references
# in comments / commit messages don't drift; the real registration
# is the only effective one.

# Sprint v1.41.1 / v1.42.1 — Request correlation IDs. The actual
# ``app.add_middleware(RequestIDMiddleware)`` call lives at the bottom
# of this module, AFTER the two ``@app.middleware("http")`` decorators
# (security headers + auth). Starlette builds its middleware stack by
# iterating ``user_middleware`` in reverse, so the LAST registered
# middleware ends up outermost. The auditor's 401-vs-X-Request-ID
# finding was caused by registering it here (which left it inside the
# auth wrapper, so 401s never reached its send-wrapper).
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _validate_dataset_name(dataset: str) -> None:
    if not DATASET_NAME_RE.fullmatch(dataset or ""):
        raise HTTPException(400, "Invalid dataset name")


def _rls_user_context(user: dict | None) -> dict:
    return rls_user_context(user)


def _runtime_user(user) -> dict | None:
    return user if isinstance(user, dict) else None


async def _call_with_optional_user(fn, *args, user=None):
    runtime_user = _runtime_user(user)
    try:
        params = inspect.signature(fn).parameters
        accepts_user = (
            "user" in params
            or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
        )
    except (TypeError, ValueError):
        accepts_user = False
    result = fn(*args, user=runtime_user) if accepts_user else fn(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _mcp_payload(tool: str, args: dict, user: dict | None = None) -> dict:
    payload = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = build_security_context(user)
    return payload


def _safe_pipeline_name(value: str) -> bool:
    return bool(DATASET_NAME_RE.fullmatch(value or ""))


def _bronze_latest_date_from_objects(cartridge: str, entity: str, object_names: list[str]) -> str | None:
    if not _safe_pipeline_name(cartridge) or not _safe_pipeline_name(entity):
        return None

    prefix = f"raw/{cartridge}/{entity}/"
    pattern = re.compile(
        rf"^{re.escape(prefix)}load_date=(\d{{4}}-\d{{2}}-\d{{2}})/.+\.parquet$"
    )
    dates = []
    for object_name in object_names:
        match = pattern.match(object_name or "")
        if match:
            dates.append(match.group(1))
    return max(dates) if dates else None


def _minio_client():
    from minio import Minio

    return Minio(
        os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "minio"),
        secret_key=required_secret("MINIO_SECRET_KEY", dev_default="minioadmin"),
        secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
    )


async def _count_bronze_parquet_rows(source: str, latest_date: str, user: dict | None) -> int | None:
    bucket = os.environ.get("MINIO_BUCKET", "lakehouse")
    parquet_glob = f"s3://{bucket}/{source}/load_date={latest_date}/*.parquet"
    sql = (
        "SELECT COUNT(*) AS record_count "
        f"FROM read_parquet('{parquet_glob}', hive_partitioning=true, union_by_name=true)"
    )
    async with httpx.AsyncClient(
        headers=_hdr_for("REFINEMENT"),
        timeout=60,
    ) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload("preview_transform", {"sql": sql, "limit": 1, "sources": [source]}, user),
        )
    if r.status_code != 200:
        return None
    data = r.json()
    rows = data.get("data") or []
    if not rows:
        return None
    row = rows[0]
    value = row.get("record_count")
    if value is None and row:
        value = next(iter(row.values()))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _bronze_physical_snapshot(cartridge: str, entity: str, user: dict | None) -> dict:
    if not _safe_pipeline_name(cartridge) or not _safe_pipeline_name(entity):
        return {}

    source = f"raw/{cartridge}/{entity}"
    ctx = build_security_context(user)
    tenant = str(ctx.get("tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or "").strip()
    list_prefix = f"{source}/"
    if tenant and workspace:
        list_prefix = f"{source}/tenant_id={tenant}/workspace_id={workspace}/"
    if not _explorer_path_allowed(list_prefix, user):
        return {}
    bucket = os.environ.get("MINIO_BUCKET", "lakehouse")
    try:
        client = _minio_client()
        object_names = [
            obj.object_name
            for obj in client.list_objects(bucket, prefix=list_prefix, recursive=True)
        ]
        latest_date = _bronze_latest_date_from_objects(cartridge, entity, object_names)
        if not latest_date:
            return {}
        return {
            "latest_date": latest_date,
            "record_count": await _count_bronze_parquet_rows(source, latest_date, user),
        }
    except Exception:
        return {}


def _parse_iso_datetime(value: str | None):
    if not value:
        return None
    from datetime import datetime as _dt

    try:
        return _dt.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _duration_seconds(started_at, finished_at) -> float | None:
    start = _parse_iso_datetime(str(started_at)) if started_at else None
    finish = _parse_iso_datetime(str(finished_at)) if finished_at else None
    if not start or not finish:
        return None
    return max((finish - start).total_seconds(), 0.0)


def _normalize_airflow_state(state: str | None) -> str:
    normalized = (state or "unknown").lower()
    if normalized in {"queued", "running", "success", "failed"}:
        return normalized
    return "unknown"


_COLUMN_EXISTS_CACHE: dict[tuple[str, str], bool] = {}


async def _table_has_column(table: str, column: str) -> bool:
    key = (table, column)
    if key in _COLUMN_EXISTS_CACHE:
        return _COLUMN_EXISTS_CACHE[key]
    try:
        pool = await _get_db_pool()
        exists = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name=$1
                   AND column_name=$2
            )
            """,
            table,
            column,
        )
        _COLUMN_EXISTS_CACHE[key] = bool(exists)
        return bool(exists)
    except Exception:
        logger.debug("Could not inspect column %s.%s", table, column, exc_info=True)
        _COLUMN_EXISTS_CACHE[key] = False
        return False


async def _pipeline_runs_scope_predicate(user: dict | None, start_index: int = 1) -> tuple[str, list]:
    ctx = build_security_context(user)
    clauses: list[str] = []
    values: list = []
    idx = start_index
    workspace_id = ctx.get("workspace_id")
    tenant_id = ctx.get("tenant_id")
    if workspace_id and await _table_has_column("pipeline_runs", "workspace_id"):
        clauses.append(f"workspace_id=${idx}::uuid")
        values.append(workspace_id)
        idx += 1
    if tenant_id and await _table_has_column("pipeline_runs", "tenant_id"):
        clauses.append(f"tenant_id=${idx}::uuid")
        values.append(tenant_id)
    return (" AND " + " AND ".join(clauses) if clauses else ""), values


async def _record_dag_pipeline_trigger(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    mode: str,
    status: str,
    conf: dict,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    if not dag_run_id:
        return

    pool = await _get_db_pool()
    has_scope = (
        tenant_id
        and workspace_id
        and await _table_has_column("pipeline_runs", "tenant_id")
        and await _table_has_column("pipeline_runs", "workspace_id")
    )
    extra = json.dumps({"raw_conf": conf, "triggered_by": "console"})
    if has_scope:
        await pool.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                mode, status, started_at, extra, tenant_id, workspace_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8::jsonb, $9::uuid, $10::uuid)
            ON CONFLICT (run_id) DO UPDATE SET
                airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                mode = EXCLUDED.mode,
                status = EXCLUDED.status,
                tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                extra = pipeline_runs.extra || EXCLUDED.extra
            """,
            dag_run_id,
            dag_id,
            cartridge,
            entity,
            dag_run_id,
            mode,
            _normalize_airflow_state(status),
            extra,
            tenant_id,
            workspace_id,
        )
    else:
        await pool.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                mode, status, started_at, extra
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                mode = EXCLUDED.mode,
                status = EXCLUDED.status,
                extra = pipeline_runs.extra || EXCLUDED.extra
            """,
            dag_run_id,
            dag_id,
            cartridge,
            entity,
            dag_run_id,
            mode,
            _normalize_airflow_state(status),
            extra,
        )


async def _refresh_dag_run_status(row: dict, user: dict | None = None) -> dict:
    status = _normalize_airflow_state(row.get("status"))
    dag_id = row.get("dag_id")
    dag_run_id = row.get("airflow_dag_run_id") or row.get("run_id")
    if status not in {"queued", "running", "unknown"} or not dag_id or not dag_run_id:
        return row

    result = await mcp_registry.invoke("infra", "airflow_get_run_status", {
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
    }, user=user)
    if result.get("error"):
        return row

    new_status = _normalize_airflow_state(result.get("state"))
    row["status"] = new_status
    row["started_at"] = _parse_iso_datetime(result.get("start_date")) or row.get("started_at")
    row["finished_at"] = _parse_iso_datetime(result.get("end_date")) or row.get("finished_at")
    row["duration_seconds"] = _duration_seconds(row.get("started_at"), row.get("finished_at"))

    try:
        pool = await _get_db_pool()
        await pool.execute(
            """
            UPDATE pipeline_runs
               SET status=$2,
                   started_at=COALESCE($3::timestamptz, started_at),
                   finished_at=COALESCE($4::timestamptz, finished_at),
                   duration_seconds=COALESCE($5::numeric, duration_seconds)
             WHERE run_id=$1
            """,
            row.get("run_id"),
            new_status,
            row.get("started_at"),
            row.get("finished_at"),
            row.get("duration_seconds"),
        )
    except Exception:
        logger.debug("Failed to persist updated run status for %s", row.get("run_id"), exc_info=True)
    return row


# NOTE: 'unsafe-inline' for script-src/style-src is required because the
# static HTML pages use inline scripts and styles. To remove it, all inline
# JS must be moved to external .js files and inline styles to external .css
# files, then CSP can use strict nonces or SHA-256 hashes instead.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
    ),
    # Sprint v1.11 phase 3: every root HTML now ships its JS as an
    # external file (login.js, me.js, monitor.js, decisions.js, rag.js,
    # iam.js, security.js, apps_gallery.js, etc.). Phase 1 and phase 2
    # already cleaned the auth forms and the viewers. With all three
    # phases shipped, the global CSP can drop 'unsafe-inline' from
    # script-src. style-src keeps 'unsafe-inline' for the per-page
    # <style> blocks (separate refactor, not in scope).
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
VIEWER_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
    ),
    # Sprint v1.11 phase 2: viewers no longer carry inline <script> blocks
    # or inline on* handlers (every one was extracted into
    # /static/js/viewers/<name>.js). Drop 'unsafe-inline' from script-src;
    # style-src keeps it because the per-page <style> blocks aren't a
    # practical XSS vector and removing them is a separate refactor.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
# Sprint v1.11 — strict CSP for the auth-form pages. Their HTML no longer
# has inline <script> blocks or inline event handlers, so we can drop
# 'unsafe-inline' from script-src on these paths. style-src keeps
# 'unsafe-inline' because the <style> blocks inside those HTMLs aren't a
# practical XSS vector and removing them is a separate refactor.
STRICT_AUTH_SECURITY_HEADERS = {
    **SECURITY_HEADERS,
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
CONTROL_ROOM_SECURITY_HEADERS = {
    **SECURITY_HEADERS,
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
_STRICT_CSP_PATHS = frozenset({
    "/login",
    "/me",
    "/forgot-password",
    "/reset-password",
    "/activate",
})

APP_EMBED_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
APP_THEME_SHIM = """
<style id="omega-app-theme-shim">
:root,
:root[data-theme="light"] {
  --bg: #f5f7fa;
  --bg2: #ffffff;
  --bg3: #eef2f7;
  --border: #d8dee8;
  --text: #0f172a;
  --text1: #0f172a;
  --text2: #334155;
  --text3: #64748b;
  --green: #117a3d;
  --blue: #0a6ed1;
  --cyan: #0a6ed1;
  --amber: #b06d00;
  --red: #b3261e;
  --purple: #6d5bd0;
  --primary: #0a6ed1;
  --primary-hover: #085caf;
  --primary-soft: rgba(10, 110, 209, 0.10);
  --success-soft: rgba(17, 122, 61, 0.10);
  --warning-soft: rgba(176, 109, 0, 0.12);
  --danger-soft: rgba(179, 38, 30, 0.08);
  --info-soft: rgba(10, 110, 209, 0.10);
  --on-primary: #ffffff;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}
:root[data-theme="dark"] {
  --bg: #0f1822;
  --bg2: #182331;
  --bg3: #1f2c3d;
  --border: rgba(226, 232, 240, 0.14);
  --text: #e6edf6;
  --text1: #e6edf6;
  --text2: #c5cfdc;
  --text3: #8a96a8;
  --green: #4cb27b;
  --blue: #4ea3e0;
  --cyan: #4ea3e0;
  --amber: #d4a042;
  --red: #e0716b;
  --purple: #b8a7f5;
  --primary: #4ea3e0;
  --primary-hover: #74b8e8;
  --primary-soft: rgba(78, 163, 224, 0.16);
  --success-soft: rgba(76, 178, 123, 0.16);
  --warning-soft: rgba(212, 160, 66, 0.18);
  --danger-soft: rgba(224, 113, 107, 0.14);
  --info-soft: rgba(78, 163, 224, 0.16);
  --on-primary: #0f172a;
}
</style>
"""
APP_THEME_SCRIPT = '<script src="/static/js/theme-switch.js" defer></script>'

RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMITS = {
    "/auth/login": (8, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/forgot-password": (5, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/reset-password": (8, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/activate": (8, RATE_LIMIT_WINDOW_SECONDS),
    # Refresh is more frequent than login (access tokens expire in minutes), so
    # the cap is higher; still bounded to deter token-stuffing brute force.
    "/auth/refresh": (60, RATE_LIMIT_WINDOW_SECONDS),
    "/api/copilot": (120, 60),
    "/api/agents": (80, 60),
    "/api/mcp": (80, 60),
    "/studio/import": (10, RATE_LIMIT_WINDOW_SECONDS),
    "/api/explorer": (180, 60),
}
# The limiter backend picks Redis when REDIS_URL is set, otherwise falls back
# to an in-memory sliding window. The in-memory path is per-process and can be
# multiplied by an attacker across replicas; configure REDIS_URL in any
# multi-replica deployment.
from app.services.rate_limiter import get_rate_limiter  # noqa: E402


_TRUSTED_PROXY_IPS: frozenset[str] = frozenset(
    ip.strip()
    for ip in os.environ.get("TRUSTED_PROXY_IPS", "").split(",")
    if ip.strip()
)


def _client_ip(request: Request) -> str:
    real_ip = request.client.host if request.client else "unknown"
    # Only trust X-Forwarded-For when the direct connection comes from a declared proxy.
    if _TRUSTED_PROXY_IPS and real_ip in _TRUSTED_PROXY_IPS:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
    return real_ip


async def _rate_limit(request: Request, action: str, subject: str = "") -> None:
    # v1.44.3.3 (Task A): E2E suites running through the Next.js
    # same-origin proxy all surface to FastAPI as a single
    # source-IP (the docker container's IP), so concurrent test
    # logins share one per-IP bucket and exhaust the 8/300s
    # window long before a real user could. The
    # ``RATE_LIMIT_ENABLED`` env var lets the test harness bypass
    # the limiter completely; in production it stays unset (or
    # explicitly ``true``) and the brute-force protection is
    # untouched.
    #
    # We also bypass when APP_ENV is ``test`` for the same reason
    # — the Python suite calls these endpoints repeatedly during
    # the auth contract tests.
    if _rate_limit_disabled():
        return

    limit, window = RATE_LIMITS[action]
    ip = _client_ip(request)
    subject_key = subject.lower().strip() or "-"
    # Two checks both must pass:
    #   1) per (ip, subject) — keeps a noisy single user from drowning others
    #   2) per ip — prevents subject-rotation bypass (e.g. an attacker
    #      cycling many invitation tokens from one IP gets a fresh
    #      (ip, subject) bucket for each token; the per-IP key is the
    #      one that actually caps the brute-force budget).
    # All registered actions are auth-sensitive (login / forgot / reset /
    # activate) — fail closed so a Redis outage cannot silently disable
    # brute-force protection.
    limiter = get_rate_limiter()
    keys = [f"{action}:{ip}:{subject_key}", f"{action}:{ip}:-"]
    for key in keys:
        if not await limiter.check(key, limit, window, sensitive=True):
            raise HTTPException(status_code=429, detail="too many requests")


async def _rate_limit_api_surface(request: Request, path: str, user: dict | None) -> None:
    if _rate_limit_disabled():
        return
    matched = None
    for prefix in ("/api/copilot", "/api/agents", "/api/mcp", "/studio/import", "/api/explorer"):
        if path == prefix or path.startswith(prefix + "/"):
            matched = prefix
            break
    if not matched:
        return
    limit, window = RATE_LIMITS[matched]
    ip = _client_ip(request)
    user_key = str((user or {}).get("id") or (user or {}).get("email") or "-")
    limiter = get_rate_limiter()
    for key in (f"{matched}:{ip}:{user_key}", f"{matched}:{ip}:-"):
        if not await limiter.check(key, limit, window, sensitive=True):
            raise HTTPException(status_code=429, detail="too many requests")


def _rate_limit_disabled() -> bool:
    """Return True when rate limiting should bypass.

    The bypass fires in two scenarios — both are EXPLICITLY
    test-harness affordances, never production behaviour:

      1. ``RATE_LIMIT_ENABLED=false`` (any case) — an explicit
         opt-out for E2E suites that hammer /auth/login. Default
         unset → enabled.
      2. ``APP_ENV`` ∈ {``test``, ``testing``} — automatic for
         pytest harnesses that don't bother setting
         RATE_LIMIT_ENABLED.

    Production deployments default ``APP_ENV`` to ``production``
    (see console/app/security.py) and leave RATE_LIMIT_ENABLED
    unset, so the limiter stays on.
    """
    enabled_env = os.environ.get("RATE_LIMIT_ENABLED")
    if enabled_env is not None and enabled_env.strip().lower() in {"false", "0", "no", "off"}:
        return True
    app_env = os.environ.get("APP_ENV", "production").strip().lower()
    return app_env in {"test", "testing"}


def _is_viewer_path(path: str) -> bool:
    return path == "/viewer" or path.startswith("/viewer/")


def _is_control_room_path(path: str) -> bool:
    return path == "/control-room" or path.startswith("/control-room/")


def _apply_security_headers(response: Response, path: str = "") -> Response:
    # Sprint v1.11: prefer the strict-auth headers for the five auth-form
    # pages; viewers keep their iframe-friendly headers; everything else
    # gets the default SECURITY_HEADERS. The dispatch is path-based — the
    # auth POST endpoints (/auth/login etc.) live under /auth/ and fall
    # through to the default set, which is fine because their responses
    # are JSON, not HTML.
    if _is_control_room_path(path):
        headers = CONTROL_ROOM_SECURITY_HEADERS
    elif path in _STRICT_CSP_PATHS:
        headers = STRICT_AUTH_SECURITY_HEADERS
    elif _is_viewer_path(path):
        headers = VIEWER_SECURITY_HEADERS
    else:
        headers = SECURITY_HEADERS
    if _is_viewer_path(path):
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
    for name, value in headers.items():
        response.headers.setdefault(name, value)
    return response


def _inject_published_app_theme(html: str) -> str:
    patched = html
    if "omega-app-theme-shim" not in patched:
        lower = patched.lower()
        idx = lower.rfind("</head>")
        if idx >= 0:
            patched = patched[:idx] + APP_THEME_SHIM + patched[idx:]
        else:
            patched = APP_THEME_SHIM + patched
    if "/static/js/theme-switch.js" not in patched:
        lower = patched.lower()
        idx = lower.rfind("</body>")
        if idx >= 0:
            patched = patched[:idx] + APP_THEME_SCRIPT + patched[idx:]
        else:
            patched += APP_THEME_SCRIPT
    return patched


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# ── Auth middleware ────────────────────────────────────────────────────────────

_AUTH_PUBLIC_EXACT = {
    # ── Login / session ──
    "/login", "/auth/login", "/api/auth/login", "/auth/logout",
    "/auth/me", "/auth/me-jwt", "/auth/me-current", "/auth/refresh",
    # ── Account activation ──
    "/activate", "/auth/activate", "/auth/activate/info",
    # ── Password recovery ──
    "/forgot-password", "/auth/forgot-password",
    "/reset-password", "/auth/reset-password", "/auth/reset/info",
    # ── Static / browser ──
    "/favicon.ico",
    # ── Health / monitoring ──
    # Sprint v1.23.1 hotfix: /healthz must be reachable WITHOUT auth so
    # the v1.21 compose probe + the v1.23 smoke script can hit it from
    # inside the container / from `make smoke` on the host. Without
    # this entry, auth_middleware redirects /healthz to /login (307)
    # and the probe never sees a 200 — leaving the service stuck on
    # `(unhealthy)` even when it's fine. Workspace and vault already
    # handle /healthz via their own public-path sets; console was the
    # outlier.
    "/healthz", "/readyz",
}
_AUTH_PUBLIC_PREFIX = ("/static/", "/vpn-config/")
_AUTH_API_LIKE_PREFIX = ("/api/", "/mcp/", "/internal/", "/datasets", "/jobs", "/tokens",
                         "/studio/", "/studio_ops/", "/monitoring/", "/auth/")
_AUTH_INTERNAL_SERVICE_PREFIX = ("/monitoring/mcp/", "/studio_ops/mcp/")

# Routes a user is allowed to hit while in must_change_password=true state.
_AUTH_FORCED_CHANGE_ALLOW_EXACT = {
    "/me", "/api/me", "/api/me/change-password", "/auth/logout", "/auth/me",
}

_RBAC_DEPENDENCY_PREFIXES = (
    "/jobs",
    "/tokens/summary",
    "/assistant/chat",
    "/datasets",
    "/api/data",
    "/api/jobs",
    "/api/me",
    "/api/users",
    "/api/decisions",
    "/api/datasets",
    "/api/apps",
    "/api/control-room",
    "/api/admin/users",
    "/api/pipeline",
    "/api/pipeline_runs",
    "/api/dag_templates",
    "/api/schema",
    "/api/sources",
    "/api/vault",
    "/api/rag",
    "/api/catalog",
    "/api/semantic",
    "/api/studio",                      # v1.44.3.3 Task B (stubs)
    "/studio/cartridges",
    "/studio/import",
    "/studio/chat",
)


def _is_api_like(path: str, accept: str) -> bool:
    if any(path.startswith(p) for p in _AUTH_API_LIKE_PREFIX):
        return True
    return "application/json" in (accept or "")


def _uses_rbac_dependency(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _RBAC_DEPENDENCY_PREFIXES)


def _is_agent_runner_request(request: Request) -> bool:
    path = request.url.path
    if not (path.startswith("/api/agents/") and path.endswith("/invoke/scheduled")):
        return False
    expected = os.environ.get("AGENT_RUNNER_TOKEN", "")
    supplied = request.headers.get("X-Agent-Runner-Token", "")
    return bool(expected and supplied == expected)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Internal routes (server-to-server) bypass session auth.
    # Their own router-level dependency (verify_internal_api_key) handles auth via header.
    if path.startswith("/internal/"):
        return await call_next(request)

    if path.startswith(_AUTH_INTERNAL_SERVICE_PREFIX) and _is_internal_request(request):
        request.state.user = _internal_service_user()
        return await call_next(request)

    if _is_replicon_vault_reveal_request(request):
        request.state.user = _internal_service_user()
        return await call_next(request)

    # Airflow scheduled agent runs authenticate with X-Agent-Runner-Token;
    # the route re-checks the same token before executing the agent.
    if _is_agent_runner_request(request):
        return await call_next(request)

    is_public = path in _AUTH_PUBLIC_EXACT or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIX)

    requested_workspace_id = (request.headers.get("x-workspace-id") or "").strip() or None
    token = request.cookies.get(_auth.COOKIE_NAME)
    user  = await _auth.get_session_user(token) if token else None
    if user and (requested_workspace_id or not user.get("active_workspace_id")):
        try:
            workspaces = await _workspace_memberships(user["id"])
            if workspaces:
                active_workspace = workspaces[0]
                if requested_workspace_id:
                    active_workspace = next((w for w in workspaces if w["workspace_id"] == requested_workspace_id), None)
                    if not active_workspace:
                        return _apply_security_headers(JSONResponse({"detail": "workspace access forbidden"}, status_code=403), path)
                user = dict(user)
                user.update({
                    "workspace_role": active_workspace["workspace_role"],
                    "active_workspace_id": active_workspace["workspace_id"],
                    "active_tenant_id": active_workspace["tenant_id"],
                    "workspaces": workspaces,
                    "allowed_cartridges": await _workspace_cartridges(active_workspace["workspace_id"], user_id=user["id"]),
                })
        except Exception:
            logger.exception("Session workspace enrichment failed")
            if not is_public and _uses_rbac_dependency(path):
                return _apply_security_headers(
                    JSONResponse({"detail": "workspace context unavailable"}, status_code=403),
                    path,
                )

    # Fall back to JWT bearer so require_permission() routes get request.state.user set.
    if not user:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                # Sprint v1.10: async variant runs the Redis blacklist
                # check; legacy decode kept for unit tests.
                claims = await verify_access_token_async(auth_header[7:])
                jwt_user = await _auth.get_user_by_id(int(claims["sub"]))
                if jwt_user and jwt_user.get("is_active"):
                    workspaces = await _workspace_memberships(jwt_user["id"])
                    if workspaces:
                        active_workspace = workspaces[0]
                        if requested_workspace_id:
                            active_workspace = next((w for w in workspaces if w["workspace_id"] == requested_workspace_id), None)
                            if not active_workspace:
                                return _apply_security_headers(JSONResponse({"detail": "workspace access forbidden"}, status_code=403), path)
                        jwt_user = dict(jwt_user)
                        jwt_user.update({
                            "workspace_role": active_workspace["workspace_role"],
                            "active_workspace_id": active_workspace["workspace_id"],
                            "active_tenant_id": active_workspace["tenant_id"],
                            "workspaces": workspaces,
                            "allowed_cartridges": await _workspace_cartridges(active_workspace["workspace_id"], user_id=jwt_user["id"]),
                        })
                    user = jwt_user
            except Exception:
                logger.debug("Bearer JWT auth fallback failed", exc_info=True)
                if not is_public and _uses_rbac_dependency(path):
                    return _apply_security_headers(
                        JSONResponse({"detail": "authentication required"}, status_code=401),
                        path,
                    )

    request.state.user = user
    try:
        await _rate_limit_api_surface(request, path, user)
    except HTTPException as exc:
        return _apply_security_headers(JSONResponse({"detail": exc.detail}, status_code=exc.status_code), path)

    if not user and not is_public and _uses_rbac_dependency(path):
        return await call_next(request)

    if not user and not is_public:
        if _is_api_like(path, request.headers.get("accept", "")):
            return _apply_security_headers(JSONResponse({"detail": "authentication required"}, status_code=401), path)
        return _apply_security_headers(RedirectResponse(url=f"/login?next={path}"), path)

    # Forced password change: confine the session to the change-password flow.
    if user and user.get("must_change_password") and not is_public:
        if path not in _AUTH_FORCED_CHANGE_ALLOW_EXACT:
            if _is_api_like(path, request.headers.get("accept", "")):
                return _apply_security_headers(JSONResponse(
                    {"detail": "password change required", "must_change_password": True},
                    status_code=403,
                ), path)
            return _apply_security_headers(RedirectResponse(url="/me"), path)

    return await call_next(request)


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "authentication required")
    return u


def require_admin(request: Request) -> dict:
    u = require_user(request)
    if u.get("role") not in {"admin", "owner", "super_admin"}:
        raise HTTPException(403, "admin role required")
    return u


def _access_token_for_user(user: dict) -> str:
    return create_access_token({
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
    })


def _set_refresh_cookie(resp: JSONResponse, token: str, expires) -> None:
    resp.set_cookie(
        _auth.REFRESH_COOKIE_NAME, token,
        httponly=True, secure=_auth.cookie_secure(), samesite="lax",
        expires=expires.replace(microsecond=0),
        path="/",
    )


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.get("/login")
async def login_page(request: Request):
    # Seed the CSRF cookie so the page's POST /auth/login fetch can echo
    # it back without an extra round-trip. The cookie is re-issued on
    # every GET /login (cheap, and avoids a stale-token edge case when
    # the user keeps the tab open across logout/login).
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "login/index.html")


async def _login_response(request: Request, body: dict):
    email = (body.get("email") or "").strip()
    pw    = body.get("password") or ""
    await _rate_limit(request, "/auth/login", email)
    if not email or not pw:
        raise HTTPException(400, "email and password are required")
    ip = request.client.host if request.client else None
    user = await _auth.authenticate(email, pw, ip=ip)
    if not user:
        raise HTTPException(401, "invalid credentials")
    token, expires = await _auth.create_session(user["id"], ip=ip)
    access_token = _access_token_for_user(user)
    refresh_token, refresh_expires = await _auth.create_refresh_token(user["id"])
    resp = JSONResponse({"user": user, "access_token": access_token, "token_type": "bearer"})
    resp.set_cookie(
        _auth.COOKIE_NAME, token,
        httponly=True, samesite="lax",
        secure=_auth.cookie_secure(),
        expires=expires.replace(microsecond=0),
        path="/",
    )
    _set_refresh_cookie(resp, refresh_token, refresh_expires)
    # Keep the CSRF token stable across the login transition. API clients
    # and the Next.js proxy seed the token on GET /login, submit it to
    # /auth/login, then immediately use the same in-memory token for the
    # first authenticated mutation while the cookie jar is catching up.
    # The double-submit check remains active because the header must still
    # match the csrf_token cookie.
    set_csrf_cookie(resp, request.cookies.get(CSRF_COOKIE_NAME))
    return resp


@app.post("/auth/login", dependencies=[Depends(require_csrf)])
async def auth_login(request: Request, body: dict):
    return await _login_response(request, body)


@app.post("/api/auth/login", dependencies=[Depends(require_csrf)])
async def api_auth_login(request: Request, body: dict):
    # Legacy compatibility alias for clients that still post to
    # /api/auth/login. Delegate through the real handler so CSRF,
    # rate-limit, and session behavior stay identical to /auth/login.
    return await auth_login(request, body)


@app.post("/auth/refresh", dependencies=[Depends(require_csrf)])
async def auth_refresh(request: Request):
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    # Hash a prefix of the token into the subject so per-token buckets isolate
    # spamming attempts without writing the secret material to Redis keys.
    subject = (refresh_token or "")[:16]
    await _rate_limit(request, "/auth/refresh", subject)
    rotate_refresh_token = getattr(_auth, "rotate_refresh_token", None)
    if callable(rotate_refresh_token):
        rotated = await rotate_refresh_token(refresh_token)
        if not rotated:
            resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
            resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
            return resp
        user, new_refresh_token, refresh_expires = rotated
    else:
        # Compatibility for unit-test doubles that predate atomic rotation.
        user = await _auth.get_refresh_token_user(refresh_token)
        if not user:
            resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
            resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
            return resp
        await _auth.revoke_refresh_token(refresh_token)
        new_refresh_token, refresh_expires = await _auth.create_refresh_token(user["id"])

    if not user:
        resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
        resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
        return resp

    access_token = _access_token_for_user(user)
    resp = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    _set_refresh_cookie(resp, new_refresh_token, refresh_expires)
    return resp


@app.post("/auth/logout", dependencies=[Depends(require_csrf)])
async def auth_logout(request: Request):
    token = request.cookies.get(_auth.COOKIE_NAME)
    if token:
        await _auth.destroy_session(token)
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    if refresh_token:
        await _auth.revoke_refresh_token(refresh_token)

    # Sprint v1.10 — blacklist the bearer access token's jti so a stolen
    # JWT can't keep authenticating up to its exp. Silent if the caller
    # is cookie-only (most of our UI) or the token is already invalid.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(bearer)
            jti = claims.get("jti")
            exp = claims.get("exp")
            if jti and exp is not None:
                from app.services.jwt_blacklist import get_blacklist
                await get_blacklist().revoke(jti, int(exp))
        except Exception:
            # JWT already expired / malformed / signature mismatch —
            # nothing to revoke, nothing to do.
            pass

    resp = JSONResponse({"logged_out": True})
    resp.delete_cookie(_auth.COOKIE_NAME, path="/")
    resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
    clear_csrf_cookie(resp)
    return resp


@app.get("/auth/me")
async def auth_me(request: Request):
    return {"user": _user_payload(current_user(request))}


@app.get("/auth/me-jwt")
async def auth_me_jwt(authorization: str | None = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    try:
        # Sprint v1.10: blacklist-aware verification.
        claims = await verify_access_token_async(token)
    except JWTAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"claims": {
        "sub": claims["sub"],
        "email": claims["email"],
        "role": claims["role"],
        "iat": claims["iat"],
        "exp": claims["exp"],
        "jti": claims["jti"],
    }}


@app.get("/auth/me-current")
async def auth_me_current(user: dict = Depends(get_current_user_dependency)):
    return {"user": _user_payload(user)}


# ── Activation ────────────────────────────────────────────────────────────────

APP_BASE_URL = _public_url(
    "APP_BASE_URL",
    fallback_env="CONSOLE_URL",
    development_default="http://localhost:8000",
)
INVITE_TTL_HOURS = int(os.environ.get("INVITE_TOKEN_TTL_HOURS", "72"))
RESET_TTL_HOURS  = int(os.environ.get("RESET_TOKEN_TTL_HOURS",  "1"))
VPN_TTL_HOURS    = int(os.environ.get("VPN_TOKEN_TTL_HOURS",   "72"))


def _normalize_email_or_400(value: object | None, *, required_message: str = "email is required") -> str:
    email = str(value or "").strip().lower()
    if not email:
        raise HTTPException(400, required_message)
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise HTTPException(400, "invalid email")
    return email


def _password_min_length() -> int:
    return int(getattr(_auth, "MIN_PASSWORD_LENGTH", 12))


def _validate_password_or_400(password: object | None, *, field: str = "password") -> str:
    password = str(password or "")
    if not password:
        raise HTTPException(400, f"{field} is required")
    if len(password) < _password_min_length():
        raise HTTPException(400, f"el password debe tener al menos {_password_min_length()} caracteres")
    return password


def _activation_link(token: str) -> str:
    return f"{APP_BASE_URL}/activate?token={token}"


def _reset_link(token: str) -> str:
    return f"{APP_BASE_URL}/reset-password?token={token}"


def _vpn_link(token: str) -> str:
    return f"{APP_BASE_URL}/vpn-config/{token}"


def _set_session_cookie(resp: JSONResponse, token: str, expires) -> None:
    resp.set_cookie(
        _auth.COOKIE_NAME, token, httponly=True, samesite="lax",
        secure=_auth.cookie_secure(), expires=expires.replace(microsecond=0), path="/",
    )


@app.get("/activate")
async def viewer_activate():
    response = FileResponse(STATIC / "activate.html")
    set_csrf_cookie(response)
    return response


@app.post("/auth/activate", dependencies=[Depends(require_csrf)])
async def auth_activate(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw    = body.get("new_password") or ""
    await _rate_limit(request, "/auth/activate", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    _validate_password_or_400(pw, field="new_password")
    info = await _tokens.consume_lookup(token, "invite")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.activate_user(info["user_id"], pw)
    if not user:
        raise HTTPException(400, "el password debe tener al menos 12 caracteres")
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"activated": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    return resp


@app.get("/auth/activate/info")
async def auth_activate_info(token: str = ""):
    """Public probe so the activate page can show the user's email/name."""
    info = await _tokens.lookup(token, "invite")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"], "name": info["name"]}


# ── Forgot / reset password ──────────────────────────────────────────────────

@app.get("/forgot-password")
async def viewer_forgot():
    # Seed CSRF cookie so the form's POST /auth/forgot-password fetch can
    # echo it back without a prior visit to /login.
    response = FileResponse(STATIC / "forgot_password.html")
    set_csrf_cookie(response)
    return response


@app.post("/auth/forgot-password", dependencies=[Depends(require_csrf)])
async def auth_forgot(request: Request, body: dict):
    """Generic OK regardless of whether the email exists (avoids enum oracle)."""
    email = (body.get("email") or "").strip().lower()
    await _rate_limit(request, "/auth/forgot-password", email)
    if email:
        u = await _auth.get_user_by_email(email)
        if u and u.get("is_active"):
            tok, _ = await _tokens.create(u["id"], "reset")
            subject, html = _email.render_password_reset(u.get("name"), _reset_link(tok), RESET_TTL_HOURS)
            await _email.send_email(u["email"], subject, html)
    return {"sent": True}


@app.get("/reset-password")
async def viewer_reset():
    response = FileResponse(STATIC / "reset_password.html")
    set_csrf_cookie(response)
    return response


@app.get("/auth/reset/info")
async def auth_reset_info(token: str = ""):
    info = await _tokens.lookup(token, "reset")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"]}


@app.post("/auth/reset-password", dependencies=[Depends(require_csrf)])
async def auth_reset(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw    = body.get("new_password") or ""
    await _rate_limit(request, "/auth/reset-password", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    _validate_password_or_400(pw, field="new_password")
    info = await _tokens.consume_lookup(token, "reset")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.reset_password_to(info["user_id"], pw)
    if not user:
        raise HTTPException(400, f"el password debe tener al menos {_password_min_length()} caracteres")
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"reset": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    # Rotate CSRF after the password reset so any leaked pre-reset token
    # cannot replay.
    set_csrf_cookie(resp)
    return resp


def _console_version() -> str:
    """Deprecated shim — use app.version.app_version(). Kept so existing
    call sites stay valid; delegates to the single source of truth."""
    from app.version import app_version
    return app_version()


@app.get("/healthz")
async def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    No auth, no DB call — answers as long as the FastAPI event loop is
    running. Used by infra/docker-compose.yml so dependent services
    wait on service_healthy instead of service_started, avoiding the
    boot race where console answers before its lifespan has wired the
    DB pool.

    Carries ``version`` + ``app_env`` (no secrets) so an operator can
    confirm WHAT is deployed without authenticating — useful for the
    beta/demo runbook's health checks."""
    return {
        "ok": True,
        "service": "console",
        "version": _console_version(),
        "app_env": _app_env(),
    }


async def _dependency_health(name: str, url: str, server: str | None = None) -> dict:
    if not url:
        return {"status": "down", "error": "missing_url"}
    try:
        headers = _hdr_for(server) if server else {}
        async with httpx.AsyncClient(headers=headers, timeout=2.5) as c:
            r = await c.get(url)
        status = "up" if r.status_code < 500 else "down"
        return {"status": status, "code": r.status_code}
    except Exception as exc:
        logger.warning("readiness probe failed for %s", name, exc_info=True)
        return {"status": "down", "error": type(exc).__name__}


@app.get("/readyz")
async def readyz():
    """Dependency-aware readiness probe.

    `/healthz` only proves the process can answer. `/readyz` is stricter:
    it verifies Postgres and core sibling services so deploy/proxy layers can
    keep traffic away from a half-started console.
    """
    checks: dict[str, dict] = {}
    try:
        pool = await _get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = {"status": "up"}
    except Exception as exc:
        logger.warning("readiness probe failed for postgres", exc_info=True)
        checks["postgres"] = {"status": "down", "error": type(exc).__name__}

    deps = {
        "refinement": (f"{REFINEMENT_URL.rstrip('/')}/healthz", "REFINEMENT"),
        "mcp-infra": (f"{os.environ.get('MCP_INFRA_URL', 'http://mcp-infra:8010').rstrip('/')}/healthz", "MCP_INFRA"),
        "vault": (f"{_vault_url()}/healthz", "VAULT"),
    }
    for name, (url, server) in deps.items():
        checks[name] = await _dependency_health(name, url, server)

    ok = all(check.get("status") == "up" for check in checks.values())
    return JSONResponse(
        {"ok": ok, "service": "console", "checks": checks},
        status_code=200 if ok else 503,
    )


@app.get("/api/config")
async def api_config(request: Request):
    """Runtime config (URLs only, no secrets)."""
    return {
        "workspace_url": _public_url(
            "WORKSPACE_URL",
            fallback_env="WORKSPACE_PUBLIC_URL",
            development_default="http://localhost:8001",
        ),
        "console_url": _public_url("CONSOLE_URL", development_default="http://localhost:8000"),
        "airflow_url": _public_url("AIRFLOW_PUBLIC_URL", development_default="http://localhost:8082"),
        "superset_url": _public_url("SUPERSET_PUBLIC_URL", development_default="http://localhost:8088"),
        "s3_bucket":     os.environ.get("S3_BUCKET_NAME") or os.environ.get("MINIO_BUCKET", "lakehouse"),
    }


@app.get("/api/system/info")
async def system_info(user: dict = Depends(require_authenticated)):
    version = _console_version()
    # v1.43.2 (Frontend R1 hardening): expose ``dev_mode`` so the UI
    # can hide CTAs that gate on dev-only mcp-infra tools (Studio
    # Deploy DAG, etc). Pre-v1.43.2 the console rendered those
    # buttons unconditionally; clicking them in production now surfaces
    # a PermissionError from airflow_create_dag — which is correct but
    # confusing. The button is hidden by checking this flag.
    app_env = os.environ.get("APP_ENV", "production").lower()
    rce_tools_enabled = os.environ.get("ALLOW_RCE_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
    dev_mode = app_env in {"development", "dev", "local", "test"}
    return {
        "version": version,
        "env": os.environ.get("MODE", "local"),
        "service": "console",
        "app_env": app_env,
        "dev_mode": dev_mode,
        "rce_tools_enabled": rce_tools_enabled,
        "dag_deploy_enabled": dev_mode and rce_tools_enabled,
    }


@app.get("/me")
async def viewer_me(request: Request):
    require_user(request)
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "me/index.html")


@app.get("/api/me")
async def api_me(user: dict = Depends(require_authenticated)):
    return _user_payload(user)


@app.get("/api/me/access")
async def api_me_access(user: dict = Depends(require_authenticated)):
    """Phase-0 SaaS-controls: a single endpoint that returns the user's
    *effective* security profile so the front-end can render "Mis accesos"
    without the user (or operator) inferring permissions from role names.

    The shape is intentionally narrow: the front-end uses these fields to
    decide what to render and what to disable. The backend is still the
    source of truth — every action endpoint enforces its own permission.
    """
    from app.services import permissions as _perms

    effective = sorted(_perms.get_effective_permissions(user))
    role_canonical = _perms.canonical_role(user.get("role"))
    workspace_role_resolved = _perms.workspace_role(user) or None

    cartridges_allowed: list[str] = []
    cartridges_denied: list[dict[str, str]] = []
    try:
        p = await cartridge_service.pool()
        async with p.acquire() as conn:  # noqa: SIM117 — nested try/except is intentional
            # Cartridges visible to this caller in their workspace.
            try:
                allowed_rows = await conn.fetch(
                    """
                    SELECT ci.cartridge_id, ci.status,
                           COALESCE(p.name, ci.cartridge_id) AS product_name
                      FROM cartridge_installations ci
                      LEFT JOIN marketplace_products p ON p.cartridge_id = ci.cartridge_id
                     WHERE ci.tenant_id = $1
                       AND ci.workspace_id = $2
                       AND ci.status IN ('ready', 'active')
                       AND NOT EXISTS (
                         SELECT 1
                           FROM user_cartridge_overrides uco
                          WHERE uco.tenant_id = ci.tenant_id
                            AND uco.workspace_id = ci.workspace_id
                            AND uco.cartridge_id = ci.cartridge_id
                            AND uco.user_id = $3
                            AND uco.mode = 'deny'
                       )
                    ORDER BY product_name
                    """,
                    user.get("tenant_id") or user.get("active_tenant_id"),
                    user.get("workspace_id") or user.get("active_workspace_id"),
                    user.get("id"),
                )
                cartridges_allowed = [
                    {"cartridge_id": r["cartridge_id"],
                     "product_name": r["product_name"],
                     "status": r["status"]}
                    for r in allowed_rows
                ]
            except Exception:  # noqa: BLE001
                # Marketplace migrations may not be applied in every env.
                cartridges_allowed = []
            try:
                denied_rows = await conn.fetch(
                    """
                    SELECT uco.cartridge_id, uco.mode,
                           ci.status AS installation_status,
                           COALESCE(p.name, uco.cartridge_id) AS product_name
                      FROM user_cartridge_overrides uco
                      LEFT JOIN cartridge_installations ci
                        ON ci.tenant_id = uco.tenant_id
                       AND ci.workspace_id = uco.workspace_id
                       AND ci.cartridge_id = uco.cartridge_id
                      LEFT JOIN marketplace_products p
                        ON p.cartridge_id = uco.cartridge_id
                     WHERE uco.tenant_id = $1
                       AND uco.workspace_id = $2
                       AND uco.user_id = $3
                       AND uco.mode = 'deny'
                    ORDER BY product_name
                    """,
                    user.get("tenant_id") or user.get("active_tenant_id"),
                    user.get("workspace_id") or user.get("active_workspace_id"),
                    user.get("id"),
                )
                cartridges_denied = [
                    {"cartridge_id": r["cartridge_id"],
                     "product_name": r["product_name"],
                     "reason": "user_deny",
                     "installation_status": r["installation_status"]}
                    for r in denied_rows
                ]
            except Exception:  # noqa: BLE001
                cartridges_denied = []
    except Exception:  # noqa: BLE001
        # If the marketplace pool is unavailable we still return the
        # identity-level info so the page can render in restricted mode.
        # Log it: silent fallback is intentional for ops resilience but
        # we must not mask repeated failures from the team.
        logger.warning("api_me_access: marketplace pool unavailable", exc_info=True)

    return {
        "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": user.get("name") or user.get("email"),
        },
        "role": {
            "global": role_canonical,
            "is_platform_admin": role_canonical in {"owner", "super_admin", "admin"},
        },
        "workspace": {
            "tenant_id": user.get("tenant_id") or user.get("active_tenant_id"),
            "workspace_id": user.get("workspace_id") or user.get("active_workspace_id"),
            "workspace_role": workspace_role_resolved,
        },
        "permissions": effective,
        "cartridges": {
            "allowed": cartridges_allowed,
            "denied": cartridges_denied,
        },
        # The front-end uses these flags to decide what to render. They are
        # *display hints only*; every action endpoint enforces its own gate.
        # IMPORTANT: each flag must replicate the FULL guard chain of the
        # target page. /iam, /admin/users, /settings, /operations require
        # both the permission AND `require_admin` (global admin role).
        # If we only checked the permission, a security_admin user (who
        # has iam.users.read but is not a global admin) would see the
        # link and get a 403 on click. The backend still rejects, but the
        # UI must not lie.
        "ui_capabilities": {
            "can_view_iam":            "iam.users.read" in effective and role_canonical in {"owner", "super_admin", "admin"},
            "can_admin_marketplace":   "marketplace.admin" in effective,
            # `workspace_role()` already normalizes the legacy database
            # workspace_role values (admin/owner/super_admin/security_admin)
            # to "workspace_admin" before returning. Comparing only to
            # "workspace_admin" keeps the intent explicit and prevents a
            # future copy-paste from re-introducing a global-admin check on
            # a workspace-scoped flag.
            "can_admin_workspace":     workspace_role_resolved == "workspace_admin",
            "can_view_audit":          "security.audit.read" in effective,
            "can_view_sessions":       "security.sessions.read" in effective,
        },
    }


@app.post("/api/me/change-password", dependencies=[Depends(require_csrf)])
async def api_me_change_password(body: dict, user: dict = Depends(require_authenticated)):
    current = body.get("current_password") or ""
    new     = body.get("new_password") or ""
    if not current or not new:
        raise HTTPException(400, "current_password and new_password are required")
    ok, err = await _auth.change_own_password(user["id"], current, new)
    if not ok:
        raise HTTPException(400, err or "password change failed")
    resp = JSONResponse({"changed": True})
    # Rotate CSRF after a successful self-service password change.
    set_csrf_cookie(resp)
    return resp


# ── Jobs ──────────────────────────────────────────────────────────────────────

@app.get("/jobs", dependencies=[Depends(require_authenticated)])
async def list_jobs(limit: int = 20, user: dict = Depends(require_authenticated)):
    return {"jobs": await _call_with_optional_user(job_service.list_recent, limit, user=user)}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_authenticated)])
async def get_job(job_id: str, user: dict = Depends(require_authenticated)):
    return await job_service.get_scoped(job_id, user=user)


# ── Token usage ───────────────────────────────────────────────────────────────

@app.get("/tokens/summary", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def tokens_summary():
    return await token_store.summary()


# ── Assistant ─────────────────────────────────────────────────────────────────

@app.post("/assistant/chat", dependencies=[Depends(require_csrf)])
async def chat(body: dict, user: dict = Depends(require_authenticated)):
    return await _call_with_optional_user(
        assistant.chat,
        body.get("message", ""),
        body.get("history", []),
        user=user,
    )


# ── Datasets proxy → refinement ───────────────────────────────────────────────

@app.get("/datasets", dependencies=[Depends(require_authenticated)])
async def list_datasets(user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("list_datasets", {}, user=user)

@app.get("/datasets/{name}/schema", dependencies=[Depends(require_authenticated)])
async def dataset_schema(name: str, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("get_schema", {"name": name}, user=user)

@app.get("/api/datasets", dependencies=[Depends(require_authenticated)])
async def api_list_datasets_alias(user: dict = Depends(require_authenticated)):
    return await list_datasets(user)

@app.get("/api/datasets/{name}/schema", dependencies=[Depends(require_authenticated)])
async def api_dataset_schema_alias(name: str, user: dict = Depends(require_authenticated)):
    return await dataset_schema(name, user)

@app.get("/datasets/{name}/data", dependencies=[Depends(require_authenticated)])
async def dataset_data(name: str, request: Request, limit: int = 100):
    # Forward user context so refinement can apply RLS. Without it the GOLD
    # tables fall through to the empty-tenant filter (or the revenue_manager
    # 'N/D' fallback) and any authenticated user could read cross-tenant rows.
    user = getattr(request.state, "user", None) or {}
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "query_dataset",
                {"name": name, "limit": limit, "user_context": _rls_user_context(user)},
                user,
            ),
        )
        return r.json()

@app.post("/datasets/{name}/refresh", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))])
async def refresh_dataset(name: str, user: dict = Depends(require_permission("datasets.write"))):
    return await _refinement_invoke("materialize", {"name": name}, timeout=120, user=user)


# ── Viewer data APIs ──────────────────────────────────────────────────────────

@app.get("/api/jobs", dependencies=[Depends(require_authenticated)])
async def api_jobs(limit: int = 50, user: dict = Depends(require_authenticated)):
    return {"jobs": await _call_with_optional_user(job_service.list_recent, limit, user=user)}

@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_authenticated)])
async def api_job(job_id: str, user: dict = Depends(require_authenticated)):
    return await job_service.get_scoped(job_id, user=user)

@app.get("/api/jobs/{job_id}/logs", dependencies=[Depends(require_authenticated)])
async def api_job_logs(job_id: str, limit: int = 200, user: dict = Depends(require_authenticated)):
    import json as _json
    scoped = await job_service.get_scoped(job_id, user=user)
    if scoped.get("error"):
        raise HTTPException(404, "job not found")
    pool = await _get_db_pool()
    rows = await pool.fetch(
        "SELECT entity, level, message, detail, ts FROM run_logs "
        "WHERE run_id=$1 AND cartridge='replicon' ORDER BY ts ASC LIMIT $2",
        job_id, limit
    )
    result = []
    for row in rows:
        detail = row["detail"]
        if isinstance(detail, str):
            try:
                detail = _json.loads(detail)
            except (json.JSONDecodeError, ValueError):
                pass
        result.append({
            "ts": row["ts"].isoformat(),
            "entity": row["entity"],
            "level": row["level"],
            "message": row["message"],
            "detail": detail,
        })
    return {"logs": result}

@app.get("/api/tools/manifest", dependencies=[Depends(require_authenticated)])
async def api_tools_manifest():
    """Sprint v1.41.0 (tornillo copilot): unified tool catalog with risk_level
    + requires_approval, sourced from every registered MCP server. The copilot
    router (v1.42+) consumes this to decide auto-execution vs approval prompts."""
    from app.services.tool_manifest import build_manifest
    return await build_manifest()


# Sprint v1.41.0 — cartridge management endpoints live in
# console/app/routers/cartridges.py (registered with include_router below).


@app.get("/api/schema", dependencies=[Depends(require_authenticated)])
async def api_schema(source: str, user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("get_source_partitions", {"source": source}, user))
        partitions = r.json()
        r2 = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                          json=_mcp_payload("preview_source", {"source": source, "limit": 5}, user))
        preview = r2.json()
    return {"partitions": partitions, "preview": preview}

@app.get("/api/sources", dependencies=[Depends(require_authenticated)])
async def api_sources(user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("list_sources", {}, user))
    data = r.json()
    # Normalize: result may be {"result": [...]} or {"sources": [...]}
    sources = data.get("result") or data.get("sources") or []
    if isinstance(sources, list):
        return {"sources": sources}
    return {"sources": []}

@app.post("/api/datasets/save", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))])
async def api_dataset_save(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("save_dataset", body, user))
        r.raise_for_status()
    return r.json()


@app.get("/api/datasets/{name}/detail", dependencies=[Depends(require_authenticated)])
async def api_dataset_detail(name: str, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("get_dataset_definition", {"name": name}, user=user)


@app.post("/api/bronze/query", dependencies=[Depends(require_csrf)])
async def api_bronze_query(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    # Restricted to datasets.write because this endpoint accepts arbitrary SQL.
    # Read-only roles (viewer) must use the dataset-scoped endpoints below,
    # which build SQL server-side instead of trusting client input.
    sql     = body.get("sql", "").strip()
    limit   = min(int(body.get("limit", 200)), 2000)
    sources = body.get("sources") or []
    if not sql:
        raise HTTPException(400, "sql is required")
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=120) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "preview_transform",
                {
                    "sql": sql,
                    "limit": limit,
                    "sources": sources,
                    "user_context": _rls_user_context(user),
                },
                user,
            ),
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _upstream_error_detail(r, "Refinement query failed"))
    return r.json()


@app.delete("/api/datasets", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.delete"))])
async def api_delete_dataset(name: str, user: dict = Depends(require_permission("datasets.delete"))):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("delete_dataset", {"name": name}, user))
    if r.status_code == 404:
        raise HTTPException(404, f"Dataset '{name}' not found")
    return r.json()


@app.get("/api/datasets/{name}/lineage", dependencies=[Depends(require_authenticated)])
async def api_dataset_lineage(name: str, user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("get_lineage", {"name": name, "limit": 20}, user))
    return r.json()


# ── Object explorer (S3/MinIO listing + presigned downloads) ─────────────────

_EXPLORER_DEFAULT_BUCKETS = [
    {"id": "lakehouse", "label": "Lakehouse", "name": os.environ.get("MINIO_BUCKET", "lakehouse")},
    {"id": "ses_inbox", "label": "SES Inbox", "name": os.environ.get("SES_INBOX_BUCKET", "modecissions-mail-inbound-36243c")},
]

_EXPLORER_QUICKLINKS = [
    {"label": "SES inbox (incoming)", "bucket": "ses_inbox", "prefix": "inbound/"},
    {"label": "SES inbox (processed)", "bucket": "ses_inbox", "prefix": "inbound-processed/"},
    {"label": "Replicon uploads", "bucket": "lakehouse", "prefix": "uploads/replicon/in/"},
    {"label": "Raw - replicon", "bucket": "lakehouse", "prefix": "raw/replicon/"},
    {"label": "Silver - replicon", "bucket": "lakehouse", "prefix": "silver/replicon/"},
]


def _s3_client():
    import boto3
    endpoint_url = os.environ.get("S3_ENDPOINT_URL") or os.environ.get("AWS_S3_ENDPOINT_URL")
    if not endpoint_url and os.environ.get("MINIO_ENDPOINT"):
        scheme = "https" if os.environ.get("MINIO_SECURE", "false").lower() == "true" else "http"
        endpoint_url = f"{scheme}://{os.environ.get('MINIO_ENDPOINT')}"
    kwargs: dict = {
        "region_name": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
    }
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("MINIO_ACCESS_KEY")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("MINIO_SECRET_KEY")
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _resolve_explorer_bucket(bucket: str, user: dict | None = None) -> str:
    allowed: dict[str, str] = {}
    for item in _EXPLORER_DEFAULT_BUCKETS:
        name = item.get("name") or ""
        bid = item.get("id") or ""
        if bid and name:
            allowed[bid] = name
            allowed[name] = name
    resolved = allowed.get(bucket)
    if not resolved:
        raise HTTPException(403, "bucket not allowed")
    ctx = build_security_context(user)
    if not _is_security_admin_context(ctx) and bucket not in {"lakehouse", os.environ.get("MINIO_BUCKET", "lakehouse")}:
        raise HTTPException(403, "bucket requires admin role")
    return resolved


def _explorer_path_allowed(path: str, user: dict | None) -> bool:
    ctx = build_security_context(user)
    path = (path or "").lstrip("/")
    if _is_security_admin_context(ctx):
        return True
    if not path:
        return False
    prefixes = [str(p).lstrip("/") for p in ctx.get("allowed_prefixes") or []]
    if any(path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/") for prefix in prefixes):
        return True

    allowed = {
        str(item).strip().strip("/")
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip().strip("/")
    }
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return False
    root, cartridge = parts[0], parts[1]
    if cartridge not in allowed and "*" not in allowed:
        return False
    if root == "cartridges":
        return True
    if root not in {"raw", "silver", "gold", "uploads"}:
        return False

    tenant = str(ctx.get("tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or "").strip()
    if not tenant or not workspace:
        return True
    scoped_marker = f"tenant_id={tenant}/workspace_id={workspace}"
    normalized = path.rstrip("/")
    return f"/{scoped_marker}/" in f"/{normalized}/" or normalized.endswith(f"/{scoped_marker}")


@app.get("/api/explorer/buckets", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_explorer_buckets(user: dict = Depends(require_authenticated)):
    ctx = build_security_context(user)
    buckets = _EXPLORER_DEFAULT_BUCKETS if _is_security_admin_context(ctx) else [
        item for item in _EXPLORER_DEFAULT_BUCKETS if item.get("id") == "lakehouse"
    ]
    quicklinks = [
        item for item in _EXPLORER_QUICKLINKS
        if _explorer_path_allowed(item.get("prefix", ""), user)
        and (_is_security_admin_context(ctx) or item.get("bucket") == "lakehouse")
    ]
    return {"buckets": buckets, "quicklinks": quicklinks}


@app.get("/api/explorer/list", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_explorer_list(
    bucket: str,
    prefix: str = "",
    max_keys: int = 200,
    continuation_token: str | None = None,
    user: dict = Depends(require_authenticated),
):
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(prefix, user):
        raise HTTPException(403, "prefix not allowed")
    kwargs = {
        "Bucket": bucket_name,
        "Prefix": prefix,
        "MaxKeys": min(max(max_keys, 1), 1000),
        "Delimiter": "/",
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token
    try:
        resp = await asyncio.to_thread(s3.list_objects_v2, **kwargs)
    except Exception as exc:
        raise HTTPException(502, f"object storage list failed: {exc}") from exc
    objects = [
        {"key": o["Key"], "size": o["Size"], "last_modified": o["LastModified"].isoformat()}
        for o in resp.get("Contents", [])
        if o.get("Key") != prefix
    ]
    folders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    return {
        "bucket": bucket_name,
        "prefix": prefix,
        "folders": folders,
        "objects": objects,
        "next_token": resp.get("NextContinuationToken"),
        "is_truncated": bool(resp.get("IsTruncated", False)),
    }


@app.get("/api/explorer/download", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_explorer_download(
    request: Request,
    bucket: str = Query(...),
    key: str = Query(...),
    expires: int = 300,
    user: dict = Depends(require_authenticated),
):
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(key, user):
        raise HTTPException(403, "object not allowed")
    expires_in = min(max(int(expires), 60), 3600)
    try:
        url = await asyncio.to_thread(
            s3.generate_presigned_url,
            "get_object",
            Params={"Bucket": bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as exc:
        raise HTTPException(502, f"object storage download failed: {exc}") from exc
    await _audit.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="explorer.object.download",
        resource_type="object",
        resource_id=f"{bucket_name}/{key}",
        ip=request.client.host if request and request.client else None,
        status="success",
        metadata={"expires_in": expires_in},
    )
    return {"url": url, "expires_in": expires_in}


@app.delete(
    "/api/explorer/object",
    dependencies=[Depends(require_csrf), Depends(require_permission("pipelines.write"))],
)
async def api_explorer_delete(
    bucket: str,
    key: str,
    request: Request,
    confirm: str = Query(...),
    user: dict = Depends(require_authenticated),
):
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(key, user):
        raise HTTPException(403, "object not allowed")
    if confirm != key:
        raise HTTPException(400, "strong confirmation required")
    try:
        await asyncio.to_thread(s3.delete_object, Bucket=bucket_name, Key=key)
    except Exception as exc:
        raise HTTPException(502, f"object storage delete failed: {exc}") from exc
    await _audit.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="explorer.object.delete",
        resource_type="object",
        resource_id=f"{bucket_name}/{key}",
        ip=request.client.host if request.client else None,
        status="success",
    )
    return {"deleted": True, "bucket": bucket_name, "key": key}


@app.get("/api/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def api_lineage(cartridge: str | None = None, user: dict = Depends(require_permission("datasets.read"))):
    """Global lineage graph across raw sources and silver/gold datasets."""
    payload = await _refinement_invoke("list_datasets", {}, timeout=15, user=user)
    datasets = (payload or {}).get("datasets") or []
    if cartridge:
        datasets = [d for d in datasets if d.get("cartridge") == cartridge]

    by_name = {d["name"]: d for d in datasets if d.get("name")}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for d in datasets:
        name = d.get("name")
        if not name:
            continue
        nid = f"ds:{name}"
        nodes[nid] = {
            "id": nid,
            "label": name,
            "type": d.get("layer", "silver"),
            "cartridge": d.get("cartridge", ""),
            "is_stale": bool(d.get("is_stale")),
            "staleness_reason": d.get("staleness_reason"),
            "row_count": d.get("row_count"),
            "last_refresh": d.get("last_refresh"),
        }
        for src in (d.get("sources") or []):
            source = (src or "").strip()
            source_lower = source.lower()
            if source_lower.startswith("raw/"):
                rid = f"raw:{source[4:]}"
                if rid not in nodes:
                    parts = source[4:].split("/", 1)
                    nodes[rid] = {
                        "id": rid,
                        "label": parts[-1] if parts else source,
                        "type": "raw",
                        "cartridge": parts[0] if len(parts) > 1 else "",
                    }
                edges.append({"from": rid, "to": nid})
                continue
            candidates = [source, source.replace("silver_", "", 1), source.replace("gold_", "", 1)]
            if "/" in source:
                candidates.append(source.rsplit("/", 1)[-1])
            matched = next((candidate for candidate in candidates if candidate in by_name), None)
            if matched:
                edges.append({"from": f"ds:{matched}", "to": nid})

    return {"nodes": list(nodes.values()), "edges": edges}


# ── Analytic Apps ─────────────────────────────────────────────────────────────

def _workspace_server_url() -> str:
    raw = os.environ.get("WORKSPACE_INTERNAL_URL") or os.environ.get("WORKSPACE_BACKEND_URL")
    if raw:
        return raw.rstrip("/")
    public = os.environ.get("WORKSPACE_PUBLIC_URL") or os.environ.get("WORKSPACE_URL")
    if Path("/.dockerenv").exists() and public and re.match(r"^https?://(localhost|127\.0\.0\.1)(:|/|$)", public):
        return "http://workspace:8001"
    if public:
        return public.rstrip("/")
    if _is_production_env():
        logger.warning("WORKSPACE_INTERNAL_URL is not configured in production")
        return ""
    return "http://localhost:8001"


async def _proxy_workspace_app(request: Request, name: str, *, content: bool = False) -> Response:
    """Serve published analytic apps through Console while preserving Workspace sandboxing.

    Workspace still owns the app wrapper, content bridge and dataset visibility
    checks. Console only proxies the HTML so users stay on the same :8000
    origin as the rest of the console.
    """
    workspace_url = _workspace_server_url()
    if not workspace_url:
        raise HTTPException(503, "workspace internal URL is not configured")
    suffix = "/content" if content else ""
    upstream_headers = {
        key: value
        for key, value in {
            "cookie": request.headers.get("cookie"),
            "authorization": request.headers.get("authorization"),
            "x-workspace-id": request.headers.get("x-workspace-id"),
            "accept": request.headers.get("accept"),
            "user-agent": request.headers.get("user-agent"),
        }.items()
        if value
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
        r = await c.get(
            f"{workspace_url}/apps/{quote(name, safe='')}{suffix}",
            headers=upstream_headers,
        )
    response_headers = {
        key: value
        for key, value in r.headers.items()
        if key.lower() in {
            "content-security-policy",
            "content-type",
            "referrer-policy",
            "x-content-type-options",
            "x-frame-options",
        }
    }
    return Response(content=r.content, status_code=r.status_code, headers=response_headers)


@app.get("/apps/{name}/content", dependencies=[Depends(require_permission("apps.read"))])
async def serve_app_content_proxy(
    request: Request,
    name: str,
    user: dict = Depends(require_permission("apps.read")),
):
    return await _proxy_workspace_app(request, name, content=True)


@app.get("/apps/{name}", dependencies=[Depends(require_permission("apps.read"))])
async def serve_app(name: str, request: Request, user: dict = Depends(require_permission("apps.read"))):
    return await _proxy_workspace_app(request, name)


@app.get("/api/apps", dependencies=[Depends(require_permission("apps.read"))])
async def api_apps(user: dict = Depends(require_permission("apps.read"))):
    """List all published analytic apps."""
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("list_apps", {}, user))
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _upstream_error_detail(r, "Apps service unavailable"))
    return r.json()


@app.delete("/api/apps/{name}", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_apps_delete(name: str, user: dict = Depends(require_authenticated)):
    """Delete a published analytic app by name."""
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("delete_app", {"name": name}, user))
    payload = r.json()
    result = payload.get("result", payload)
    if not result.get("deleted"):
        raise HTTPException(404, result.get("error") or f"App '{name}' not found")
    return result


@app.get("/api/data/{dataset}", dependencies=[Depends(require_authenticated)])
async def api_data(dataset: str, request: Request, limit: int = 5000):
    """Return dataset rows as JSON array for use by analytic apps."""
    _validate_dataset_name(dataset)
    user = getattr(request.state, "user", None) or {}
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload(
                             "query_dataset",
                             {"name": dataset, "limit": limit, "user_context": _rls_user_context(user)},
                             user,
                         ))
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Dataset unavailable")
    data = r.json()
    return data.get("data", data)


@app.get("/api/data/{dataset}/options")
async def api_data_options(dataset: str, columns: str = "", user: dict = Depends(require_authenticated)):
    """Return distinct values per column for building filter selectors."""
    _validate_dataset_name(dataset)
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else []
    if not cols:
        raise HTTPException(400, "columns param required, e.g. ?columns=revenue_manager,cliente")

    # Validate column names (alphanumeric + underscore only)
    import re as _re
    for col in cols:
        if not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            raise HTTPException(400, f"Invalid column name: {col}")

    sqls = [f"SELECT DISTINCT {col} AS val, '{col}' AS col FROM pggold.gold_{dataset} WHERE {col} IS NOT NULL"
            for col in cols]
    union_sql = " UNION ALL ".join(sqls) + f" ORDER BY col, val"

    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload(
                             "preview_transform",
                             {
                                 "sql": union_sql,
                                 "limit": 5000,
                                 "user_context": _rls_user_context(user),
                             },
                             user,
                         ))
    result = r.json()
    rows = result.get("data", [])

    # Group by column name
    options: dict = {col: [] for col in cols}
    for row in rows:
        col_key = row.get("col")
        if col_key in options and row.get("val") is not None:
            options[col_key].append(str(row["val"]))
    return options


@app.post("/api/data/{dataset}/query", dependencies=[Depends(require_authenticated)])
async def api_data_query_filtered(dataset: str, body: dict, request: Request):
    """
    Execute a filtered query against a gold dataset.
    Body: {"filters": {"revenue_manager": "X", "fiscal_year": 2025,
                        "cliente": "Y", "proyecto": "Z"},
           "limit": 1000, "columns": ["col1", "col2"]}
    fiscal_year uses March-February logic automatically.
    """
    _validate_dataset_name(dataset)
    import re as _re
    filters   = body.get("filters", {})
    limit     = min(int(body.get("limit", 2000)), 10000)
    columns   = body.get("columns", ["*"])

    # Forward the authenticated user's context so refinement can apply RLS.
    _user = getattr(request.state, "user", None) or {}
    _user_context = _rls_user_context(_user)

    # Validate column names
    safe_cols = []
    for col in columns:
        if col == "*" or _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            safe_cols.append(col)
    select_clause = ", ".join(safe_cols) if safe_cols else "*"

    if not isinstance(filters, dict):
        raise HTTPException(400, "filters must be an object")
    if len(filters) > 20:
        raise HTTPException(400, "Too many filters (max 20)")

    # Build a parameterized query — values go into `params`, never interpolated into SQL.
    # Column/table identifiers are allowlisted via regex; only values are parametrized.
    params: list = []

    def _add_param(v) -> str:
        """Append value to params list and return a DuckDB positional placeholder."""
        s = str(v)
        if len(s) > 500:
            raise HTTPException(400, "Filter value too long (max 500 chars)")
        params.append(s)
        return "?"

    conditions = []
    for key, val in filters.items():
        if val is None or val == "" or val == []:
            continue
        if key == "fiscal_year":
            # March-February fiscal year: month<=2 belongs to previous calendar year.
            # fiscal_year values must be integers — cast before adding to params.
            fy_expr = "(CASE WHEN EXTRACT(MONTH FROM mes)<=2 THEN EXTRACT(YEAR FROM mes)-1 ELSE EXTRACT(YEAR FROM mes) END)"
            vals = val if isinstance(val, list) else [val]
            if len(vals) > 50:
                raise HTTPException(400, "Too many fiscal_year values (max 50)")
            placeholders = ",".join(_add_param(int(v)) for v in vals)
            conditions.append(f"{fy_expr} IN ({placeholders})")
        elif _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            vals = val if isinstance(val, list) else [val]
            if len(vals) > 100:
                raise HTTPException(400, f"Too many values for filter '{key}' (max 100)")
            if len(vals) == 1:
                conditions.append(f"{key} = {_add_param(vals[0])}")
            else:
                placeholders = ",".join(_add_param(v) for v in vals)
                conditions.append(f"{key} IN ({placeholders})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT {select_clause} FROM pggold.gold_{dataset} {where} LIMIT {limit}"

    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload(
                             "preview_transform",
                             {"sql": sql, "params": params, "limit": limit, "user_context": _user_context},
                             _user,
                         ))
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Query failed")
    result = r.json()
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result.get("data", [])


# ── Pipeline DAG ──────────────────────────────────────────────────────────────

@app.get("/studio/cartridges/{cartridge_id}/connections", dependencies=[Depends(require_authenticated)])
async def studio_cartridge_connections(cartridge_id: str, user: dict = Depends(require_authenticated)):
    """Proxy to Vault — returns masked connection config for the cartridge."""
    _require_cartridge_visible(user, cartridge_id)
    vault_url = _vault_url()
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        try:
            r = await c.get(f"{vault_url}/connections/{cartridge_id}")
            if r.status_code in (404, 204):
                return {"connections": []}
            if r.status_code >= 500:
                return {"connections": []}
            return r.json()
        except (httpx.HTTPError, ValueError):
            return {"connections": []}


@app.get("/api/pipeline", dependencies=[Depends(require_authenticated)])
async def api_pipeline(cartridge: str = "replicon", user: dict = Depends(require_authenticated)):
    """
    Ensambla el DAG completo: entidades × bronze status × silver datasets × gold deps.
    Fuentes: entity_config (entities), pipeline_runs + jobs (run history), refinement (datasets).
    """
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    import re as _re
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    def _freshness(dt_str: str | None, threshold_h: int = 24) -> str:
        if not dt_str:
            return "never"
        try:
            dt = _dt.fromisoformat(str(dt_str).replace("Z", "+00:00"))
            age = _dt.now(_tz.utc) - dt
            return "fresh" if age < _td(hours=threshold_h) else "stale"
        except Exception:
            return "unknown"

    # 1. Entities
    from app.services import cartridge_service as _cs
    entity_list: list[dict] = []
    manifest = await _cs.get_cartridge(cartridge)
    if manifest:
        for e in (manifest.get("entities") or []):
            entity_list.append({
                "entity":          e.get("id") or e.get("entity") or "",
                "mode":            e.get("mode", "full"),
                "watermark_field": e.get("watermark_field"),
                "description":     e.get("description", ""),
            })
    if not entity_list:
        entities_raw = await mcp_registry.invoke(cartridge, "list_entities", {}, user=user)
        if isinstance(entities_raw, dict):
            entity_list = entities_raw.get("entities", entities_raw.get("result", []))
        elif isinstance(entities_raw, list):
            entity_list = entities_raw

    # 2a. pipeline_runs — most recent run per entity (written by Airflow DAGs)
    dag_runs_by_entity: dict[str, dict] = {}
    try:
        _pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 2)
        rows_pg = await _pool.fetch(
            f"""SELECT DISTINCT ON (entity)
                   run_id, dag_id, entity, airflow_dag_run_id,
                   status, mode,
                   started_at, finished_at,
                   record_count, bytes_written, storage_uri,
                   duration_seconds, watermark_updated_to, error_message, extra
               FROM pipeline_runs
               WHERE cartridge_id = $1
                 {scope_sql}
               ORDER BY entity, started_at DESC""",
            cartridge,
            *scope_values,
        )
        for row in rows_pg:
            run = await _refresh_dag_run_status(dict(row), user)
            dag_runs_by_entity[row["entity"]] = run
    except Exception:
        logger.debug("Could not load pipeline_runs for %s", cartridge, exc_info=True)

    # 2b. jobs table — internal queue (legacy / console-triggered runs)
    all_jobs = await _call_with_optional_user(job_service.list_recent, 100, user=user)
    jobs_by_entity: dict[str, dict] = {}
    for j in all_jobs:
        entity = (j.get("args") or {}).get("entity") or ""
        if not entity or entity in jobs_by_entity:
            continue
        jobs_by_entity[entity] = j

    # 3. Silver datasets from refinement
    try:
        all_datasets = (await _refinement_invoke("list_datasets", {}, timeout=15, user=user)).get("datasets", [])
    except Exception:
        all_datasets = []
        # Some in-process tests replace ``httpx.AsyncClient`` with a minimal
        # get-only fake that predates the MCP invoke path. Keep that legacy
        # compatibility path working without changing production behavior.
        if not hasattr(httpx.AsyncClient, "post"):
            try:
                async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=15) as c:
                    r = await c.get(f"{REFINEMENT_URL}/datasets")
                if getattr(r, "status_code", 500) == 200:
                    all_datasets = (r.json() or {}).get("datasets", [])
            except Exception:
                all_datasets = []

    silver_ds = [d for d in all_datasets if d.get("layer") == "silver"]
    gold_ds   = [d for d in all_datasets if d.get("layer") == "gold"]

    silver_by_source: dict[str, list[dict]] = {}
    for ds in silver_ds:
        for src in (ds.get("sources") or []):
            silver_by_source.setdefault(src, []).append(ds)

    def _gold_deps_for_silver(silver_name: str) -> list[dict]:
        deps = []
        for gds in gold_ds:
            sql = gds.get("sql_def") or gds.get("sql") or ""
            if _re.search(rf"\bsilver_{_re.escape(silver_name)}\b", sql, _re.IGNORECASE):
                deps.append(gds)
        return deps

    # 4. Assemble pipeline rows
    rows = []
    for e in entity_list:
        entity = e.get("entity") or e.get("name") or ""
        source = f"raw/{cartridge}/{entity}"

        # Prefer pipeline_runs (Airflow DAGs); fall back to jobs table
        dag_run  = dag_runs_by_entity.get(entity)
        last_job = jobs_by_entity.get(entity)

        bronze_date  = None
        bronze_count = None
        last_run_info = None

        if dag_run:
            # Airflow DAG run is authoritative
            fin = dag_run.get("finished_at")
            bronze_date  = str(fin)[:10] if fin else None
            bronze_count = dag_run.get("record_count")
            dag_status   = _normalize_airflow_state(dag_run.get("status"))
            dag_run_id   = dag_run.get("airflow_dag_run_id") or dag_run.get("run_id")
            last_run_info = {
                "source":       "airflow",
                "dag_id":       dag_run.get("dag_id"),
                "dag_run_id":   dag_run_id,
                "run_id":       dag_run_id,
                "status":       dag_status,
                "mode":         dag_run.get("mode"),
                "triggered_at": str(dag_run.get("started_at", "")) if dag_run.get("started_at") else None,
                "started_at":   str(dag_run.get("started_at", "")) if dag_run.get("started_at") else None,
                "finished_at":  str(fin) if fin else None,
                "duration_sec": float(dag_run.get("duration_seconds")) if dag_run.get("duration_seconds") is not None else None,
                "error":        dag_run.get("error_message"),
            }
        elif last_job:
            if last_job.get("status") == "done":
                res = last_job.get("result") or {}
                bronze_date  = (last_job.get("finished_at") or last_job.get("created_at") or "")[:10]
                bronze_count = res.get("record_count") or res.get("total_records")
            last_run_info = {
                "source":      "jobs",
                "job_id":      last_job["job_id"],
                "status":      last_job["status"],
                "finished_at": last_job.get("finished_at") or last_job.get("created_at"),
                "message":     last_job.get("message"),
            }

        if not bronze_date or bronze_count is None:
            physical_bronze = await _call_with_optional_user(
                _bronze_physical_snapshot,
                cartridge,
                entity,
                user=user,
            )
            if physical_bronze:
                bronze_date = bronze_date or physical_bronze.get("latest_date")
                if bronze_count is None:
                    bronze_count = physical_bronze.get("record_count")

        # Bronze freshness
        if dag_run and dag_run["status"] == "failed" and not bronze_date:
            bronze_status = "error"
        elif bronze_date:
            bronze_status = _freshness(bronze_date + "T00:00:00+00:00")
        else:
            bronze_status = "never"

        # Silver/Gold nodes
        is_failed = (dag_run and dag_run["status"] == "failed") or (last_job and last_job.get("status") == "failed")
        silver_nodes = []
        gold_nodes   = []
        for ds in silver_by_source.get(source, []):
            s_status = "stale" if is_failed else _freshness(ds.get("last_refresh"), threshold_h=24)
            silver_nodes.append({
                "name":         ds["name"],
                "layer":        ds.get("layer", "silver"),
                "row_count":    ds.get("row_count"),
                "last_refresh": ds.get("last_refresh"),
                "status":       s_status,
            })
            for gds in _gold_deps_for_silver(ds["name"]):
                if not any(g["name"] == gds["name"] for g in gold_nodes):
                    gold_nodes.append({
                        "name":         gds["name"],
                        "layer":        "gold",
                        "row_count":    gds.get("row_count"),
                        "last_refresh": gds.get("last_refresh"),
                        "status":       _freshness(gds.get("last_refresh"), threshold_h=24),
                    })

        rows.append({
            "entity":    entity,
            "cartridge": cartridge,
            "modes":     e.get("modes") or ([e["mode"]] if e.get("mode") else ["full"]),
            "watermark": e.get("watermark_field") or "",
            "last_run":  last_run_info,
            # Keep last_job for backward compat with pipeline.html polling logic
            "last_job":  {
                "job_id":       last_run_info.get("job_id") if last_run_info else None,
                "dag_id":       last_run_info.get("dag_id") if last_run_info else None,
                "dag_run_id":   last_run_info.get("dag_run_id") if last_run_info else None,
                "status":       last_run_info.get("status") if last_run_info else None,
                "mode":         last_run_info.get("mode") if last_run_info else None,
                "triggered_at": last_run_info.get("triggered_at") if last_run_info else None,
                "finished_at":  last_run_info.get("finished_at") if last_run_info else None,
                "duration_sec": last_run_info.get("duration_sec") if last_run_info else None,
                "created_at":   last_run_info.get("finished_at") or last_run_info.get("triggered_at") if last_run_info else None,
                "message":      last_run_info.get("message") if last_run_info else None,
            } if last_run_info else None,
            "bronze": {
                "source":       source,
                "latest_date":  bronze_date,
                "record_count": bronze_count,
                "status":       bronze_status,
            },
            "silver": silver_nodes,
            "gold":   gold_nodes,
        })

    _order = {"running": 0, "error": 1, "stale": 2, "fresh": 3, "never": 4, "unknown": 5}
    rows.sort(key=lambda r: _order.get(r["bronze"]["status"], 5))
    return {"pipeline": rows}


@app.get("/api/dag_templates", dependencies=[Depends(require_authenticated)])
async def api_dag_templates():
    from app.services import dag_templates
    return {"templates": dag_templates.get_all()}


@app.get("/api/dag_templates/{template_id}", dependencies=[Depends(require_authenticated)])
async def api_dag_template_code(template_id: str,
                                cartridge: str = "my_cartridge",
                                entity: str = "MyEntity"):
    from app.services import dag_templates
    code = dag_templates.get_code(template_id, cartridge, entity)
    if code is None:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return {"id": template_id, "cartridge": cartridge, "entity": entity, "code": code}


@app.get("/api/pipeline_runs", dependencies=[Depends(require_authenticated)])
async def api_pipeline_runs(cartridge: str = "replicon", entity: str = None, limit: int = 50, user: dict = Depends(require_authenticated)):
    """Recent DAG run history from pipeline_runs table."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    try:
        pool = await _get_db_pool()
        if entity:
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
            rows = await pool.fetch(
                "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $3",
                cartridge, entity, limit, *scope_values,
            )
        else:
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
            rows = await pool.fetch(
                "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 "
                f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $2",
                cartridge, limit, *scope_values,
            )
        return {"runs": [dict(r) for r in rows]}
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("pipeline runs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")


def _format_pipeline_entity_run(row: dict) -> dict:
    dag_run_id = row.get("airflow_dag_run_id") or row.get("run_id")
    return {
        "dag_id":       row.get("dag_id"),
        "dag_run_id":   dag_run_id,
        "status":       _normalize_airflow_state(row.get("status")),
        "mode":         row.get("mode"),
        "triggered_at": str(row.get("started_at")) if row.get("started_at") else None,
        "started_at":   str(row.get("started_at")) if row.get("started_at") else None,
        "finished_at":  str(row.get("finished_at")) if row.get("finished_at") else None,
        "duration_sec": float(row.get("duration_seconds")) if row.get("duration_seconds") is not None else None,
        "error":        row.get("error_message"),
    }


@app.get("/api/pipeline/{cartridge}/{entity}/runs", dependencies=[Depends(require_authenticated)])
async def api_pipeline_entity_runs(cartridge: str, entity: str, limit: int = 20, user: dict = Depends(require_authenticated)):
    """Recent DAG-based pipeline runs for one cartridge entity."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")

    safe_limit = max(1, min(int(limit or 20), 100))

    try:
        pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
        rows = await pool.fetch(
            f"""
            SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                   started_at, finished_at, duration_seconds, error_message
              FROM pipeline_runs
             WHERE cartridge_id=$1 AND entity=$2
               {scope_sql}
             ORDER BY started_at DESC NULLS LAST
             LIMIT $3
            """,
            cartridge,
            entity,
            safe_limit,
            *scope_values,
        )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("entity runs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    runs = []
    for row in rows:
        refreshed = await _refresh_dag_run_status(dict(row), user)
        runs.append(_format_pipeline_entity_run(refreshed))

    return {
        "cartridge": cartridge,
        "entity": entity,
        "runs": runs,
    }


@app.get("/api/pipeline/{cartridge}/{entity}/runs/{dag_run_id}/logs", dependencies=[Depends(require_authenticated)])
async def api_pipeline_run_logs(cartridge: str, entity: str, dag_run_id: str, user: dict = Depends(require_authenticated)):
    """Basic DAG run logs summary for one cartridge entity run."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")

    try:
        pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
        row = await pool.fetchrow(
            f"""
            SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                   started_at, finished_at, duration_seconds, error_message
              FROM pipeline_runs
             WHERE cartridge_id=$1
               AND entity=$2
               AND (run_id=$3 OR airflow_dag_run_id=$3)
               {scope_sql}
             ORDER BY started_at DESC NULLS LAST
             LIMIT 1
            """,
            cartridge,
            entity,
            dag_run_id,
            *scope_values,
        )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("run logs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    if not row:
        raise HTTPException(404, f"Run '{dag_run_id}' not found for {cartridge}/{entity}")

    run = await _refresh_dag_run_status(dict(row), user)
    dag_id = run.get("dag_id") or metadata.get("dag_id")
    resolved_dag_run_id = run.get("airflow_dag_run_id") or run.get("run_id") or dag_run_id
    response = {
        "cartridge": cartridge,
        "entity": entity,
        "dag_id": dag_id,
        "dag_run_id": resolved_dag_run_id,
        "status": _normalize_airflow_state(run.get("status")),
        "tasks": [],
        "logs": [],
        "error": run.get("error_message"),
        "available": False,
    }

    try:
        tasks_result = await mcp_registry.invoke("infra", "airflow_list_task_instances", {
            "dag_id": dag_id,
            "dag_run_id": resolved_dag_run_id,
        }, user=user)
        if tasks_result.get("error"):
            response["error"] = tasks_result["error"]
            return response

        tasks = tasks_result.get("tasks") or []
        response["tasks"] = tasks
        logs = []
        for task in tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            log_result = await mcp_registry.invoke("infra", "airflow_get_task_logs", {
                "dag_id": dag_id,
                "dag_run_id": resolved_dag_run_id,
                "task_id": task_id,
            }, user=user)
            if log_result.get("error"):
                logs.append({"task_id": task_id, "available": False, "error": log_result["error"]})
            else:
                logs.append({"task_id": task_id, "available": True, "logs": log_result.get("logs", "")})

        response["logs"] = logs
        response["available"] = bool(tasks) and all(item.get("available") for item in logs)
        return response
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("run logs Airflow fetch failed error_id=%s", _eid)
        response["error"] = f"Internal server error. error_id={_eid}"
        return response


@app.post("/api/pipeline/{cartridge}/{entity}/extract", dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)])
async def api_pipeline_extract(
    cartridge: str,
    entity: str,
    body: dict | None = None,
    user: dict = Depends(require_permission("pipelines.run")),
):
    """Trigger extraction for a single entity. Returns job_id for polling."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    body = body or {}
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if (metadata.get("pattern") or "").lower() == "dag-based":
        if not metadata.get("entity"):
            raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")
        if not metadata.get("enabled"):
            raise HTTPException(400, f"Entity '{entity}' is disabled")
        dag_id = metadata.get("dag_id")
        if not dag_id:
            raise HTTPException(400, f"No dag_id configured for {cartridge}.{entity}")

        conf = _apply_user_scope_to_dag_conf(
            _build_dag_extract_conf(cartridge, entity, metadata.get("mode"), body),
            user,
        )
        requested_dag_run_id = _dag_run_id_from_idempotency_key(
            dag_id,
            body.get("idempotency_key") or body.get("request_id"),
        )
        result = await _trigger_airflow_extract_dag(dag_id, conf, user, requested_dag_run_id)
        if result.get("error"):
            raise HTTPException(502, f"Airflow trigger failed: {result['error']}")
        dag_run_id = result.get("dag_run_id") or result.get("run_id")
        await _record_dag_pipeline_trigger(
            cartridge=cartridge,
            entity=entity,
            dag_id=dag_id,
            dag_run_id=dag_run_id,
            mode=conf.get("mode", metadata.get("mode") or "incremental"),
            status=result.get("state") or "queued",
            conf=conf,
            tenant_id=conf.get("tenant_id"),
            workspace_id=conf.get("workspace_id"),
        )
        return {
            "triggered": True,
            "cartridge": cartridge,
            "entity": entity,
            "dag_id": dag_id,
            "run_id": dag_run_id,
            "dag_run_id": dag_run_id,
            "state": result.get("state"),
            "conf": conf,
        }

    mode = body.get("mode", "incremental")
    result = await mcp_registry.invoke(cartridge, "extract", {
        "entity": entity,
        "mode": mode,
    }, user=user)
    return result


@app.post(
    "/api/pipeline/{cartridge}/extract_all",
    dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)],
)
async def api_pipeline_extract_all(
    cartridge: str,
    body: dict | None = None,
    user: dict = Depends(require_permission("pipelines.run")),
):
    """Trigger extraction for every entity currently visible in the pipeline."""
    body = body or {}
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    pipeline = await _call_with_optional_user(api_pipeline, cartridge, user=user)
    rows = pipeline.get("pipeline") or []
    triggered: list[dict] = []
    errors: list[dict] = []

    for row in rows:
        entity = row.get("entity")
        if not entity:
            continue
        try:
            result = await _call_with_optional_user(api_pipeline_extract, cartridge, entity, body, user=user)
            triggered.append({
                "entity": entity,
                "job_id": result.get("job_id") or result.get("dag_run_id") or result.get("run_id"),
                "dag_run_id": result.get("dag_run_id") or result.get("run_id"),
                "dag_id": result.get("dag_id"),
                "state": result.get("state") or result.get("status"),
                "result": result,
            })
        except HTTPException as exc:
            errors.append({
                "entity": entity,
                "status_code": exc.status_code,
                "error": str(exc.detail),
            })
        except Exception:
            error_id = uuid.uuid4().hex
            logger.exception("pipeline extract_all failed for %s.%s error_id=%s", cartridge, entity, error_id)
            errors.append({
                "entity": entity,
                "status_code": 500,
                "error": f"Internal server error. error_id={error_id}",
            })

    return {
        "cartridge": cartridge,
        "triggered": triggered,
        "errors": errors,
        "count": len(triggered),
        "error_count": len(errors),
    }


async def _pipeline_extract_metadata(cartridge: str, entity: str) -> dict:
    pool = await _get_db_pool()
    row = await pool.fetchrow(
        """
        SELECT
            c.id AS cartridge_id,
            c.pattern AS pattern,
            e.entity AS entity,
            e.dag_id AS dag_id,
            e.mode AS mode,
            e.enabled AS enabled,
            e.primary_key AS primary_key
        FROM cartridges c
        LEFT JOIN entity_config e
          ON e.cartridge_id = c.id
         AND e.entity = $2
        WHERE c.id = $1
        """,
        cartridge,
        entity,
    )
    if not row:
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")
    return dict(row)


def _build_dag_extract_conf(cartridge: str, entity: str, configured_mode: str | None, body: dict) -> dict:
    mode = body.get("mode") or configured_mode or "incremental"
    conf = {
        "cartridge_id": cartridge,
        "entity": entity,
        "mode": mode,
    }
    if body.get("from_date"):
        conf["from_date"] = body["from_date"]
    if body.get("to_date"):
        conf["to_date"] = body["to_date"]
    return conf


def _apply_user_scope_to_dag_conf(conf: dict, user: dict | None) -> dict:
    scoped = dict(conf or {})
    ctx = build_security_context(user)
    tenant_id = ctx.get("tenant_id")
    workspace_id = ctx.get("workspace_id")
    if not tenant_id or not workspace_id:
        return scoped
    for key, value in (("tenant_id", tenant_id), ("workspace_id", workspace_id)):
        existing = scoped.get(key)
        if existing and str(existing) != str(value):
            raise HTTPException(403, detail=f"{key} scope mismatch")
        scoped[key] = str(value)
    scoped["security_context"] = ctx
    return scoped


def _dag_run_id_from_idempotency_key(dag_id: str, idempotency_key: object | None) -> str | None:
    if idempotency_key is None:
        return None
    key = str(idempotency_key).strip()
    if not key:
        return None
    if len(key) > 160:
        raise HTTPException(400, "idempotency_key is too long")
    return f"console__{dag_id}__{uuid.uuid5(uuid.NAMESPACE_URL, f'{dag_id}:{key}').hex}"


async def _trigger_airflow_extract_dag(
    dag_id: str,
    conf: dict,
    user: dict | None,
    dag_run_id: str | None = None,
) -> dict:
    scoped_conf = _apply_user_scope_to_dag_conf(conf, user)
    result: dict = {}
    for attempt in range(5):
        args = {
            "dag_id": dag_id,
            "conf": scoped_conf,
        }
        if dag_run_id:
            args["dag_run_id"] = dag_run_id
        result = await mcp_registry.invoke("infra", "airflow_trigger_dag", args, user=user)
        error = result.get("error")
        if not error:
            return result
        if not _is_transient_airflow_trigger_error(error) or attempt == 4:
            return result
        await asyncio.sleep(4)
    return result


def _is_transient_airflow_trigger_error(error: str) -> bool:
    lowered = str(error).lower()
    return "connection" in lowered or "connect" in lowered


# ── Studio — Entity config ───────────────────────────────────────────────────

@app.post("/studio/cartridges/{cartridge_id}/entities/{entity}/rename", dependencies=[Depends(require_csrf)])
async def studio_rename_entity(
    cartridge_id: str,
    entity: str,
    body: dict,
    _global_admin: dict = Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    user: dict = Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
):
    _require_cartridge_visible(user, cartridge_id)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")
    if new_name == entity:
        return {"renamed": False, "reason": "same name"}
    # Verify old entity exists
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    entities = [e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])]
    if entity not in entities:
        raise HTTPException(404, f"Entity '{entity}' not found in cartridge '{cartridge_id}'")
    if new_name in entities:
        raise HTTPException(409, f"Entity '{new_name}' already exists")
    await cartridge_service.rename_entity(cartridge_id, entity, new_name)
    return {"renamed": True, "old_name": entity, "new_name": new_name}


@app.patch("/studio/cartridges/{cartridge_id}/entities/{entity}", dependencies=[Depends(require_csrf)])
async def studio_update_entity(
    cartridge_id: str,
    entity: str,
    body: dict,
    _global_admin: dict = Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    user: dict = Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
):
    """Update entity_config fields."""
    _require_cartridge_visible(user, cartridge_id)
    allowed = {"display_name", "mode", "primary_key", "dag_id",
               "trigger_type", "cron_expression", "description", "enabled",
               "dag_params", "connection_id"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    await cartridge_service.upsert_entity(cartridge_id, entity, **updates)
    return {"updated": True, "entity": entity, **updates}


# ── Studio — Cartridge management ────────────────────────────────────────────

@app.get("/studio/cartridges", dependencies=[Depends(require_authenticated)])
async def studio_list_cartridges(user: dict = Depends(require_authenticated)):
    cartridges = await cartridge_service.list_cartridges()
    ctx = build_security_context(user)
    allowed = {str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()}
    if "*" not in allowed:
        cartridges = [c for c in cartridges if str(c.get("id") or c.get("cartridge") or "").strip() in allowed]
    return {"cartridges": cartridges}


# ── Microservice-backed cartridges (probed via internal HTTP) ───────────────
# Maps cartridge_id → internal base URL of the cartridge microservice. When a
# cartridge is in this map, /studio/cartridges/{id}/status probes the service's
# /health endpoint to decide whether to report it as operational, degraded
# (live but missing credentials) or offline (not responding).
_MICROSERVICE_CARTRIDGES = {
    "sap_successfactors": os.environ.get(
        "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"
    ),
    "sap_hcm": os.environ.get(
        "SAP_HCM_URL", "http://sap-hcm:8202"
    ),
    "sap_s4hana": os.environ.get(
        "SAP_S4HANA_URL", "http://sap-s4hana:8204"
    ),
}


def _internal_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE")
    if not key and _is_production_env():
        raise RuntimeError("Missing INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE; legacy fallback disabled in production")
    return {"x-api-key": key or INTERNAL_API_KEY, "x-internal-service": "console"}


async def _probe_microservice(base_url: str, cartridge_id: str) -> dict:
    """Probe a cartridge microservice and classify its status.

    Returns one of:
      - {"status": "operational",            ...} — /health and credentials OK
      - {"status": "degraded",     "reason": ...} — /health OK, credentials missing
      - {"status": "offline",      "reason": ...} — /health unreachable
    """
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{base_url}/health")
    except (httpx.HTTPError, OSError) as exc:
        return {"status": "offline", "reason": f"/health unreachable: {exc!s}"}
    if r.status_code >= 500 or not r.is_success:
        return {"status": "offline", "reason": f"/health HTTP {r.status_code}"}

    # Liveness ok — try the deep check that hits SAP. Anything other than 200
    # means the service is up but credentials / connectivity are pending.
    try:
        async with httpx.AsyncClient(timeout=5, headers=_internal_headers()) as c:
            deep = await c.get(f"{base_url}/health/{cartridge_id}")
        if deep.is_success:
            data = deep.json() if "application/json" in deep.headers.get("content-type", "") else {}
            return {"status": "operational", **(data or {})}
        try:
            payload = deep.json()
        except Exception:
            payload = {"detail": deep.text[:200]}
        return {"status": "degraded", "reason": payload}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"status": "degraded", "reason": f"deep health unavailable: {exc!s}"}


@app.get("/studio/cartridges/{cartridge_id}/status", dependencies=[Depends(require_authenticated)])
async def studio_cartridge_status(cartridge_id: str, user: dict = Depends(require_authenticated)):
    """Lightweight status probe for the cartridge.

    For Replicon (and any cartridge not backed by a dedicated microservice in
    this deployment) we just report ``operational`` if it is registered.
    For SAP cartridges we probe the corresponding FastAPI service.
    """
    _require_cartridge_visible(user, cartridge_id)
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")

    base_url = _MICROSERVICE_CARTRIDGES.get(cartridge_id)
    if not base_url:
        return {"cartridge_id": cartridge_id, "status": "operational"}

    probe = await _probe_microservice(base_url, cartridge_id)
    return {"cartridge_id": cartridge_id, **probe}


@app.post("/studio/cartridges", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def studio_create_cartridge(body: dict):
    cid  = body.get("id", "").strip()
    name = body.get("name", "").strip()
    if not cid or not name:
        raise HTTPException(400, "id and name are required")
    existing = await cartridge_service.get_cartridge(cid)
    if existing:
        raise HTTPException(409, f"Cartridge '{cid}' already exists")
    try:
        manifest = await cartridge_service.create_cartridge(cid, name, body.get("description", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return manifest


@app.get("/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_authenticated)])
async def studio_get_cartridge(cartridge_id: str, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge_id)
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    return manifest


@app.patch("/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def studio_update_cartridge(cartridge_id: str, body: dict, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    try:
        return await cartridge_service.update_cartridge(cartridge_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/studio/cartridges/{cartridge_id}/spec", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def studio_upload_spec(cartridge_id: str, file: UploadFile = File(...), user: dict = Depends(require_authenticated)):
    """Upload a spec file (OpenAPI YAML, WSDL, OData $metadata) for the cartridge."""
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    content = (await file.read()).decode("utf-8", errors="replace")
    try:
        key = cartridge_service.upload_spec(cartridge_id, file.filename or "spec.yaml", content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"uploaded": key, "filename": file.filename, "size": len(content)}


def _require_cartridge_visible(user: dict | None, cartridge_id: str) -> None:
    if user is None:
        return
    ctx = build_security_context(user)
    allowed = {str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()}
    if "*" in allowed:
        return
    if str(cartridge_id) not in allowed:
        raise HTTPException(403, "cartridge not allowed")


@app.get("/studio/cartridges/{cartridge_id}/export")
async def studio_export_cartridge(
    cartridge_id: str,
    user: dict = Depends(require_permission("cartridges.read")),
):
    """Download the cartridge as a ZIP archive."""
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    try:
        zip_bytes = await cartridge_service.export_cartridge(cartridge_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{cartridge_id}.zip"'},
    )


@app.post("/studio/import", dependencies=[Depends(require_csrf), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def studio_import_cartridge(file: UploadFile = File(...), user: dict = Depends(require_authenticated)):
    """Import a cartridge from a previously exported ZIP."""
    zip_bytes = await file.read()
    try:
        manifest = await cartridge_service.import_cartridge(zip_bytes, actor_user=user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return manifest


# ── Studio — AI assistant ─────────────────────────────────────────────────────

@app.post("/studio/chat", dependencies=[Depends(require_csrf), Depends(require_permission("studio.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def studio_chat(body: dict, user: dict = Depends(require_authenticated)):
    cartridge_id = body.get("cartridge_id")
    manifest     = await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    return await studio_assistant.chat(
        message  = body.get("message", ""),
        history  = body.get("history", []),
        step     = body.get("step", 1),
        manifest = manifest,
        actor_role = user.get("role"),
        actor_user = user,
    )


@app.post("/studio/chat/stream", dependencies=[Depends(require_csrf), Depends(require_permission("studio.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def studio_chat_stream(body: dict, user: dict = Depends(require_authenticated)):
    """SSE-style streaming chat: emits tool_use / tool_result / text / done / error
    events as the assistant runs, so the UI can show a live reasoning trail."""
    cartridge_id = body.get("cartridge_id")
    manifest     = await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    message      = (body.get("message") or "").strip()
    history      = body.get("history") or []
    step         = body.get("step", 1)
    if not message:
        raise HTTPException(400, "message is required")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            result = await studio_assistant.chat(
                message  = message,
                history  = history,
                step     = step,
                manifest = manifest,
                on_event = on_event,
                actor_role = user.get("role"),
                actor_user = user,
            )
            await queue.put({"type": "done", **result})
        except Exception:
            _eid = uuid.uuid4().hex
            logger.exception("studio assistant chat failed error_id=%s", _eid)
            await queue.put({"type": "error", "message": f"Internal server error. error_id={_eid}"})

    asyncio.create_task(run())

    async def event_stream():
        yield "event: open\ndata: {}\n\n"
        while True:
            evt = await queue.get()
            etype = evt.get("type", "message")
            yield f"event: {etype}\ndata: {json.dumps(evt, default=str)}\n\n"
            if etype in ("done", "error"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/studio", dependencies=[Depends(require_permission("studio.read"))])
async def studio_page():
    return FileResponse(STATIC / "studio.html")

@app.get("/marketplace", dependencies=[Depends(require_permission("marketplace.read"))])
async def marketplace_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "marketplace/index.html")

@app.get("/customer/cartridges", dependencies=[Depends(require_permission("marketplace.read"))])
async def customer_cartridges_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "customer/cartridges/index.html")

@app.get(
    "/admin/installations",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def admin_installations_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "admin/installations/index.html")

@app.get(
    "/admin/licenses",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def admin_licenses_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "admin/licenses/index.html")

@app.get("/api/marketplace/products", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_marketplace_products(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_products(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.get("/api/marketplace/products/{cartridge_id}", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_marketplace_product(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return {"product": await marketplace_service.get_product(cartridge_id, user)}
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, str(exc)) from exc

@app.get("/api/marketplace/installations", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_marketplace_installations(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.get("/api/customer/cartridges", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_customer_cartridges(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/marketplace/products/{cartridge_id}/request",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.request")),
    ],
)
async def api_marketplace_request(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.request_product(cartridge_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/marketplace/products/{cartridge_id}/activate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_marketplace_activate(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.activate_product(cartridge_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/marketplace/installations/{installation_id}/retry",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.request")),
    ],
)
async def api_marketplace_retry(installation_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.retry_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        message = str(exc)
        lowered = message.lower()
        status = 403 if "permission" in lowered or "access" in lowered or "allowed" in lowered else (
            409 if "approval" in lowered or "active" in lowered or "state" in lowered or "status" in lowered else 404
        )
        raise HTTPException(status, message) from exc

@app.get(
    "/api/admin/installations",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installations(user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.list_admin_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(403, str(exc)) from exc

@app.get(
    "/api/admin/installations/{installation_id}",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.get_admin_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, str(exc)) from exc

@app.get(
    "/api/admin/installations/{installation_id}/access",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_access(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.list_installation_access(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, str(exc)) from exc

@app.patch(
    "/api/admin/installations/{installation_id}/access/{target_user_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_user_access(
    installation_id: str,
    target_user_id: int,
    payload: dict = Body(default_factory=dict),
    user: dict = Depends(get_current_global_user),
):
    try:
        return await marketplace_service.set_installation_user_access(
            installation_id,
            target_user_id,
            payload.get("mode"),
            payload.get("reason"),
            user,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/admin/installations/{installation_id}/approve",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_approve(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.approve_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/admin/installations/{installation_id}/pause",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_pause(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.pause_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/admin/installations/{installation_id}/revoke",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_revoke(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.revoke_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post(
    "/api/admin/installations/{installation_id}/reactivate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_reactivate(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.reactivate_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc

def _viewer_redirect(request: Request, viewer_type: str, **params: str) -> RedirectResponse:
    query = dict(request.query_params)
    query["type"] = viewer_type
    for key, value in params.items():
        if value:
            query[key] = value
    return RedirectResponse(url=f"/viewer?{urlencode(query)}", status_code=307)


@app.get("/viewer/pipeline", dependencies=[Depends(require_permission("monitor.read"))])
async def viewer_pipeline(request: Request):
    return _viewer_redirect(request, "pipeline")

@app.get("/viewer/vault", dependencies=[Depends(require_permission("vault.connections.read"))])
async def viewer_vault(request: Request):
    return _viewer_redirect(request, "vault")

@app.get("/explorer", dependencies=[Depends(require_permission("pipelines.read"))])
async def explorer_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "explorer/index.html")

@app.get("/viewer/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def viewer_lineage(request: Request):
    return _viewer_redirect(request, "lineage")

@app.get("/rag", dependencies=[Depends(require_admin)])
async def rag_page():
    # MEJORAS moved RAG operation into Studio step 7; keep /rag as a
    # compatibility entrypoint without serving the removed standalone page.
    return RedirectResponse(url="/studio")


# ── Agents — CRUD + invoke ────────────────────────────────────────────────────
# Agent definitions include prompts, tool allowlists and execution traces. Keep
# the detailed API on admin-only surfaces until per-agent workspace ACLs land.

@app.get("/agents", dependencies=[Depends(require_admin)])
async def viewer_agents(request: Request):
    require_admin(request)
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "agents/index.html")


@app.get("/api/agents", dependencies=[Depends(require_admin)])
async def api_agents_list(
    request: Request,
    cartridge_id: str | None = None,
    include_inactive: bool = False,
):
    require_admin(request)
    return {"agents": await _agents.list_agents(cartridge_id, include_inactive)}


@app.get("/api/agents/_tool-catalog", dependencies=[Depends(require_admin)])
async def api_agents_tool_catalog(request: Request):
    """Aggregate of tools exposed by every MCP server — used by the agent
    editor UI to populate the 'allowed_tools' multi-select."""
    require_admin(request)
    out: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for srv_id, base in _agent_runtime.SERVER_URLS.items():
            try:
                server_key = "MCP_INFRA" if srv_id == "mcp-infra" else srv_id.upper()
                r = await c.get(f"{base}/mcp/tools", headers=_hdr_for(server_key))
                r.raise_for_status()
                out[srv_id] = [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        **tool_manifest.classify_tool(t["name"]),
                    }
                    for t in (r.json().get("tools") or [])
                ]
            except Exception:
                out[srv_id] = []
    return {"servers": out}


@app.get("/api/agents/{agent_id}", dependencies=[Depends(require_admin)])
async def api_agents_get(request: Request, agent_id: str):
    require_admin(request)
    a = await _agents.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "agent not found")
    return a


@app.post("/api/agents", dependencies=[Depends(require_csrf), Depends(require_role(ROLE_ADMIN))])
async def api_agents_create(request: Request, body: dict):
    user = require_admin(request)
    try:
        return await _agents.create_agent(body, owner_user_id=user.get("id"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.patch("/api/agents/{agent_id}", dependencies=[Depends(require_csrf), Depends(require_role(ROLE_ADMIN))])
async def api_agents_update(request: Request, agent_id: str, body: dict):
    require_admin(request)
    try:
        a = await _agents.update_agent(agent_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not a:
        raise HTTPException(404, "agent not found")
    return a


@app.delete("/api/agents/{agent_id}", dependencies=[Depends(require_csrf), Depends(require_role(ROLE_ADMIN))])
async def api_agents_delete(request: Request, agent_id: str):
    require_admin(request)
    ok = await _agents.delete_agent(agent_id)
    if not ok:
        raise HTTPException(404, "agent not found")
    return {"deleted": True}


@app.post("/api/agents/{agent_id}/invoke", dependencies=[Depends(require_csrf), Depends(require_admin)])
async def api_agents_invoke(request: Request, agent_id: str, body: dict):
    user  = require_admin(request)
    agent = await _agent_runtime.load_agent(agent_id)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []
    result = await _agent_runtime.run(agent, message, history=history, user=user)
    return result


_AGENT_RUNNER_TOKEN = os.environ.get("AGENT_RUNNER_TOKEN", "")
_AGENT_RUNNER_INTERVAL_MINUTES = int(os.environ.get("AGENT_RUNNER_INTERVAL_MINUTES", "5"))
_AGENT_RUNNER_GRACE_MINUTES = int(os.environ.get("AGENT_RUNNER_GRACE_MINUTES", "1"))


def _agent_schedule_due(schedule: dict) -> bool:
    cron_expr = str(schedule.get("cron") or schedule.get("cron_expression") or "").strip()
    if not cron_expr:
        return False
    try:
        from croniter import croniter
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from zoneinfo import ZoneInfo
    except Exception as exc:
        raise HTTPException(503, "cron scheduler dependency unavailable") from exc
    try:
        tz = ZoneInfo(str(schedule.get("tz") or "UTC"))
        now_utc = _dt.now(_tz.utc)
        window_start_utc = now_utc - _td(minutes=max(_AGENT_RUNNER_INTERVAL_MINUTES, 1) + _AGENT_RUNNER_GRACE_MINUTES)
        window_end_utc = now_utc + _td(minutes=_AGENT_RUNNER_GRACE_MINUTES)
        iterator = croniter(cron_expr, window_start_utc.astimezone(tz))
        next_fire = iterator.get_next(_dt)
        if next_fire.tzinfo is None:
            next_fire = next_fire.replace(tzinfo=tz)
        next_fire_utc = next_fire.astimezone(_tz.utc)
    except Exception as exc:
        raise HTTPException(403, "agent schedule cron is invalid") from exc
    return window_start_utc <= next_fire_utc <= window_end_utc


@app.post("/api/agents/{agent_id}/invoke/scheduled", dependencies=[Depends(verify_internal_api_key)])
async def api_agents_invoke_scheduled(request: Request, agent_id: str, body: dict):
    """Cron-driven invocation from the airflow `agent_runner` DAG. Uses a
    shared token so it can run without a user session. The agent_runs row
    is logged with user_id=NULL."""
    token = request.headers.get("X-Agent-Runner-Token", "")
    if not _AGENT_RUNNER_TOKEN or not secrets.compare_digest(token, _AGENT_RUNNER_TOKEN):
        raise HTTPException(401, "invalid runner token")
    agent = await _agent_runtime.load_agent(agent_id)
    if not agent:
        raise HTTPException(404, "agent not found")
    extra = getattr(agent, "extra", None) or {}
    schedule = extra.get("schedule") if isinstance(extra, dict) else {}
    if not isinstance(schedule, dict) or schedule.get("enabled") is False:
        raise HTTPException(403, "agent schedule is not enabled")
    if not (str(schedule.get("cron") or schedule.get("cron_expression") or "").strip()):
        raise HTTPException(403, "agent schedule cron is required")
    if not _agent_schedule_due(schedule):
        raise HTTPException(403, "agent schedule is not due")
    message = (body.get("message") or "").strip() or "Ejecuta tu tarea programada."
    result = await _agent_runtime.run(agent, message, history=[], user=None)
    return result


@app.post("/api/agents/{agent_id}/invoke/stream", dependencies=[Depends(require_csrf), Depends(require_admin)])
async def api_agents_invoke_stream(request: Request, agent_id: str, body: dict):
    """Server-Sent Events stream of tool_use / tool_result / text events."""
    user  = require_admin(request)
    agent = await _agent_runtime.load_agent(agent_id)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev: dict):
        await queue.put(ev)

    async def runner():
        try:
            result = await _agent_runtime.run(agent, message, history=history,
                                              user=user, on_event=on_event)
            await queue.put({"type": "done", "run_id": result.get("run_id")})
        except Exception as exc:                                # noqa: BLE001
            await queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen():
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/agents/{agent_id}/runs", dependencies=[Depends(require_admin)])
async def api_agents_runs(request: Request, agent_id: str, limit: int = 20):
    require_admin(request)
    return {"runs": await _agents.list_runs(agent_id, limit=limit)}


@app.get("/api/agent-runs/{run_id}", dependencies=[Depends(require_admin)])
async def api_agent_run_detail(request: Request, run_id: int):
    require_admin(request)
    run = await _agents.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


# ── Vault proxy ───────────────────────────────────────────────────────────────

_VAULT_URL = _vault_url()
_RAG_URL   = os.environ.get("RAG_URL",   "http://mcp-infra:8010")  # migrado

@app.get("/api/vault/connections/{cartridge}", dependencies=[Depends(require_permission("vault.connections.read"))])
async def api_vault_list_connections(cartridge: str, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge)
    try:
        async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
            r = await c.get(f"{_VAULT_URL}/connections/{cartridge}")
        if r.status_code in (404, 204):
            return {"connections": []}
        if r.status_code >= 500:
            raise HTTPException(502, "Vault request failed")
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Vault request failed") from exc
    if not data:
        return {"connections": []}
    return data

@app.get("/api/vault/connections/{cartridge}/{conn_id}/reveal", dependencies=[Depends(require_permission("vault.secrets.reveal"))])
async def api_vault_reveal_connection(cartridge: str, conn_id: str, user: dict = Depends(require_authenticated)):
    """Returns full credentials including token (not masked)."""
    _require_cartridge_visible(user, cartridge)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

@app.put("/api/vault/connections/{cartridge}/{conn_id}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def api_vault_upsert_connection(cartridge: str, conn_id: str, body: dict, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.put(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}", json=body)
        r.raise_for_status()
        return r.json()

@app.delete("/api/vault/connections/{cartridge}/{conn_id}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def api_vault_delete_connection(cartridge: str, conn_id: str, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.delete(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()


def _require_vault_scope_visible(user: dict, scope: str) -> None:
    scope = (scope or "").strip()
    if scope in {"global", "platform", "studio", "system", "_system"}:
        if not _is_global_iam_admin(user):
            raise HTTPException(403, "global vault scope requires platform admin")
        return
    _require_cartridge_visible(user, scope)


@app.get("/api/vault/secrets/{scope}", dependencies=[Depends(require_permission("vault.secrets.read_masked"))])
async def api_vault_list_secrets(scope: str, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{scope}")
        r.raise_for_status()
        return r.json()

@app.get("/api/vault/secrets/{scope}/{key}/reveal", dependencies=[Depends(require_permission("vault.secrets.reveal"))])
async def api_vault_reveal_secret(scope: str, key: str, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{scope}/{key}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

@app.put("/api/vault/secrets/{scope}/{key}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def api_vault_upsert_secret(scope: str, key: str, body: dict, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.put(f"{_VAULT_URL}/secrets/{scope}/{key}", json=body)
        r.raise_for_status()
        return r.json()

@app.delete("/api/vault/secrets/{scope}/{key}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
async def api_vault_delete_secret(scope: str, key: str, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.delete(f"{_VAULT_URL}/secrets/{scope}/{key}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()


# ── RAG proxy ─────────────────────────────────────────────────────────────────

def _rag_headers_for_user(user: dict) -> dict[str, str]:
    return {
        **_hdr_for("MCP_INFRA"),
        "x-security-context": json.dumps(build_security_context(user), ensure_ascii=False),
    }


@app.get("/api/rag/sources", dependencies=[Depends(require_authenticated)])
async def api_rag_sources(kinds: str = "", user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_rag_headers_for_user(user), timeout=10) as c:
        params = {"kinds": kinds} if kinds else None
        r = await c.get(f"{_RAG_URL}/rag/sources", params=params)
        r.raise_for_status()
        return r.json()

@app.delete("/api/rag/sources/{source_id}", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_rag_delete_source(source_id: int, user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_rag_headers_for_user(user), timeout=10) as c:
        r = await c.delete(f"{_RAG_URL}/rag/sources/{source_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Source not found")
        r.raise_for_status()
        return r.json()

@app.post("/api/rag/search", dependencies=[Depends(require_csrf), Depends(require_authenticated)])
async def api_rag_search(body: dict, user: dict = Depends(require_authenticated)):
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/mcp/invoke",
            json=_mcp_payload(
                "search_rag",
                {
                    "query": body.get("query"),
                    "top_k": body.get("top_k", 5),
                    "source_ids": body.get("source_ids"),
                    "kinds": body.get("kinds"),
                },
                user,
            ),
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG search failed"))
        return r.json().get("result") or r.json()

@app.post("/api/rag/reindex", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_rag_reindex(body: dict, user: dict = Depends(require_authenticated)):
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/reindex", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG reindex failed"))
        return r.json()

@app.post("/api/rag/ingest", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_rag_ingest(body: dict, user: dict = Depends(require_authenticated)):
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/ingest", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG ingest failed"))
        return r.json()


@app.post("/api/rag/ask", dependencies=[Depends(require_csrf), Depends(require_authenticated)])
async def api_rag_ask(body: dict, user: dict = Depends(require_authenticated)):
    """Retrieval-augmented answer: search top-K chunks, synthesize with the chat LLM."""
    from app.services import llm_client as _llm
    from google.genai import types as _gtypes

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "Missing 'query'")
    top_k       = int(body.get("top_k") or 5)
    source_ids  = body.get("source_ids") or None
    kinds       = body.get("kinds") or None

    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/mcp/invoke",
            json=_mcp_payload(
                "search_rag",
                {"query": query, "top_k": top_k, "source_ids": source_ids, "kinds": kinds},
                user,
            ),
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "RAG search failed"))
        results = ((r.json().get("result") or {}).get("results") or [])

    if not results:
        return {"answer": "No encontré información relacionada en las fuentes ingeridas.", "results": []}

    ctx_blocks = []
    for i, h in enumerate(results, start=1):
        src  = h.get("source_name") or "?"
        body_text = h.get("context") or h.get("child_content") or ""
        ctx_blocks.append(f"[{i}] Fuente: {src}\n{body_text}")
    context = "\n\n---\n\n".join(ctx_blocks)

    system = (
        "Eres un asistente que responde preguntas usando ÚNICAMENTE el contexto provisto. "
        "Si la respuesta no está en el contexto, di explícitamente que no la encuentras. "
        "Cita las fuentes usando el formato [n] al final de cada afirmación. "
        "Sé conciso y responde en el idioma de la pregunta."
    )
    user_msg = f"Contexto:\n\n{context}\n\nPregunta: {query}"

    try:
        if _llm.CHAT_PROVIDER == "gemini":
            resp = await _llm._gemini_generate_with_retry(
                model=_llm.CHAT_MODEL,
                contents=[_gtypes.Content(role="user", parts=[_gtypes.Part.from_text(text=user_msg)])],
                config=_gtypes.GenerateContentConfig(system_instruction=system),
            )
            answer = (resp.text or "").strip() or "(sin respuesta)"
        else:
            resp = await _llm._ant.messages.create(
                model=_llm.CHAT_MODEL,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            answer = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "").strip() or "(sin respuesta)"
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("LLM synthesis failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    return {"answer": answer, "results": results}


@app.get("/api/semantic", dependencies=[Depends(require_authenticated)])
async def api_semantic(cartridge: str = "replicon", user: dict = Depends(require_authenticated)):
    from app.services import cartridge_service as _cs
    _require_cartridge_visible(user, cartridge)
    manifest = await _cs.get_cartridge(cartridge)
    if manifest:
        # Pass through all entity fields so Studio can render display_name, dag_id, etc.
        entities = manifest.get("entities") or []
        return {"cartridge": cartridge, "server": manifest, "entities": entities}

    # Fallback: Pattern A — invoke via MCP server
    servers = await mcp_registry.list_servers()
    srv = next((s for s in servers if s["id"] == cartridge), None)
    if not srv:
        raise HTTPException(404, f"Cartridge '{cartridge}' not registered")
    entities = await mcp_registry.invoke(cartridge, "list_entities", {}, user=user)
    return {"cartridge": cartridge, "server": srv, "entities": entities}


# ── Data Catalog API ──────────────────────────────────────────────────────────

@app.get("/api/catalog", dependencies=[Depends(require_authenticated)])
async def api_catalog_get(
    layer: str = "",
    cartridge: str = "",
    tags: str = "",
    datasets: str = "",
    user: dict = Depends(require_authenticated),
):
    args: dict = {}
    if layer:    args["layer"]    = layer
    if cartridge: args["cartridge"] = cartridge
    if tags:     args["tags"]     = [t.strip() for t in tags.split(",") if t.strip()]
    if datasets: args["datasets"] = [d.strip() for d in datasets.split(",") if d.strip()]
    result = await _refinement_invoke("get_data_catalog", args, user=user)
    return result


@app.post("/api/catalog/entries", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_catalog_upsert(body: dict, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("upsert_catalog_entries", body, user=user)


@app.post("/api/catalog/relationships", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_catalog_relationship(body: dict, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("register_relationship", body, user=user)


def _upstream_error_detail(response, fallback: str = "Upstream request failed"):
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or fallback
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, dict):
            return detail
        if detail:
            return str(detail)
        result = payload.get("result")
        if isinstance(result, dict):
            nested = result.get("detail") or result.get("error") or result.get("message")
            if isinstance(nested, dict):
                return nested
            if nested:
                return str(nested)
    return fallback


async def _refinement_invoke(tool: str, args: dict, *, timeout: int = 30, user: dict | None = None):
    import httpx
    refinement_url = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=timeout) as client:
        r = await client.post(
            f"{refinement_url}/mcp/invoke",
            json=_mcp_payload(tool, args, user),
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _upstream_error_detail(r, "Refinement request failed"))
        return r.json()


# ── Monitoring MCP server — MCP-compatible wrapper (used by registry) ─────────

@app.get("/monitoring/mcp/tools")
async def monitoring_mcp_tools(user: dict = Depends(_internal_or_authenticated)):
    """MCP-compatible tools endpoint so the registry can discover monitoring tools.

    Sprint v1.22: added auth. Tool descriptors include parameter
    schemas — an anonymous reader could enumerate the platform's MCP
    surface and target downstream attack research at it."""
    t = await monitoring_tools()
    return t  # already returns {"tools": [...]}


@app.post("/monitoring/mcp/invoke")
async def monitoring_mcp_invoke(body: dict, user: dict = Depends(_internal_or_authenticated)):
    """MCP-compatible invoke endpoint so the assistant can call monitoring tools.

    Sprint v1.21 (F1): added require_authenticated. This route is the
    MCP entry point for the monitoring toolset (view_job, view_schema,
    etc.) and was previously reachable without a session — an
    unauthenticated caller could enumerate jobs and read schema metadata.
    The underlying monitoring_invoke() handler did not check the cookie
    on its own, so the dependency is the single chokepoint.
    """
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "monitor.read")
    return await monitoring_invoke(body, user=user)


# ── Studio-ops MCP server — cartridge & entity management tools ───────────────

STUDIO_OPS_WRITE_TOOLS = {"rename_entity", "delete_entity", "update_entity"}


def _role_name(user: dict) -> str:
    return user.get("workspace_role") or user.get("role") or ""


def _require_studio_ops_write_role(user: dict) -> None:
    if str(user.get("role") or "").lower() not in {"owner", "super_admin", ROLE_ADMIN}:
        raise HTTPException(403, "global admin role required")


@app.get("/studio_ops/mcp/tools")
async def studio_ops_tools(user: dict = Depends(_internal_or_authenticated)):
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    tools = [
        {
            "name": "rename_entity",
            "description": (
                "Rename an entity within a cartridge. "
                "Updates entity_config, entity_watermarks, pipeline_runs and silver_lineage atomically. "
                "Bronze files in MinIO keep their original path (historical data). "
                "Use this when the user asks to rename an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string", "description": "Cartridge ID, e.g. 'replicon'"},
                    "old_name":     {"type": "string", "description": "Current entity name"},
                    "new_name":     {"type": "string", "description": "New entity name"},
                },
                "required": ["cartridge_id", "old_name", "new_name"],
            },
        },
        {
            "name": "list_entities",
            "description": (
                "List all entities registered in a cartridge, including their mode, dag_id, "
                "trigger_type, and last pipeline run status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                },
                "required": ["cartridge_id"],
            },
        },
        {
            "name": "get_entity_logs",
            "description": (
                "Fetch the Airflow task logs for the most recent run of a specific entity. "
                "Use this when a DAG run failed and the user wants to diagnose the error. "
                "Returns the error message from pipeline_runs plus the full Airflow task log."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity":       {"type": "string"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "delete_entity",
            "description": (
                "Delete an entity from a cartridge. "
                "Removes it from entity_config and clears its watermarks. "
                "Pipeline run history is preserved for auditing. "
                "Use this when the user explicitly asks to delete or remove an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string", "description": "Cartridge ID, e.g. 'replicon'"},
                    "entity":       {"type": "string", "description": "Entity name to delete"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "update_entity",
            "description": (
                "Update one or more fields of an entity: display_name, mode (full|incremental), "
                "dag_id, trigger_type (manual|scheduled), cron_expression, description, enabled."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity":       {"type": "string"},
                    "display_name": {"type": "string"},
                    "mode":         {"type": "string", "enum": ["full", "incremental"]},
                    "dag_id":       {"type": "string"},
                    "connection_id": {"type": "string"},
                    "trigger_type": {"type": "string", "enum": ["manual", "scheduled"]},
                    "cron_expression": {"type": "string"},
                    "description":  {"type": "string"},
                    "enabled":      {"type": "boolean"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
    ]
    if _role_name(user) == ROLE_ANALYST:
        tools = [tool for tool in tools if tool["name"] not in STUDIO_OPS_WRITE_TOOLS]
    return {"tools": tools}


@app.post("/studio_ops/mcp/invoke", dependencies=[Depends(require_csrf)])
async def studio_ops_invoke(body: dict, user: dict = Depends(_internal_or_authenticated)):
    tool = body.get("tool")
    args = body.get("args", {})
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    cartridge_id_arg = str((args or {}).get("cartridge_id") or "").strip()
    if cartridge_id_arg:
        _require_cartridge_visible(user, cartridge_id_arg)

    if tool in STUDIO_OPS_WRITE_TOOLS:
        _require_studio_ops_write_role(user)

    if tool == "delete_entity":
        cartridge_id = args["cartridge_id"]
        entity       = args["entity"]
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])]
        if entity not in entities:
            return {"error": f"Entity '{entity}' not found in cartridge '{cartridge_id}'"}
        await cartridge_service.delete_entity(cartridge_id, entity)
        return {"deleted": True, "cartridge_id": cartridge_id, "entity": entity,
                "note": "Pipeline run history preserved. Bronze files in MinIO not removed."}

    if tool == "rename_entity":
        cartridge_id = args["cartridge_id"]
        old_name     = args["old_name"]
        new_name     = args["new_name"].strip()
        if not new_name or new_name == old_name:
            return {"renamed": False, "reason": "same name or empty"}
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])]
        if old_name not in entities:
            return {"error": f"Entity '{old_name}' not found"}
        if new_name in entities:
            return {"error": f"Entity '{new_name}' already exists"}
        await cartridge_service.rename_entity(cartridge_id, old_name, new_name)
        return {"renamed": True, "old_name": old_name, "new_name": new_name,
                "note": "Bronze files in MinIO remain at the old path — new extractions will use the new name."}

    if tool == "list_entities":
        cartridge_id = args["cartridge_id"]
        manifest     = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        runs_map = {}
        try:
            _pool = await _get_db_pool()
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 2)
            rows = await _pool.fetch(
                "SELECT DISTINCT ON (entity) entity, status, started_at, finished_at, record_count, error_message "
                f"FROM pipeline_runs WHERE cartridge_id=$1 {scope_sql} ORDER BY entity, started_at DESC",
                cartridge_id,
                *scope_values,
            )
            for r in rows:
                runs_map[r["entity"]] = {"status": r["status"],
                                         "started_at":  str(r["started_at"])[:16]  if r["started_at"]  else None,
                                         "finished_at": str(r["finished_at"])[:10] if r["finished_at"] else None,
                                         "record_count": r["record_count"],
                                         "error": r["error_message"]}
        except Exception:
            logger.debug("Could not load pipeline_runs for list_entities %s", cartridge_id, exc_info=True)
        entities = []
        for e in (manifest.get("entities") or []):
            name = e.get("entity") or e.get("id") or ""
            entities.append({
                "entity":       name,
                "display_name": e.get("display_name"),
                "mode":         e.get("mode", "full"),
                "dag_id":       e.get("dag_id"),
                "trigger_type": e.get("trigger_type", "manual"),
                "last_run":     runs_map.get(name),
            })
        return {"cartridge_id": cartridge_id, "entities": entities, "count": len(entities)}

    if tool == "get_entity_logs":
        cartridge_id = args["cartridge_id"]
        entity       = args["entity"]

        # 1. Last pipeline_run for this entity — includes airflow_dag_run_id stored by the DAG
        last_run = None
        try:
            _pool = await _get_db_pool()
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
            row  = await _pool.fetchrow(
                "SELECT dag_id, airflow_dag_run_id, status, mode, "
                "       started_at, finished_at, record_count, error_message, extra "
                "FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                f"{scope_sql} ORDER BY started_at DESC LIMIT 1",
                cartridge_id, entity, *scope_values,
            )
            if row:
                last_run = dict(row)
        except Exception:
            _eid = uuid.uuid4().hex
            logger.exception("get_entity_logs DB query failed error_id=%s", _eid)
            return {"error": f"Internal server error. error_id={_eid}"}

        if not last_run:
            return {"error": f"No pipeline runs found for {cartridge_id}/{entity}"}

        dag_id = last_run.get("dag_id") or f"{cartridge_id}_extract"

        # 2. Resolve Airflow dag_run_id — prefer the stored value, fall back to list+match
        airflow_run_id = last_run.get("airflow_dag_run_id")
        airflow_logs   = None
        try:
            if not airflow_run_id:
                # Fallback for older runs that predate the airflow_dag_run_id column:
                # match by conf.entity against recent Airflow runs
                runs_r  = await mcp_registry.invoke("infra", "airflow_list_dag_runs",
                                                    {"dag_id": dag_id, "limit": 20},
                                                    user=user)
                af_runs = (runs_r or {}).get("runs", [])
                started_str = str(last_run.get("started_at", ""))[:10]
                for run in af_runs:
                    conf_entity = (run.get("conf") or {}).get("entity", "")
                    run_date    = (run.get("start_date") or "")[:10]
                    if conf_entity == entity and run_date == started_str:
                        airflow_run_id = run["dag_run_id"]
                        break
                # Last resort: most recent run of that DAG
                if not airflow_run_id and af_runs:
                    airflow_run_id = af_runs[0]["dag_run_id"]

            if airflow_run_id:
                logs_r = await mcp_registry.invoke("infra", "airflow_get_task_logs",
                                                   {"dag_id":     dag_id,
                                                    "dag_run_id": airflow_run_id,
                                                    "task_id":    "extract"},
                                                   user=user)
                airflow_logs = (logs_r or {}).get("logs", "")
        except Exception:
            logger.debug("Airflow log fetch failed for %s/%s", cartridge_id, entity, exc_info=True)
            airflow_logs = "(No se pudieron obtener logs de Airflow)"

        return {
            "entity":        entity,
            "cartridge_id":  cartridge_id,
            "dag_id":        dag_id,
            "dag_run_id":    airflow_run_id,
            "status":        last_run["status"],
            "started_at":    str(last_run.get("started_at",""))[:19],
            "error_message": last_run.get("error_message"),
            "airflow_logs":  airflow_logs,
        }

    if tool == "update_entity":
        cartridge_id = args.pop("cartridge_id")
        entity       = args.pop("entity")
        if not args:
            return {"error": "No fields to update"}
        await cartridge_service.upsert_entity(cartridge_id, entity, **args)
        return {"updated": True, "entity": entity, **args}

    raise HTTPException(400, f"Unknown tool: {tool}")


# ── Monitoring MCP server (deeplinks para el asistente) ───────────────────────

CONSOLE_URL = _public_url("CONSOLE_URL", development_default="http://localhost:8000")

@app.get("/monitoring/tools", dependencies=[Depends(require_permission("monitor.read"))])
async def monitoring_tools(user: dict = Depends(require_permission("monitor.read"))):
    # Sprint v1.22: same rationale as /monitoring/mcp/tools — tool
    # discovery should be authenticated.
    return {"tools": [
        {
            "name": "view_job",
            "description": (
                "Genera un deeplink para visualizar el detalle de un job: "
                "status, progreso, logs linea a linea por entidad. "
                "Retorna una URL que el usuario puede abrir directamente."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "ID del job"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "view_jobs",
            "description": "Genera un deeplink para ver todos los jobs recientes con su estado.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_schema",
            "description": (
                "Genera un deeplink para visualizar el schema de una fuente Bronze: "
                "columnas, tipos, particiones disponibles y preview de filas."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string",
                               "description": "Ruta de la fuente, e.g. 'raw/replicon/TimeEntry'"},
                },
                "required": ["source"],
            },
        },
        {
            "name": "view_dataset",
            "description": (
                "Genera un deeplink para visualizar un dataset Silver/Gold: "
                "SQL, column mapping (terminos de negocio), lineage e historial y preview."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del dataset"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "view_datasets",
            "description": "Genera un deeplink para ver todos los datasets Silver/Gold definidos.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_semantic",
            "description": (
                "Genera un deeplink para visualizar el modelo semantico de un cartucho: "
                "entidades, campos, modos de extraccion, watermarks y relaciones."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge": {"type": "string", "default": "replicon"},
                },
            },
        },
        {
            "name": "view_pipeline",
            "description": (
                "Genera un deeplink para el Pipeline Monitor DAG: vista completa del flujo "
                "Entidad → Bronze → Silver → Gold con estado de frescura y botones de extracción. "
                "Úsalo cuando el usuario pregunte por el estado del pipeline, quiera ver qué "
                "está desactualizado, o quiera extraer/refrescar datos."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
    ]}


@app.post("/monitoring/invoke", dependencies=[Depends(require_csrf)])
async def monitoring_invoke(body: dict, user: dict = Depends(require_authenticated)):
    # Sprint v1.22: was reachable without any auth. monitoring tools
    # read job state and DAG metadata, which a session-less caller has
    # no business seeing. CSRF added because this is a state-shaped
    # POST and could be called from a cross-origin form otherwise.
    tool = body.get("tool")
    args = body.get("args", {})
    _require_effective_permission(user, "monitor.read")

    if tool == "view_job":
        job_id = args["job_id"]
        job = await job_service.get_scoped(job_id, user=user)
        entity = (job.get("args") or {}).get("entity", "")
        return {
            "url":     f"{CONSOLE_URL}/viewer?type=job&id={quote(str(job_id), safe='')}",
            "label":   f"Ver job {job_id}" + (f" — {entity}" if entity else ""),
            "status":  job.get("status", "unknown"),
            "message": job.get("message", ""),
        }

    if tool == "view_jobs":
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=jobs",
            "label": "Ver todos los jobs",
        }

    if tool == "view_schema":
        source = args["source"]
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=schema&source={quote(str(source), safe='')}",
            "label": f"Ver schema de {source}",
        }

    if tool == "view_dataset":
        name = args["name"]
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=dataset&name={quote(str(name), safe='')}",
            "label": f"Ver dataset {name}",
        }

    if tool == "view_datasets":
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=datasets",
            "label": "Ver todos los datasets",
        }

    if tool == "view_semantic":
        cartridge = args.get("cartridge", "replicon")
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=semantic&cartridge={quote(str(cartridge), safe='')}",
            "label": f"Ver modelo semantico de {cartridge}",
        }

    if tool == "view_pipeline":
        return {
            "url":   f"{CONSOLE_URL}/viewer?type=pipeline",
            "label": "Pipeline Monitor — Bronze → Silver → Gold",
        }

    raise HTTPException(400, f"Unknown tool: {tool}")


# ── DAG graph parser ──────────────────────────────────────────────────────────

@app.post("/api/dags/parse", dependencies=[Depends(require_csrf)])
async def api_dag_parse(body: dict, user: dict = Depends(require_authenticated)):
    # Sprint v1.22: parsing arbitrary Python source is non-trivial work
    # and an anonymous caller could DOS the parser. Auth + CSRF required.
    source = body.get("source", "")
    if not source:
        raise HTTPException(400, "source is required")
    return _parse_dag_graph(source)


def _parse_dag_graph(source: str) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"tasks": [], "edges": [], "error": str(e)}

    tasks:        list[dict] = []
    edges:        list[list] = []
    var_to_id:    dict       = {}
    fn_to_id:     dict       = {}
    output_vars:  dict       = {}
    helper_fns:   dict       = {}
    task_fn_names: set       = set()

    def is_task_deco(node):
        if isinstance(node, ast.Name):      return node.id == "task"
        if isinstance(node, ast.Attribute): return node.attr == "task"
        if isinstance(node, ast.Call):      return is_task_deco(node.func)
        return False

    def is_operator(name: str) -> bool:
        return any(name.endswith(s) for s in
                   ("Operator", "Sensor", "Hook", "Branch", "Trigger", "Task"))

    def task_id_from_call(call_node):
        for kw in call_node.keywords:
            if kw.arg == "task_id" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return None

    def fn_name_of(node):
        if isinstance(node, ast.Name):      return node.id
        if isinstance(node, ast.Attribute): return node.attr
        return None

    # ── Pass 1: classify all function defs ────────────────────────────────────
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(is_task_deco(d) for d in node.decorator_list):
            tid = node.name
            task_fn_names.add(tid)
            fn_to_id[tid]  = tid
            var_to_id[tid] = tid
            tasks.append({"id": tid, "op": "TaskFlow", "line": node.lineno, "calls": []})
        else:
            helper_fns[node.name] = node.lineno

    # ── Pass 2: classic Operators + output-var data deps ─────────────────────
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        var  = node.targets[0].id
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        fname = fn_name_of(call.func)
        if not fname:
            continue
        if is_operator(fname):
            tid = task_id_from_call(call) or var
            var_to_id[var] = tid
            tasks.append({"id": tid, "op": fname, "line": node.lineno, "calls": []})
        elif fname in fn_to_id:
            output_vars[var] = fn_to_id[fname]
            var_to_id[var] = fn_to_id[fname]

    # ── Pass 3: helper calls inside each @task body ───────────────────────────
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in task_fn_names:
            continue
        task = next((t for t in tasks if t["id"] == node.name), None)
        if not task:
            continue
        seen: set = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            cn = fn_name_of(child.func)
            if cn and cn in helper_fns and cn not in seen:
                seen.add(cn)
                task["calls"].append({"name": cn, "line": helper_fns[cn]})

    # ── Pass 4: >> chains + TaskFlow data deps ────────────────────────────────
    def resolve(node) -> list:
        if isinstance(node, ast.Name):
            t = var_to_id.get(node.id) or fn_to_id.get(node.id)
            return [t] if t else []
        if isinstance(node, ast.List):
            out = []
            for e in node.elts:
                out.extend(resolve(e))
            return out
        if isinstance(node, ast.Call):
            fname = fn_name_of(node.func)
            if fname:
                tid = var_to_id.get(fname) or fn_to_id.get(fname)
                if tid:
                    for arg in node.args:
                        if isinstance(arg, ast.Name):
                            src = output_vars.get(arg.id)
                            if src and src != tid:
                                edges.append([src, tid])
                    return [tid]
        return []

    def collect_chain(node) -> list:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift):
            left  = collect_chain(node.left)
            right = resolve(node.right)
            for f in (left[-1] if left else []):
                for t in right:
                    if f != t:
                        edges.append([f, t])
            return left + [right]
        return [resolve(node)]

    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr)
                and isinstance(node.value, ast.BinOp)
                and isinstance(node.value.op, ast.RShift)):
            collect_chain(node.value)

    # ── Pass 4c: data deps from standalone task calls (not in >> chains) ──────
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = fn_name_of(node.func)
        if not fname:
            continue
        tid = fn_to_id.get(fname) or var_to_id.get(fname)
        if not tid:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name):
                src = output_vars.get(arg.id)
                if src and src != tid:
                    edges.append([src, tid])

    # ── Deduplicate edges ─────────────────────────────────────────────────────
    seen_e: set = set()
    dedup = []
    for e in edges:
        k = f"{e[0]}→{e[1]}"
        if k not in seen_e:
            seen_e.add(k)
            dedup.append(e)

    return {"tasks": tasks, "edges": dedup}


# ── Decision Manager ─────────────────────────────────────────────────────────

import json as _json_dec
from datetime import date as _date_dec, datetime as _datetime_dec
import asyncpg as _asyncpg_dec

_DEC_POOL: _asyncpg_dec.Pool | None = None


def _coerce_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, _date_dec):
        return v
    return _date_dec.fromisoformat(str(v)[:10])


def _coerce_dt(v):
    if v is None or v == "":
        return None
    if isinstance(v, _datetime_dec):
        return v
    return _datetime_dec.fromisoformat(str(v).replace("Z", "+00:00"))


async def _dec_pool() -> _asyncpg_dec.Pool:
    global _DEC_POOL
    if _DEC_POOL is None:
        async def _init_conn(c):
            await c.set_type_codec(
                "jsonb", encoder=_json_dec.dumps, decoder=_json_dec.loads, schema="pg_catalog"
            )

        dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
        _DEC_POOL = await _asyncpg_dec.create_pool(
            dsn, min_size=1, max_size=4,
            init=_init_conn,
            command_timeout=10,
        )
    return _DEC_POOL


async def _close_dec_pool() -> None:
    global _DEC_POOL
    if _DEC_POOL is not None:
        await _DEC_POOL.close()
        _DEC_POOL = None


def _dec_row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("created_at", "closed_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    if d.get("commitment_date"):
        d["commitment_date"] = d["commitment_date"].isoformat()
    # Some asyncpg/codec combinations return jsonb as raw string; normalize.
    if isinstance(d.get("kpis"), str):
        try:
            d["kpis"] = _json_dec.loads(d["kpis"])
        except Exception:
            d["kpis"] = []
    return d


@app.get("/decisions", dependencies=[Depends(require_admin)])
async def viewer_decisions(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "decisions/index.html")


def _current_workspace_id(user: dict) -> str | None:
    """Return the active workspace UUID for ``user``, or ``None`` if the
    middleware never assigned one. Matches the workspace service's
    helper so console and workspace agree on which workspace owns a
    decision row.
    """
    return user.get("active_workspace_id") or user.get("workspace_id")


def _dec_visible_clause(uid: int, is_admin: bool, params: list, workspace_id: str | None = None) -> str:
    """Returns a SQL clause that filters decisions visible to this user.

    Sprint v1.37 (audit B7 P0): now also scopes to ``workspace_id``.
    Pre-v1.37 the clause filtered only by ``visibility`` /
    ``created_by_id`` / ``assignee_id``, so a user with membership in
    multiple workspaces saw every ``visibility='shared'`` decision in
    every workspace they had ever joined (even when their active
    session was scoped to a single one). Combined with the v1.32
    migration adding ``workspace_id`` to ``decisions``, the column
    exists; this is the code path catching up.

    Admins are still global by design, but only inside the active
    workspace — they don't get to read tenant A's decisions while their
    active session is on tenant B. If ``workspace_id`` is ``None``
    (user has no active workspace), the clause is ``FALSE`` so the
    query returns nothing rather than every row.
    """
    if not workspace_id:
        return "FALSE"
    params.append(workspace_id)
    ws_param = f"${len(params)}"
    workspace_clause = f"workspace_id = {ws_param}"
    if is_admin:
        return workspace_clause
    params.append(uid)
    p = f"${len(params)}"
    return f"({workspace_clause} AND (visibility = 'shared' OR created_by_id = {p} OR assignee_id = {p}))"


async def _dec_load_with_visibility(decision_id: int, user: dict) -> dict | None:
    # Sprint v1.37: no active workspace -> no decisions are visible.
    # Short-circuit BEFORE opening the pool so an unauthorized caller
    # never touches the DB. Pre-v1.37 the lookup proceeded with only
    # the visibility/owner filters, so a logged-in user with zero
    # memberships could still see ``visibility='shared'`` rows from
    # any tenant.
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        return None
    is_admin = user.get("role") == "admin"
    params: list = [decision_id, workspace_id]
    sql = "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2"
    if not is_admin:
        params.append(user["id"])
        sql += f" AND (visibility = 'shared' OR created_by_id = ${len(params)} OR assignee_id = ${len(params)})"
    pool = await _dec_pool()
    row = await pool.fetchrow(sql, *params)
    return dict(row) if row else None


def _dec_can_edit(row: dict, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    return row.get("created_by_id") == user["id"] or row.get("assignee_id") == user["id"]


def _dec_can_delete(row: dict, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    return row.get("created_by_id") == user["id"]


@app.get("/api/decisions")
async def api_decisions_list(status: str = "", overdue: str = "", user: dict = Depends(require_authenticated)):
    where, params = [], []
    workspace_id = _current_workspace_id(user)
    where.append(_dec_visible_clause(user["id"], user.get("role") == "admin", params, workspace_id))
    if status in ("open", "closed"):
        params.append(status)
        where.append(f"status = ${len(params)}")
    if overdue.lower() == "true":
        where.append("status = 'open' AND commitment_date IS NOT NULL AND commitment_date < CURRENT_DATE")
    sql = "SELECT * FROM decisions WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 500"
    pool = await _dec_pool()
    rows = await pool.fetch(sql, *params)
    return {"decisions": [_dec_row_to_dict(r) for r in rows]}


@app.post("/api/decisions", dependencies=[Depends(require_csrf)])
async def api_decisions_create(body: dict, user: dict = Depends(require_authenticated)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    # Sprint v1.37 (audit B7 P0): every decision belongs to the user's
    # active workspace. Without this, console.POST /api/decisions
    # silently created rows with workspace_id=NULL and the list
    # endpoint then leaked them as "shared" across tenants on the
    # legacy fallback in 33_decisions_workspace_id.sql.
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(400, "active workspace is required to create a decision")
    pool = await _dec_pool()
    row = await pool.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
           RETURNING *""",
        title,
        body.get("description") or "",
        _coerce_date(body.get("commitment_date")),
        _json_dec.dumps(body.get("kpis") or []),
        user["id"],
        body.get("assignee_id"),
        body.get("visibility") if body.get("visibility") in ("private", "shared") else "private",
        workspace_id,
    )
    return _dec_row_to_dict(row)


@app.get("/api/decisions/{decision_id}")
async def api_decisions_get(decision_id: int, user: dict = Depends(require_authenticated)):
    row = await _dec_load_with_visibility(decision_id, user)
    if not row:
        raise HTTPException(404, f"Decision {decision_id} not found")
    pool = await _dec_pool()
    actions = await pool.fetch(
        "SELECT * FROM decision_actions WHERE decision_id = $1 ORDER BY ts DESC",
        decision_id,
    )
    out = _dec_row_to_dict(row)
    out["actions"] = [
        {**dict(a), "ts": a["ts"].isoformat() if a["ts"] else None} for a in actions
    ]
    return out


@app.patch("/api/decisions/{decision_id}", dependencies=[Depends(require_csrf)])
async def api_decisions_update(decision_id: int, body: dict, user: dict = Depends(require_authenticated)):
    """Patch any subset of: title, description, commitment_date, kpis, status, outcome,
    closed_at, follow_up_decision_id, assignee_id, visibility."""
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_edit(existing, user):
        raise HTTPException(403, "you can only edit decisions you created or are assigned to")

    allowed = {
        "title", "description", "commitment_date", "kpis",
        "status", "outcome", "closed_at", "follow_up_decision_id",
        "assignee_id", "visibility",
    }
    sets, params = [], []
    for k, v in body.items():
        if k not in allowed:
            continue
        if k == "kpis":
            params.append(_json_dec.dumps(v))
            sets.append(f"{k} = ${len(params)}::jsonb")
            continue
        if k == "commitment_date":
            v = _coerce_date(v)
        elif k == "closed_at":
            v = _coerce_dt(v)
        elif k == "visibility" and v not in ("private", "shared"):
            continue
        params.append(v)
        sets.append(f"{k} = ${len(params)}")
    if not sets:
        raise HTTPException(400, "no updatable fields supplied")
    if body.get("status") == "closed" and "closed_at" not in body:
        sets.append("closed_at = COALESCE(closed_at, NOW())")
    # Sprint v1.37: pin UPDATE to (id, workspace_id) — defense-in-depth
    # against a future code path that loads ``existing`` from a
    # different source. ``existing`` already came from
    # ``_dec_load_with_visibility`` which itself filters by workspace,
    # so ``existing["workspace_id"]`` is the active workspace by
    # construction.
    params.append(decision_id)
    decision_ref = f"${len(params)}"
    params.append(existing["workspace_id"])
    workspace_ref = f"${len(params)}"
    sql = (
        f"UPDATE decisions SET {', '.join(sets)} "
        f"WHERE id = {decision_ref} AND workspace_id = {workspace_ref} RETURNING *"
    )
    pool = await _dec_pool()
    row = await pool.fetchrow(sql, *params)
    if not row:
        # The visibility check passed but the row vanished between
        # SELECT and UPDATE (e.g. a concurrent delete, or the row was
        # moved to a different workspace). Treat as not-found rather
        # than 500.
        raise HTTPException(404, f"Decision {decision_id} not found")
    return _dec_row_to_dict(row)


@app.delete("/api/decisions/{decision_id}", dependencies=[Depends(require_csrf)])
async def api_decisions_delete(decision_id: int, user: dict = Depends(require_authenticated)):
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_delete(existing, user):
        raise HTTPException(403, "only the creator or an admin can delete a decision")
    pool = await _dec_pool()
    # Sprint v1.37: pin DELETE to (id, workspace_id) — same rationale
    # as the UPDATE above. ``existing["workspace_id"]`` came from
    # ``_dec_load_with_visibility`` which is already workspace-scoped.
    await pool.execute(
        "DELETE FROM decisions WHERE id = $1 AND workspace_id = $2",
        decision_id,
        existing["workspace_id"],
    )
    return {"deleted": True, "id": decision_id}


@app.post("/api/decisions/{decision_id}/actions", dependencies=[Depends(require_csrf)])
async def api_decisions_add_action(decision_id: int, body: dict, user: dict = Depends(require_authenticated)):
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_edit(existing, user):
        raise HTTPException(403, "only creator/assignee/admin can add to bitácora")
    action_text = (body.get("action_text") or "").strip()
    if not action_text:
        raise HTTPException(400, "action_text is required")
    pool = await _dec_pool()
    actor = user.get("email") or "user"
    row = await pool.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        decision_id, action_text, body.get("note"), actor,
    )
    return {**dict(row), "ts": row["ts"].isoformat() if row["ts"] else None}


# ── Users (assignee picker, all logged-in users) ────────────────────────────

_GLOBAL_ASSIGNABLE_ROLES = {"owner", "super_admin", ROLE_ADMIN, "security_admin", "auditor"}
_WORKSPACE_ASSIGNABLE_ROLES = {"workspace_admin", "analyst", "viewer", "workspace_user", "user"}


def _assignable_role(value: str | None, actor_user: dict | None = None) -> str:
    role = (value or "user").strip()
    info = ROLE_DEFINITIONS.get(role)
    if not info or not info.get("assignable"):
        return "user"
    if _is_global_iam_admin(actor_user):
        return role
    # Workspace admins can invite/manage users inside their workspace, but
    # cannot write platform/global roles into users.role. That keeps tenant
    # IAM from becoming a hidden global privilege escalation path.
    if role in _GLOBAL_ASSIGNABLE_ROLES:
        raise HTTPException(403, "global role assignment requires platform admin")
    return role if role in _WORKSPACE_ASSIGNABLE_ROLES else "user"


@app.get("/api/users")
async def api_users_list(user: dict = Depends(require_permission("iam.users.read"))):
    users = await _auth.list_users(active_only=True)
    if _is_global_iam_admin(user):
        return {"users": users}
    visible_ids = await _visible_user_ids_for_admin(user, users)
    return {"users": [u for u in users if u.get("id") in visible_ids]}


# ── Admin user management ───────────────────────────────────────────────────

@app.get("/admin/users", dependencies=[Depends(require_admin)])
async def viewer_admin_users(request: Request, user: dict = Depends(require_permission("iam.users.read"))):
    # Compatibility URL, but not a separate users app anymore:
    # /admin/users now enters the IAM ecosystem and opens the Users tab.
    return RedirectResponse(url="/operations/users", status_code=307)


def _is_global_iam_admin(user: dict | None) -> bool:
    return (user or {}).get("role") in {"owner", "super_admin", ROLE_ADMIN}


def _session_workspace_ids(user: dict | None) -> set[str]:
    return {
        str(w.get("workspace_id"))
        for w in ((user or {}).get("workspaces") or [])
        if w.get("workspace_id")
    }


def _workspace_scope_db_unavailable(exc: BaseException) -> bool:
    message = str(exc)
    asyncpg_module = sys.modules.get("asyncpg")
    return (
        isinstance(exc, RuntimeError)
        and "DATABASE_URL is not configured" in message
    ) or (
        isinstance(exc, AttributeError)
        and "create_pool" in message
        and getattr(asyncpg_module, "__name__", "") == "stub"
    )


async def _workspace_rows_from_auth_stub(user_id: int) -> list[dict]:
    """Compatibility path for unit-test auth doubles without DATABASE_URL."""
    pool_factory = getattr(_auth, "pool", None)
    if not callable(pool_factory):
        return []
    pool = await pool_factory()
    rows = await pool.fetch(
        "SELECT workspace_id::text AS workspace_id FROM user_workspace_roles WHERE user_id = $1",
        user_id,
    )
    return [dict(row) for row in rows]


async def _target_user_workspace_ids(user_id: int) -> set[str]:
    try:
        pool = await _get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not _workspace_scope_db_unavailable(exc):
            raise
        rows = await _workspace_rows_from_auth_stub(user_id)
        return {str(row["workspace_id"]) for row in rows if row.get("workspace_id")}
    rows = await pool.fetch(
        "SELECT workspace_id::text AS workspace_id FROM user_workspace_roles WHERE user_id = $1",
        user_id,
    )
    return {str(row["workspace_id"]) for row in rows if row["workspace_id"]}


async def _visible_user_ids_for_admin(admin_user: dict, users: list[dict]) -> set[int]:
    if _is_global_iam_admin(admin_user):
        return {int(u["id"]) for u in users if u.get("id") is not None}
    workspace_ids = sorted(_session_workspace_ids(admin_user))
    if not workspace_ids:
        return set()
    try:
        pool = await _get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not _workspace_scope_db_unavailable(exc):
            raise
        visible: set[int] = set()
        admin_id = admin_user.get("id")
        for candidate in users:
            candidate_id = candidate.get("id")
            candidate_workspaces = _session_workspace_ids(candidate)
            if candidate_id == admin_id or candidate_workspaces.intersection(workspace_ids):
                visible.add(int(candidate_id))
        return visible
    rows = await pool.fetch(
        """
        SELECT DISTINCT user_id
          FROM user_workspace_roles
         WHERE workspace_id = ANY($1::uuid[])
        """,
        workspace_ids,
    )
    return {int(row["user_id"]) for row in rows}


async def _set_workspace_role_for_user(user_id: int, workspace_id: str, role: str) -> None:
    pool = await _get_db_pool()
    role_id = await pool.fetchval("SELECT id FROM roles WHERE name = $1", role)
    if not role_id:
        raise HTTPException(400, "invalid workspace role")
    await pool.execute(
        """
        INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
        VALUES ($1, $2::uuid, $3)
        ON CONFLICT (user_id, workspace_id)
        DO UPDATE SET role_id = EXCLUDED.role_id
        """,
        user_id,
        workspace_id,
        role_id,
    )


async def _assert_can_use_workspace(admin_user: dict, workspace_id: str | None) -> None:
    if _is_global_iam_admin(admin_user):
        return
    memberships = _session_workspace_ids(admin_user)
    if not workspace_id or workspace_id not in memberships:
        raise HTTPException(403, "workspace access forbidden")


async def _assert_can_manage_target_user(admin_user: dict, target_user_id: int) -> None:
    if _is_global_iam_admin(admin_user):
        return
    memberships = _session_workspace_ids(admin_user)
    if not memberships:
        raise HTTPException(403, "workspace access forbidden")
    target = await _auth.get_user_by_id(target_user_id)
    if _is_global_iam_admin(target):
        raise HTTPException(403, "workspace admins cannot manage platform admins")
    target_workspaces = await _target_user_workspace_ids(target_user_id)
    if not target_workspaces or memberships.isdisjoint(target_workspaces):
        raise HTTPException(403, "workspace access forbidden")


@app.get("/api/admin/users")
async def api_admin_users_list(admin_user: dict = Depends(require_permission("iam.users.read"))):
    users = await _auth.list_users(active_only=False)
    if _is_global_iam_admin(admin_user):
        return {"users": users}
    visible_ids = await _visible_user_ids_for_admin(admin_user, users)
    return {"users": [u for u in users if u.get("id") in visible_ids]}


@app.post("/api/admin/users", dependencies=[Depends(require_csrf)])
async def api_admin_users_create(body: dict, request: Request, admin_user: dict = Depends(require_permission("iam.users.write"))):
    email_raw = body.get("email")
    pw = body.get("password") or ""
    if not email_raw or not pw:
        raise HTTPException(400, "email and password are required")
    email = _normalize_email_or_400(email_raw)
    pw = _validate_password_or_400(pw)
    if await _auth.get_user_by_email(email):
        raise HTTPException(409, f"user with email {email} already exists")
    requested_role = _assignable_role(body.get("role"), admin_user)
    platform_role = requested_role if _is_global_iam_admin(admin_user) else "user"
    workspace_role = requested_role if not _is_global_iam_admin(admin_user) else None
    workspace_id = (
        (body.get("workspace_id") or admin_user.get("active_workspace_id") or "").strip()
        or None
    )
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")
    await _assert_can_use_workspace(admin_user, workspace_id)
    try:
        create_user_kwargs = {
            "email": email,
            "password": pw,
            "name": body.get("name"),
            "role": platform_role,
        }
        # Test doubles from older auth contracts may not expose workspace_id;
        # production auth.create_user does and assigns the membership in the
        # same transaction after the route has validated workspace scope.
        if "workspace_id" in inspect.signature(_auth.create_user).parameters:
            create_user_kwargs["workspace_id"] = workspace_id
        target_user = await _auth.create_user(**create_user_kwargs)
        if workspace_role and target_user.get("id"):
            try:
                await _set_workspace_role_for_user(int(target_user["id"]), workspace_id, workspace_role)
            except (RuntimeError, AttributeError) as exc:
                if not _workspace_scope_db_unavailable(exc):
                    raise
    except RuntimeError as exc:
        # create_user assigns workspace membership in the same transaction;
        # surface a clear 500 when the RBAC seed (workspaces/roles) is missing.
        raise HTTPException(500, str(exc)) from exc
    await _audit.record_event(
        admin_user.get("id"), admin_user.get("email"), "user.created", "user", str(target_user["id"]),
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        metadata={"role": platform_role, "workspace_role": workspace_role, "workspace_id": workspace_id},
    )
    return target_user


@app.patch("/api/admin/users/{user_id}", dependencies=[Depends(require_csrf)])
async def api_admin_users_update(user_id: int, body: dict, request: Request, admin_user: dict = Depends(require_permission("iam.users.write"))):
    # Don't let an admin demote / disable themselves accidentally
    if user_id == admin_user["id"] and (body.get("role") not in (None, admin_user.get("role")) or body.get("is_active") is False):
        raise HTTPException(400, "you cannot demote or disable your own account")
    await _assert_can_manage_target_user(admin_user, user_id)
    before = await _auth.get_user_by_id(user_id)
    password = None
    if "password" in body:
        password = _validate_password_or_400(body.get("password"))
    password_changed = password is not None
    role_update = body.get("role")
    workspace_role = None
    platform_role_update = None
    if role_update:
        requested_role = _assignable_role(role_update, admin_user)
        if _is_global_iam_admin(admin_user):
            platform_role_update = requested_role
        else:
            workspace_role = requested_role
    target_user = await _auth.update_user(
        user_id,
        name=body.get("name"),
        role=platform_role_update,
        is_active=body.get("is_active"),
        password=password,
        escalation_notify=body.get("escalation_notify") if "escalation_notify" in body else None,
    )
    if not target_user:
        raise HTTPException(404, "user not found")
    if workspace_role:
        workspace_id = str(admin_user.get("active_workspace_id") or "")
        if not workspace_id:
            raise HTTPException(400, "active workspace is required")
        try:
            await _set_workspace_role_for_user(user_id, workspace_id, workspace_role)
        except (RuntimeError, AttributeError) as exc:
            if not _workspace_scope_db_unavailable(exc):
                raise
        target_user["workspace_role"] = workspace_role
    action = "user.updated"
    if before and before.get("role") != target_user.get("role"):
        action = "user.role_changed"
    elif before and before.get("is_active") and not target_user.get("is_active"):
        action = "user.disabled"
    elif before and not before.get("is_active") and target_user.get("is_active"):
        action = "user.enabled"
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        action,
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "role": target_user.get("role"),
            "is_active": target_user.get("is_active"),
            "password_changed": password_changed,
        },
    )
    if password_changed:
        # Password change is independently auditable: an admin overriding a
        # user's credential is privileged enough to warrant its own row, even
        # when bundled with other field updates in the same request.
        await _audit.record_event(
            admin_user.get("id"),
            admin_user.get("email"),
            "user.password_changed",
            "user",
            str(user_id),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            metadata={"target_email": target_user.get("email")},
        )
    return target_user


@app.delete("/api/admin/users/{user_id}", dependencies=[Depends(require_csrf)])
async def api_admin_users_delete(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    if user_id == admin_user["id"]:
        raise HTTPException(400, "you cannot delete your own account")
    await _assert_can_manage_target_user(admin_user, user_id)
    try:
        ok = await _auth.delete_user(user_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "user not found")
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.deleted",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"deleted": True, "id": user_id}


def _vpn_configured() -> bool:
    return bool(os.environ.get("VPN_API_URL") and os.environ.get("VPN_API_PASSWORD"))


def _pack_vpn_conf(conf_text: str, email: str) -> tuple[bytes, str]:
    import io as _io
    import secrets as _secrets
    import pyzipper
    password = _secrets.token_urlsafe(9)
    buf = _io.BytesIO()
    with pyzipper.AESZipFile(
        buf,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(f"{_safe_filename(email)}.conf", conf_text)
    return buf.getvalue(), password


def _safe_filename(email: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(email or "user")).strip("._-")
    return (cleaned or "user")[:120]


async def _create_vpn_config_link(user_id: int, email: str) -> dict:
    try:
        if not _vpn_configured():
            return {"issued": False, "error": "VPN_API_URL / VPN_API_PASSWORD no configurados"}
        wg_id = await _vpn.create_client(email)
        vpn_tok, _ = await _tokens.create(user_id, "vpn", wg_client_id=wg_id)
        try:
            conf_text = await _vpn.get_config(wg_id)
        except Exception:
            conf_text = None
        return {
            "issued": True,
            "link": _vpn_link(vpn_tok),
            "wg_client_id": wg_id,
            "conf_text": conf_text,
        }
    except _vpn.VPNError as exc:
        return {"issued": False, "error": str(exc)}
    except Exception as exc:
        return {"issued": False, "error": f"unexpected: {exc}"}


async def _issue_vpn_for_user(user_id: int, email: str, name: str | None) -> dict:
    res = await _create_vpn_config_link(user_id, email)
    if not res.get("issued"):
        return res
    subject, html = _email.render_vpn_config(name, res["link"], VPN_TTL_HOURS)
    sent = await _email.send_email(email, subject, html)
    return {"issued": True, "email_sent": sent, "wg_client_id": res["wg_client_id"]}


@app.get("/vpn-config/{token}")
async def get_vpn_config(token: str, user: dict | None = Depends(current_user)):
    info = await _tokens.consume_lookup(token, "vpn")
    if not info or not info.get("wg_client_id"):
        raise HTTPException(404, "Link invalido o ya utilizado")
    try:
        cfg = await _vpn.get_config(info["wg_client_id"])
    except _vpn.VPNError as exc:
        raise HTTPException(502, f"No se pudo obtener la configuracion VPN: {exc}") from exc
    safe = _safe_filename(info.get("email") or "user")
    return Response(
        content=cfg,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{safe}.conf"'},
    )


@app.post("/api/admin/users/{user_id}/vpn-reissue", dependencies=[Depends(require_csrf)])
async def api_admin_users_vpn_reissue(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await _assert_can_manage_target_user(admin_user, user_id)
    res = await _issue_vpn_for_user(user_id, target_user["email"], target_user.get("name"))
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "vpn.reissued",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"issued": bool(res.get("issued")), "email_sent": res.get("email_sent")},
    )
    return {"reissued": res.get("issued", False), **res}


@app.post("/api/admin/users/invite", dependencies=[Depends(require_csrf)])
async def api_admin_users_invite(body: dict, request: Request, admin_user: dict = Depends(require_permission("iam.users.write"))):
    """Invite a new user by email. Creates an inactive user with no password,
    issues an invitation token, and emails the activation link."""
    email = _normalize_email_or_400(body.get("email"))
    existing = await _auth.get_user_by_email(email)
    if existing:
        raise HTTPException(409, f"user with email {email} already exists")
    role = _assignable_role(body.get("role"), admin_user)
    workspace_id = (
        (body.get("workspace_id") or admin_user.get("active_workspace_id") or "").strip()
        or None
    )
    if workspace_id:
        await _assert_can_use_workspace(admin_user, workspace_id)
    else:
        raise HTTPException(400, "workspace_id is required")
    target_user = await _auth.create_invited_user(
        email=email,
        name=body.get("name"),
        role=role,
        workspace_id=workspace_id,
    )
    tok, _ = await _tokens.create(target_user["id"], "invite")
    activation_link = _activation_link(tok)

    vpn_result: dict = {"issued": False}
    if body.get("with_vpn", True):
        vpn_result = await _create_vpn_config_link(target_user["id"], email)

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = _pack_vpn_conf(vpn_result["conf_text"], email)
        attachments.append((f"{_safe_filename(email)}.zip", zip_bytes, "application/zip"))

    if vpn_result.get("issued"):
        subject, html = _email.render_invitation_with_vpn(
            target_user.get("name"),
            email,
            activation_link,
            vpn_result["link"],
            INVITE_TTL_HOURS,
            VPN_TTL_HOURS,
            vpn_password,
        )
        sent = await _email.send_email(email, subject, html, attachments=attachments)
        vpn_result["email_sent"] = sent
    else:
        subject, html = _email.render_invitation(target_user.get("name"), email, activation_link, INVITE_TTL_HOURS)
        sent = await _email.send_email(email, subject, html)
    await _audit.record_event(
        admin_user.get("id"), admin_user.get("email"), "user.invited", "user", str(target_user["id"]),
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        metadata={"role": role, "workspace_id": workspace_id, "email_sent": sent, "vpn": vpn_result},
    )
    return {"invited": True, "user": target_user, "email_sent": sent, "vpn": vpn_result}


@app.post("/api/admin/users/{user_id}/reinvite", dependencies=[Depends(require_csrf)])
async def api_admin_users_reinvite(
    user_id: int,
    request: Request,
    body: dict | None = None,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    """Re-issue an invitation email (only for users that have not activated yet)."""
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await _assert_can_manage_target_user(admin_user, user_id)
    if target_user.get("is_active"):
        raise HTTPException(400, "user already active; use password reset instead")
    tok, _ = await _tokens.create(user_id, "invite")
    activation_link = _activation_link(tok)
    payload = body or {}
    vpn_result: dict = {"issued": False}
    if payload.get("with_vpn", True):
        vpn_result = await _create_vpn_config_link(user_id, target_user["email"])

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = _pack_vpn_conf(vpn_result["conf_text"], target_user["email"])
        attachments.append((f"{_safe_filename(target_user['email'])}.zip", zip_bytes, "application/zip"))

    if vpn_result.get("issued"):
        subject, html = _email.render_invitation_with_vpn(
            target_user.get("name"),
            target_user["email"],
            activation_link,
            vpn_result["link"],
            INVITE_TTL_HOURS,
            VPN_TTL_HOURS,
            vpn_password,
        )
        sent = await _email.send_email(target_user["email"], subject, html, attachments=attachments)
        vpn_result["email_sent"] = sent
    else:
        subject, html = _email.render_invitation(
            target_user.get("name"), target_user["email"], activation_link, INVITE_TTL_HOURS,
        )
        sent = await _email.send_email(target_user["email"], subject, html)
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.reinvited",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"email_sent": sent, "vpn": vpn_result},
    )
    return {"reinvited": True, "email_sent": sent, "vpn": vpn_result}


@app.post("/api/admin/users/{user_id}/send-reset", dependencies=[Depends(require_csrf)])
async def api_admin_users_send_reset(user_id: int, request: Request, admin: dict = Depends(require_permission("iam.users.write"))):
    """Email a password reset link to an existing active user."""
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user or not target_user.get("is_active"):
        raise HTTPException(404, "user not found or inactive")
    await _assert_can_manage_target_user(admin, user_id)
    tok, _ = await _tokens.create(user_id, "reset")
    subject, html = _email.render_password_reset(target_user.get("name"), _reset_link(tok), RESET_TTL_HOURS)
    sent = await _email.send_email(target_user["email"], subject, html)
    await _audit.record_event(
        admin.get("id"), admin.get("email"), "password_reset.sent", "user", str(user_id),
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        metadata={"email_sent": sent},
    )
    return {"sent": sent}


from app.routers import cartridges as cartridges_router
from app.routers import copilot as copilot_router
from app.routers import copilot_drafts as copilot_drafts_router       # v1.44.2 Tarea H
from app.routers import copilot_memory as copilot_memory_router       # v1.44.2 Tarea G
from app.routers import copilot_workflows as copilot_workflows_router # v1.44.2 Tarea I
from app.routers import dashboard as dashboard_router      # v1.44.1 Tarea E
from app.routers import freshness as freshness_router
from app.routers import metrics as metrics_router
from app.routers import onboarding as onboarding_router    # v1.44.1 Tarea F
from app.routers import studio as studio_router             # v1.44.3.3 Task B
from app.routers import control_room, mcp, mcp_public, operations, pages, security, settings, settings_internal

app.include_router(pages.router)
app.include_router(mcp.router)
app.include_router(mcp_public.router)
app.include_router(settings.router)
app.include_router(settings_internal.router)
app.include_router(operations.router)
app.include_router(control_room.router)
app.include_router(security.router)
app.include_router(cartridges_router.router)
app.include_router(freshness_router.router)
app.include_router(metrics_router.router)
app.include_router(copilot_router.router)
app.include_router(dashboard_router.router)               # v1.44.1 Tarea E
app.include_router(onboarding_router.router)              # v1.44.1 Tarea F
app.include_router(copilot_memory_router.router)          # v1.44.2 Tarea G
app.include_router(copilot_drafts_router.router)          # v1.44.2 Tarea H
app.include_router(copilot_workflows_router.router)       # v1.44.2 Tarea I
app.include_router(copilot_workflows_router.plural_router) # v1.44.6 Task 1 executor aliases
app.include_router(studio_router.router)                  # v1.44.3.3 Task B (stub)


# v1.42.1 auditor finding: RequestIDMiddleware must be the OUTERMOST
# wrapper so the ``X-Request-ID`` header lands on responses generated
# by inner middlewares (auth 401, CSRF 403, etc.). Registering it
# here — after every ``@app.middleware("http")`` decorator above has
# run — guarantees it ends up near the front of ``user_middleware``.
# v1.44.3.2.2 R-Mac-3 update: CORS is now registered AFTER this so
# CORS ends up STRICTLY OUTERMOST, with RequestID one layer in.
# Both invariants hold:
#   - CORS sees every request (incl. OPTIONS preflight) before any
#     inner middleware short-circuits
#   - RequestID still wraps auth_middleware so X-Request-ID lands
#     on auth 401s / CSRF 403s
app.add_middleware(RequestIDMiddleware)

# v1.44.3.2.2 R-Mac-3 (CORS ordering hotfix): CORSMiddleware MUST
# be the OUTERMOST middleware in the ASGI stack so that:
#   - OPTIONS preflight requests are intercepted + answered by
#     CORS itself BEFORE auth_middleware can return 401/405,
#   - Allow-Origin lands on EVERY response including auth 401s and
#     security_headers redirects (which is what the browser needs
#     to surface a proper CORS error vs a generic "fetch failed").
#
# Starlette builds the stack by REVERSING user_middleware, so the
# LAST registered middleware ends up OUTERMOST. This is the LAST
# add_middleware call in the module, so CORS is now outermost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # v1.44.3.2.2 R-Mac: added X-CSRF-Token. The Next.js login flow
    # (lib/auth-flow.ts) sends the double-submit-cookie value as
    # this header on POST /auth/login; without it the browser
    # preflight rejects the actual request before it leaves the
    # tab.
    # R-Mac-3 follow-up: added X-Requested-With (axios + fetch
    # default), Accept (browser default), Cookie (some browsers
    # send it on credentialed requests).
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Internal-Api-Key", "x-api-key", "x-internal-service",
        "X-CSRF-Token",
        "X-Requested-With",
        "Accept",
        "Cookie",
    ],
    # R-Mac-3: surface Set-Cookie + X-CSRF-Token through the CORS
    # response so the browser's response.cookies / header reads
    # work from the Next.js side. expose_headers is for ACTUAL
    # responses (different from allow_headers, which is for the
    # preflight Access-Control-Allow-Headers reply).
    expose_headers=["Set-Cookie", "X-CSRF-Token", "X-Request-ID"],
    # Cache preflight for 1 h so the browser doesn't re-OPTIONS
    # every single XHR.
    max_age=3600,
)
