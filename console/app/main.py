"""
MODecissionsPaaS — Console
MCP-first: descubre y orquesta MCP servers registrados.
UI minimalista: chat con asistente + estado de servidores MCP.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

from app.services import mcp_registry, assistant, studio_assistant, token_store, job_service
from app.services import cartridge_service
from app.services import auth as _auth
from app.services import tokens as _tokens
from app.services import email_service as _email
from app.services.jwt_auth import JWTAuthError, create_access_token, decode_access_token
from app.security import get_internal_api_key
from app.dependencies import (
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_WORKSPACE_ADMIN,
    get_current_user as get_current_user_dependency,
    require_any_role,
    require_authenticated,
    require_role,
)


async def _periodic_health_check():
    """Wait for cartridges to boot, then re-check every 60 s."""
    await asyncio.sleep(12)          # grace period for sibling containers
    while True:
        try:
            await mcp_registry.health_check_all()
        except Exception:
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await mcp_registry.startup()
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
        await _close_dec_pool()



INTERNAL_API_KEY = get_internal_api_key()
# INTERNAL_API_KEY is cached at service startup. Rotating it requires restarting
# Console and peer services so all in-process values are refreshed together.

app = FastAPI(title="MODecissionsPaaS Console", lifespan=lifespan)


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000")
    return [origin.strip() for origin in raw.split(",") if origin.strip() and origin.strip() != "*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Internal-Api-Key", "x-api-key", "x-internal-service"],
)

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _validate_dataset_name(dataset: str) -> None:
    if not DATASET_NAME_RE.fullmatch(dataset or ""):
        raise HTTPException(400, "Invalid dataset name")


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
        secret_key=os.environ.get("MINIO_SECRET_KEY"),
        secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
    )


async def _count_bronze_parquet_rows(source: str, latest_date: str) -> int | None:
    bucket = os.environ.get("MINIO_BUCKET", "lakehouse")
    parquet_glob = f"s3://{bucket}/{source}/load_date={latest_date}/*.parquet"
    sql = (
        "SELECT COUNT(*) AS record_count "
        f"FROM read_parquet('{parquet_glob}', hive_partitioning=true, union_by_name=true)"
    )
    async with httpx.AsyncClient(
        headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"},
        timeout=60,
    ) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json={"tool": "preview_transform", "args": {"sql": sql, "limit": 1, "sources": [source]}},
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


async def _bronze_physical_snapshot(cartridge: str, entity: str) -> dict:
    if not _safe_pipeline_name(cartridge) or not _safe_pipeline_name(entity):
        return {}

    source = f"raw/{cartridge}/{entity}"
    bucket = os.environ.get("MINIO_BUCKET", "lakehouse")
    try:
        client = _minio_client()
        object_names = [
            obj.object_name
            for obj in client.list_objects(bucket, prefix=f"{source}/", recursive=True)
        ]
        latest_date = _bronze_latest_date_from_objects(cartridge, entity, object_names)
        if not latest_date:
            return {}
        return {
            "latest_date": latest_date,
            "record_count": await _count_bronze_parquet_rows(source, latest_date),
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


async def _record_dag_pipeline_trigger(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    mode: str,
    status: str,
    conf: dict,
) -> None:
    import asyncpg as _asyncpg

    if not dag_run_id:
        return

    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(dsn, min_size=1, max_size=2)
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
            json.dumps({"raw_conf": conf, "triggered_by": "console"}),
        )
    finally:
        if pool:
            await pool.close()


async def _refresh_dag_run_status(row: dict) -> dict:
    status = _normalize_airflow_state(row.get("status"))
    dag_id = row.get("dag_id")
    dag_run_id = row.get("airflow_dag_run_id") or row.get("run_id")
    if status not in {"queued", "running", "unknown"} or not dag_id or not dag_run_id:
        return row

    result = await mcp_registry.invoke("infra", "airflow_get_run_status", {
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
    })
    if result.get("error"):
        return row

    new_status = _normalize_airflow_state(result.get("state"))
    row["status"] = new_status
    row["started_at"] = _parse_iso_datetime(result.get("start_date")) or row.get("started_at")
    row["finished_at"] = _parse_iso_datetime(result.get("end_date")) or row.get("finished_at")
    row["duration_seconds"] = _duration_seconds(row.get("started_at"), row.get("finished_at"))

    import asyncpg as _asyncpg

    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(dsn, min_size=1, max_size=2)
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
        pass
    finally:
        if pool:
            await pool.close()
    return row


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' http://localhost:* ws://localhost:*; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
VIEWER_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' http://localhost:* ws://localhost:*; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}

RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMITS = {
    "/auth/login": (8, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/forgot-password": (5, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/reset-password": (8, RATE_LIMIT_WINDOW_SECONDS),
}
_RATE_BUCKETS: dict[tuple[str, str], list[float]] = {}
# TODO(hardening): this in-memory limiter is per process. Multi-replica
# deployments need a shared backend such as Redis to enforce global limits.


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, action: str, subject: str = "") -> None:
    limit, window = RATE_LIMITS[action]
    now = time.monotonic()
    subject_key = subject.lower().strip() or "-"
    key = (action, f"{_client_ip(request)}:{subject_key}")
    hits = [ts for ts in _RATE_BUCKETS.get(key, []) if now - ts < window]
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="too many requests")
    hits.append(now)
    _RATE_BUCKETS[key] = hits


def _is_viewer_path(path: str) -> bool:
    return path == "/viewer" or path.startswith("/viewer/")


def _apply_security_headers(response: Response, path: str = "") -> Response:
    headers = VIEWER_SECURITY_HEADERS if _is_viewer_path(path) else SECURITY_HEADERS
    if _is_viewer_path(path):
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
    for name, value in headers.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# ── Auth middleware ────────────────────────────────────────────────────────────

_AUTH_PUBLIC_EXACT = {
    "/login", "/auth/login", "/auth/logout", "/auth/me", "/auth/me-jwt", "/auth/me-current", "/auth/refresh", "/favicon.ico",
    "/activate", "/auth/activate", "/auth/activate/info",
    "/forgot-password", "/auth/forgot-password",
    "/reset-password",  "/auth/reset-password", "/auth/reset/info",
}
_AUTH_PUBLIC_PREFIX = ("/static/",)
_AUTH_API_LIKE_PREFIX = ("/api/", "/mcp/", "/datasets", "/jobs", "/tokens",
                         "/studio/", "/studio_ops/", "/monitoring/", "/auth/")

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


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    is_public = path in _AUTH_PUBLIC_EXACT or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIX)

    token = request.cookies.get(_auth.COOKIE_NAME)
    user  = await _auth.get_session_user(token) if token else None
    request.state.user = user

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
    # TODO(phase-3): legacy cookie-middleware compatibility shim. Prefer dependencies.py.
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    # TODO(phase-3): legacy cookie-middleware compatibility shim. Prefer require_authenticated.
    u = current_user(request)
    if not u:
        raise HTTPException(401, "authentication required")
    return u


def require_admin(request: Request) -> dict:
    # TODO(phase-3): legacy cookie-middleware compatibility shim. Prefer require_role(ROLE_ADMIN).
    u = require_user(request)
    if u.get("role") != "admin":
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
async def login_page():
    return FileResponse(STATIC / "login.html")


@app.post("/auth/login")
async def auth_login(request: Request, body: dict):
    email = (body.get("email") or "").strip()
    pw    = body.get("password") or ""
    _rate_limit(request, "/auth/login", email)
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
    return resp


@app.post("/auth/refresh")
async def auth_refresh(request: Request):
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    user = await _auth.get_refresh_token_user(refresh_token)
    if not user:
        resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
        resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
        return resp

    await _auth.revoke_refresh_token(refresh_token)
    new_refresh_token, refresh_expires = await _auth.create_refresh_token(user["id"])
    access_token = _access_token_for_user(user)
    resp = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    _set_refresh_cookie(resp, new_refresh_token, refresh_expires)
    return resp


@app.post("/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(_auth.COOKIE_NAME)
    if token:
        await _auth.destroy_session(token)
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    if refresh_token:
        await _auth.revoke_refresh_token(refresh_token)
    resp = JSONResponse({"logged_out": True})
    resp.delete_cookie(_auth.COOKIE_NAME, path="/")
    resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
    return resp


@app.get("/auth/me")
async def auth_me(request: Request):
    return {"user": current_user(request)}


@app.get("/auth/me-jwt")
async def auth_me_jwt(authorization: str | None = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    try:
        claims = decode_access_token(token)
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
    return {"user": user}


# ── Activation ────────────────────────────────────────────────────────────────

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")
INVITE_TTL_HOURS = int(os.environ.get("INVITE_TOKEN_TTL_HOURS", "72"))
RESET_TTL_HOURS  = int(os.environ.get("RESET_TOKEN_TTL_HOURS",  "1"))


def _activation_link(token: str) -> str:
    return f"{APP_BASE_URL}/activate?token={token}"


def _reset_link(token: str) -> str:
    return f"{APP_BASE_URL}/reset-password?token={token}"


def _set_session_cookie(resp: JSONResponse, token: str, expires) -> None:
    resp.set_cookie(
        _auth.COOKIE_NAME, token, httponly=True, samesite="lax",
        secure=_auth.cookie_secure(), expires=expires.replace(microsecond=0), path="/",
    )


@app.get("/activate")
async def viewer_activate():
    return FileResponse(STATIC / "activate.html")


@app.post("/auth/activate")
async def auth_activate(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw    = body.get("new_password") or ""
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    info = await _tokens.lookup(token, "invite")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.activate_user(info["user_id"], pw)
    if not user:
        raise HTTPException(400, "el password debe tener al menos 8 caracteres")
    await _tokens.consume(token)
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
    return FileResponse(STATIC / "forgot_password.html")


@app.post("/auth/forgot-password")
async def auth_forgot(request: Request, body: dict):
    """Generic OK regardless of whether the email exists (avoids enum oracle)."""
    email = (body.get("email") or "").strip().lower()
    _rate_limit(request, "/auth/forgot-password", email)
    if email:
        u = await _auth.get_user_by_email(email)
        if u and u.get("is_active"):
            tok, _ = await _tokens.create(u["id"], "reset")
            subject, html = _email.render_password_reset(u.get("name"), _reset_link(tok), RESET_TTL_HOURS)
            await _email.send_email(u["email"], subject, html)
    return {"sent": True}


@app.get("/reset-password")
async def viewer_reset():
    return FileResponse(STATIC / "reset_password.html")


@app.get("/auth/reset/info")
async def auth_reset_info(token: str = ""):
    info = await _tokens.lookup(token, "reset")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"]}


@app.post("/auth/reset-password")
async def auth_reset(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw    = body.get("new_password") or ""
    _rate_limit(request, "/auth/reset-password", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    info = await _tokens.lookup(token, "reset")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.reset_password_to(info["user_id"], pw)
    if not user:
        raise HTTPException(400, "el password debe tener al menos 8 caracteres")
    await _tokens.consume(token)
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"reset": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    return resp


@app.get("/api/config")
async def api_config(request: Request):
    """Runtime config (URLs only, no secrets)."""
    return {
        "workspace_url": os.environ.get("WORKSPACE_URL", "http://localhost:8001"),
        "console_url":   os.environ.get("CONSOLE_URL",   "http://localhost:8000"),
        "s3_bucket":     os.environ.get("S3_BUCKET_NAME") or os.environ.get("MINIO_BUCKET", "lakehouse"),
    }


@app.get("/me")
async def viewer_me(request: Request):
    require_user(request)
    return FileResponse(STATIC / "me.html")


@app.get("/api/me")
async def api_me(user: dict = Depends(require_authenticated)):
    return user


@app.post("/api/me/change-password")
async def api_me_change_password(body: dict, user: dict = Depends(require_authenticated)):
    current = body.get("current_password") or ""
    new     = body.get("new_password") or ""
    if not current or not new:
        raise HTTPException(400, "current_password and new_password are required")
    ok, err = await _auth.change_own_password(user["id"], current, new)
    if not ok:
        raise HTTPException(400, err or "password change failed")
    return {"changed": True}


# ── Jobs ──────────────────────────────────────────────────────────────────────

@app.get("/jobs", dependencies=[Depends(require_authenticated)])
async def list_jobs(limit: int = 20):
    return {"jobs": await job_service.list_recent(limit)}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_authenticated)])
async def get_job(job_id: str):
    return await job_service.get(job_id)


# ── Token usage ───────────────────────────────────────────────────────────────

@app.get("/tokens/summary", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def tokens_summary():
    return await token_store.summary()


# ── Assistant ─────────────────────────────────────────────────────────────────

@app.post("/assistant/chat")
async def chat(body: dict, user: dict = Depends(require_authenticated)):
    return await assistant.chat(body.get("message", ""), body.get("history", []))


# ── Datasets proxy → refinement ───────────────────────────────────────────────

@app.get("/datasets", dependencies=[Depends(require_authenticated)])
async def list_datasets():
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.get(f"{REFINEMENT_URL}/datasets")
        return r.json()

@app.get("/datasets/{name}/schema", dependencies=[Depends(require_authenticated)])
async def dataset_schema(name: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.get(f"{REFINEMENT_URL}/datasets/{name}/schema")
        return r.json()

@app.get("/datasets/{name}/data", dependencies=[Depends(require_authenticated)])
async def dataset_data(name: str, limit: int = 100):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=30) as c:
        r = await c.get(f"{REFINEMENT_URL}/datasets/{name}/data", params={"limit": limit})
        return r.json()

@app.post("/datasets/{name}/refresh", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def refresh_dataset(name: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=120) as c:
        r = await c.post(f"{REFINEMENT_URL}/datasets/{name}/refresh")
        return r.json()


# ── Viewer data APIs ──────────────────────────────────────────────────────────

@app.get("/api/jobs", dependencies=[Depends(require_authenticated)])
async def api_jobs(limit: int = 50):
    return {"jobs": await job_service.list_recent(limit)}

@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_authenticated)])
async def api_job(job_id: str):
    return await job_service.get(job_id)

@app.get("/api/jobs/{job_id}/logs", dependencies=[Depends(require_authenticated)])
async def api_job_logs(job_id: str, limit: int = 200):
    import asyncpg, os, json as _json
    dsn = os.environ.get("DATABASE_URL","").replace("postgresql+psycopg2://","postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            "SELECT entity, level, message, detail, ts FROM run_logs "
            "WHERE run_id=$1 AND cartridge='replicon' ORDER BY ts ASC LIMIT $2",
            job_id, limit
        )
    finally:
        await pool.close()
    result = []
    for row in rows:
        detail = row["detail"]
        if isinstance(detail, str):
            try: detail = _json.loads(detail)
            except: pass
        result.append({
            "ts": row["ts"].isoformat(),
            "entity": row["entity"],
            "level": row["level"],
            "message": row["message"],
            "detail": detail,
        })
    return {"logs": result}

@app.get("/api/schema", dependencies=[Depends(require_authenticated)])
async def api_schema(source: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "get_source_partitions", "args": {"source": source}})
        partitions = r.json()
        r2 = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                          json={"tool": "preview_source", "args": {"source": source, "limit": 5}})
        preview = r2.json()
    return {"partitions": partitions, "preview": preview}

@app.get("/api/sources", dependencies=[Depends(require_authenticated)])
async def api_sources():
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "list_sources", "args": {}})
    data = r.json()
    # Normalize: result may be {"result": [...]} or {"sources": [...]}
    sources = data.get("result") or data.get("sources") or []
    if isinstance(sources, list):
        return {"sources": sources}
    return {"sources": []}

@app.post("/api/datasets/save", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_dataset_save(body: dict):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "save_dataset", "args": body})
        r.raise_for_status()
    return r.json()


@app.get("/api/datasets/{name}/detail", dependencies=[Depends(require_authenticated)])
async def api_dataset_detail(name: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.get(f"{REFINEMENT_URL}/datasets/{name}/definition")
    if r.status_code == 404:
        raise HTTPException(404, f"Dataset '{name}' not found")
    return r.json()


@app.post("/api/bronze/query", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN, ROLE_ANALYST))])
async def api_bronze_query(body: dict):
    sql     = body.get("sql", "").strip()
    limit   = min(int(body.get("limit", 200)), 2000)
    sources = body.get("sources") or []
    if not sql:
        raise HTTPException(400, "sql is required")
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=120) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "preview_transform",
                               "args": {"sql": sql, "limit": limit, "sources": sources}})
    return r.json()


@app.delete("/api/datasets", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_delete_dataset(name: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "delete_dataset", "args": {"name": name}})
    if r.status_code == 404:
        raise HTTPException(404, f"Dataset '{name}' not found")
    return r.json()


@app.get("/api/datasets/{name}/lineage", dependencies=[Depends(require_authenticated)])
async def api_dataset_lineage(name: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "get_lineage", "args": {"name": name, "limit": 20}})
    return r.json()


# ── Analytic Apps ─────────────────────────────────────────────────────────────

@app.get("/apps/{name}")
async def serve_app(name: str):
    """Serve a published analytic app HTML page."""
    import asyncpg as _asyncpg
    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(dsn, min_size=1, max_size=2)
        row  = await pool.fetchrow("SELECT html FROM analytic_apps WHERE name=$1", name)
    except Exception:
        raise HTTPException(503, "Database unavailable")
    finally:
        if pool:
            await pool.close()
    if not row:
        raise HTTPException(404, f"App '{name}' not found")
    return Response(content=row["html"], media_type="text/html")


@app.get("/api/apps", dependencies=[Depends(require_authenticated)])
async def api_apps():
    """List all published analytic apps."""
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "list_apps", "args": {}})
    return r.json()


@app.delete("/api/apps/{name}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_apps_delete(name: str):
    """Delete a published analytic app by name."""
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "delete_app", "args": {"name": name}})
    payload = r.json()
    result = payload.get("result", payload)
    if not result.get("deleted"):
        raise HTTPException(404, result.get("error") or f"App '{name}' not found")
    return result


@app.get("/api/data/{dataset}", dependencies=[Depends(require_authenticated)])
async def api_data(dataset: str, limit: int = 5000):
    """Return dataset rows as JSON array for use by analytic apps."""
    _validate_dataset_name(dataset)
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "query_dataset",
                               "args": {"name": dataset, "limit": limit}})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Dataset unavailable")
    data = r.json()
    return data.get("data", data)


@app.get("/api/data/{dataset}/options", dependencies=[Depends(require_authenticated)])
async def api_data_options(dataset: str, columns: str = ""):
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

    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "preview_transform",
                               "args": {"sql": union_sql, "limit": 5000}})
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
async def api_data_query_filtered(dataset: str, body: dict):
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

    # Validate column names
    safe_cols = []
    for col in columns:
        if col == "*" or _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            safe_cols.append(col)
    select_clause = ", ".join(safe_cols) if safe_cols else "*"

    conditions = []
    for key, val in filters.items():
        if val is None or val == "" or val == []:
            continue
        if key == "fiscal_year":
            # March-February fiscal year: month<=2 belongs to previous year
            fy_expr = "(CASE WHEN EXTRACT(MONTH FROM mes)<=2 THEN EXTRACT(YEAR FROM mes)-1 ELSE EXTRACT(YEAR FROM mes) END)"
            vals = val if isinstance(val, list) else [val]
            in_clause = ",".join(str(int(v)) for v in vals)
            conditions.append(f"{fy_expr} IN ({in_clause})")
        elif _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            vals = val if isinstance(val, list) else [val]
            if len(vals) == 1:
                escaped = str(vals[0]).replace("'", "''")
                conditions.append(f"{key} = '{escaped}'")
            else:
                in_list = ",".join(f"'{str(v).replace(chr(39), chr(39)*2)}'" for v in vals)
                conditions.append(f"{key} IN ({in_list})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT {select_clause} FROM pggold.gold_{dataset} {where} LIMIT {limit}"

    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "preview_transform",
                               "args": {"sql": sql, "limit": limit}})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Query failed")
    result = r.json()
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result.get("data", [])


# ── Pipeline DAG ──────────────────────────────────────────────────────────────

@app.get("/studio/cartridges/{cartridge_id}/connections", dependencies=[Depends(require_authenticated)])
async def studio_cartridge_connections(cartridge_id: str):
    """Proxy to Vault — returns masked connection config for the cartridge."""
    vault_url = os.environ.get("VAULT_URL", "http://vault:8300")
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
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
async def api_pipeline(cartridge: str = "replicon"):
    """
    Ensambla el DAG completo: entidades × bronze status × silver datasets × gold deps.
    Fuentes: entity_config (entities), pipeline_runs + jobs (run history), refinement (datasets).
    """
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
        entities_raw = await mcp_registry.invoke(cartridge, "list_entities", {})
        if isinstance(entities_raw, dict):
            entity_list = entities_raw.get("entities", entities_raw.get("result", []))
        elif isinstance(entities_raw, list):
            entity_list = entities_raw

    # 2a. pipeline_runs — most recent run per entity (written by Airflow DAGs)
    import asyncpg as _asyncpg, os as _os
    _dsn = _os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    dag_runs_by_entity: dict[str, dict] = {}
    _pool = None
    try:
        _pool = await _asyncpg.create_pool(_dsn, min_size=1, max_size=2)
        rows_pg = await _pool.fetch(
            """SELECT DISTINCT ON (entity)
                   run_id, dag_id, entity, airflow_dag_run_id,
                   status, mode,
                   started_at, finished_at,
                   record_count, bytes_written, storage_uri,
                   duration_seconds, watermark_updated_to, error_message, extra
               FROM pipeline_runs
               WHERE cartridge_id = $1
               ORDER BY entity, started_at DESC""",
            cartridge,
        )
        for row in rows_pg:
            run = await _refresh_dag_run_status(dict(row))
            dag_runs_by_entity[row["entity"]] = run
    except Exception:
        pass
    finally:
        if _pool:
            await _pool.close()

    # 2b. jobs table — internal queue (legacy / console-triggered runs)
    all_jobs = await job_service.list_recent(100)
    jobs_by_entity: dict[str, dict] = {}
    for j in all_jobs:
        entity = (j.get("args") or {}).get("entity") or ""
        if not entity or entity in jobs_by_entity:
            continue
        jobs_by_entity[entity] = j

    # 3. Silver/Master datasets from refinement
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=15) as c:
        try:
            r = await c.get(f"{REFINEMENT_URL}/datasets")
            all_datasets: list[dict] = r.json().get("datasets", [])
        except Exception:
            all_datasets = []

    silver_ds = [d for d in all_datasets if d.get("layer") in ("silver", "master")]
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
            physical_bronze = await _bronze_physical_snapshot(cartridge, entity)
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
async def api_pipeline_runs(cartridge: str = "replicon", entity: str = None, limit: int = 50):
    """Recent DAG run history from pipeline_runs table."""
    import asyncpg as _asyncpg, os as _os
    _dsn = _os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(_dsn, min_size=1, max_size=2)
        if entity:
            rows = await pool.fetch(
                "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                "ORDER BY started_at DESC NULLS LAST LIMIT $3",
                cartridge, entity, limit,
            )
        else:
            rows = await pool.fetch(
                "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 "
                "ORDER BY started_at DESC NULLS LAST LIMIT $2",
                cartridge, limit,
            )
        return {"runs": [dict(r) for r in rows]}
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        if pool:
            await pool.close()


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
async def api_pipeline_entity_runs(cartridge: str, entity: str, limit: int = 20):
    """Recent DAG-based pipeline runs for one cartridge entity."""
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")

    safe_limit = max(1, min(int(limit or 20), 100))

    import asyncpg as _asyncpg

    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(dsn, min_size=1, max_size=2)
        rows = await pool.fetch(
            """
            SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                   started_at, finished_at, duration_seconds, error_message
              FROM pipeline_runs
             WHERE cartridge_id=$1 AND entity=$2
             ORDER BY started_at DESC NULLS LAST
             LIMIT $3
            """,
            cartridge,
            entity,
            safe_limit,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        if pool:
            await pool.close()

    runs = []
    for row in rows:
        refreshed = await _refresh_dag_run_status(dict(row))
        runs.append(_format_pipeline_entity_run(refreshed))

    return {
        "cartridge": cartridge,
        "entity": entity,
        "runs": runs,
    }


@app.get("/api/pipeline/{cartridge}/{entity}/runs/{dag_run_id}/logs", dependencies=[Depends(require_authenticated)])
async def api_pipeline_run_logs(cartridge: str, entity: str, dag_run_id: str):
    """Basic DAG run logs summary for one cartridge entity run."""
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(404, f"Entity '{entity}' not found for cartridge '{cartridge}'")

    import asyncpg as _asyncpg

    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(dsn, min_size=1, max_size=2)
        row = await pool.fetchrow(
            """
            SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                   started_at, finished_at, duration_seconds, error_message
              FROM pipeline_runs
             WHERE cartridge_id=$1
               AND entity=$2
               AND (run_id=$3 OR airflow_dag_run_id=$3)
             ORDER BY started_at DESC NULLS LAST
             LIMIT 1
            """,
            cartridge,
            entity,
            dag_run_id,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        if pool:
            await pool.close()

    if not row:
        raise HTTPException(404, f"Run '{dag_run_id}' not found for {cartridge}/{entity}")

    run = await _refresh_dag_run_status(dict(row))
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
        })
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
            })
            if log_result.get("error"):
                logs.append({"task_id": task_id, "available": False, "error": log_result["error"]})
            else:
                logs.append({"task_id": task_id, "available": True, "logs": log_result.get("logs", "")})

        response["logs"] = logs
        response["available"] = bool(tasks) and all(item.get("available") for item in logs)
        return response
    except Exception as exc:
        response["error"] = str(exc)
        return response


@app.post("/api/pipeline/{cartridge}/{entity}/extract", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_pipeline_extract(cartridge: str, entity: str, body: dict | None = None):
    """Trigger extraction for a single entity. Returns job_id for polling."""
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

        conf = _build_dag_extract_conf(entity, metadata.get("mode"), body)
        result = await _trigger_airflow_extract_dag(dag_id, conf)
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
    })
    return result


async def _pipeline_extract_metadata(cartridge: str, entity: str) -> dict:
    import asyncpg as _asyncpg
    import os as _os

    _dsn = _os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    pool = None
    try:
        pool = await _asyncpg.create_pool(_dsn, min_size=1, max_size=2)
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
    finally:
        if pool:
            await pool.close()


def _build_dag_extract_conf(entity: str, configured_mode: str | None, body: dict) -> dict:
    mode = body.get("mode") or configured_mode or "incremental"
    conf = {
        "entity": entity,
        "mode": mode,
    }
    if body.get("from_date"):
        conf["from_date"] = body["from_date"]
    if body.get("to_date"):
        conf["to_date"] = body["to_date"]
    return conf


async def _trigger_airflow_extract_dag(dag_id: str, conf: dict) -> dict:
    result: dict = {}
    for attempt in range(5):
        result = await mcp_registry.invoke("infra", "airflow_trigger_dag", {
            "dag_id": dag_id,
            "conf": conf,
        })
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

@app.post("/studio/cartridges/{cartridge_id}/entities/{entity}/rename", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def studio_rename_entity(cartridge_id: str, entity: str, body: dict):
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


@app.patch("/studio/cartridges/{cartridge_id}/entities/{entity}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def studio_update_entity(cartridge_id: str, entity: str, body: dict):
    """Update entity_config fields."""
    allowed = {"display_name", "mode", "primary_key", "dag_id",
               "trigger_type", "cron_expression", "description", "enabled"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    await cartridge_service.upsert_entity(cartridge_id, entity, **updates)
    return {"updated": True, "entity": entity, **updates}


# ── Studio — Cartridge management ────────────────────────────────────────────

@app.get("/studio/cartridges", dependencies=[Depends(require_authenticated)])
async def studio_list_cartridges():
    return {"cartridges": await cartridge_service.list_cartridges()}


@app.post("/studio/cartridges", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def studio_create_cartridge(body: dict):
    cid  = body.get("id", "").strip()
    name = body.get("name", "").strip()
    if not cid or not name:
        raise HTTPException(400, "id and name are required")
    existing = await cartridge_service.get_cartridge(cid)
    if existing:
        raise HTTPException(409, f"Cartridge '{cid}' already exists")
    manifest = await cartridge_service.create_cartridge(cid, name, body.get("description", ""))
    return manifest


@app.get("/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_authenticated)])
async def studio_get_cartridge(cartridge_id: str):
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    return manifest


@app.patch("/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def studio_update_cartridge(cartridge_id: str, body: dict):
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    return await cartridge_service.update_cartridge(cartridge_id, body)


@app.post("/studio/cartridges/{cartridge_id}/spec", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def studio_upload_spec(cartridge_id: str, file: UploadFile = File(...)):
    """Upload a spec file (OpenAPI YAML, WSDL, OData $metadata) for the cartridge."""
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    content = (await file.read()).decode("utf-8", errors="replace")
    key = cartridge_service.upload_spec(cartridge_id, file.filename or "spec.yaml", content)
    return {"uploaded": key, "filename": file.filename, "size": len(content)}


@app.get("/studio/cartridges/{cartridge_id}/export", dependencies=[Depends(require_authenticated)])
async def studio_export_cartridge(cartridge_id: str):
    """Download the cartridge as a ZIP archive."""
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    zip_bytes = await cartridge_service.export_cartridge(cartridge_id)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{cartridge_id}.zip"'},
    )


@app.post("/studio/import", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def studio_import_cartridge(file: UploadFile = File(...)):
    """Import a cartridge from a previously exported ZIP."""
    zip_bytes = await file.read()
    try:
        manifest = await cartridge_service.import_cartridge(zip_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return manifest


# ── Studio — AI assistant ─────────────────────────────────────────────────────

@app.post("/studio/chat")
async def studio_chat(body: dict, user: dict = Depends(require_authenticated)):
    cartridge_id = body.get("cartridge_id")
    manifest     = await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    return await studio_assistant.chat(
        message  = body.get("message", ""),
        history  = body.get("history", []),
        step     = body.get("step", 1),
        manifest = manifest,
        actor_role = user.get("workspace_role") or user.get("role"),
    )


@app.post("/studio/chat/stream")
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
                actor_role = user.get("workspace_role") or user.get("role"),
            )
            await queue.put({"type": "done", **result})
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})

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


@app.get("/studio")
async def studio_page():
    return FileResponse(STATIC / "studio.html")

@app.get("/viewer/pipeline")
async def viewer_pipeline():
    return FileResponse(STATIC / "viewers" / "pipeline.html")

@app.get("/viewer/vault")
async def viewer_vault():
    return FileResponse(STATIC / "viewers" / "vault.html")

@app.get("/rag")
async def rag_page():
    return FileResponse(STATIC / "rag.html")


# ── Vault proxy ───────────────────────────────────────────────────────────────

_VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8300")
_RAG_URL   = os.environ.get("RAG_URL",   "http://mcp-infra:8010")  # migrado

@app.get("/api/vault/connections/{cartridge}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_list_connections(cartridge: str):
    try:
        async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
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

@app.get("/api/vault/connections/{cartridge}/{conn_id}/reveal", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_reveal_connection(cartridge: str, conn_id: str):
    """Returns full credentials including token (not masked)."""
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

@app.put("/api/vault/connections/{cartridge}/{conn_id}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_upsert_connection(cartridge: str, conn_id: str, body: dict):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.put(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}", json=body)
        r.raise_for_status()
        return r.json()

@app.delete("/api/vault/connections/{cartridge}/{conn_id}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_delete_connection(cartridge: str, conn_id: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.delete(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

@app.get("/api/vault/secrets/{scope}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_list_secrets(scope: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{scope}")
        r.raise_for_status()
        return r.json()

@app.get("/api/vault/secrets/{scope}/{key}/reveal", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_reveal_secret(scope: str, key: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{scope}/{key}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

@app.put("/api/vault/secrets/{scope}/{key}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_upsert_secret(scope: str, key: str, body: dict):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.put(f"{_VAULT_URL}/secrets/{scope}/{key}", json=body)
        r.raise_for_status()
        return r.json()

@app.delete("/api/vault/secrets/{scope}/{key}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_vault_delete_secret(scope: str, key: str):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=5) as c:
        r = await c.delete(f"{_VAULT_URL}/secrets/{scope}/{key}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()


# ── RAG proxy ─────────────────────────────────────────────────────────────────

@app.get("/api/rag/sources", dependencies=[Depends(require_authenticated)])
async def api_rag_sources():
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.get(f"{_RAG_URL}/rag/sources")
        r.raise_for_status()
        return r.json()

@app.delete("/api/rag/sources/{source_id}", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_rag_delete_source(source_id: int):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=10) as c:
        r = await c.delete(f"{_RAG_URL}/rag/sources/{source_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Source not found")
        r.raise_for_status()
        return r.json()

@app.post("/api/rag/search", dependencies=[Depends(require_authenticated)])
async def api_rag_search(body: dict):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=60) as c:
        r = await c.post(f"{_RAG_URL}/rag/search", json=body)
        r.raise_for_status()
        return r.json()

@app.post("/api/rag/ingest", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_rag_ingest(body: dict):
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/ingest", json=body)
        r.raise_for_status()
        return r.json()


@app.post("/api/rag/ask", dependencies=[Depends(require_authenticated)])
async def api_rag_ask(body: dict):
    """Retrieval-augmented answer: search top-K chunks, synthesize with the chat LLM."""
    from app.services import llm_client as _llm
    from google.genai import types as _gtypes

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "Missing 'query'")
    top_k       = int(body.get("top_k") or 5)
    source_ids  = body.get("source_ids") or None

    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/rag/search",
            json={"query": query, "top_k": top_k, "source_ids": source_ids},
        )
        r.raise_for_status()
        results = (r.json().get("results") or [])

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
    except Exception as exc:
        raise HTTPException(500, f"LLM synthesis failed: {exc}")

    return {"answer": answer, "results": results}


@app.get("/api/semantic", dependencies=[Depends(require_authenticated)])
async def api_semantic(cartridge: str = "replicon"):
    from app.services import cartridge_service as _cs
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
    entities = await mcp_registry.invoke(cartridge, "list_entities", {})
    return {"cartridge": cartridge, "server": srv, "entities": entities}


# ── Data Catalog API ──────────────────────────────────────────────────────────

@app.get("/api/catalog", dependencies=[Depends(require_authenticated)])
async def api_catalog_get(layer: str = "", cartridge: str = "", tags: str = "", datasets: str = ""):
    args: dict = {}
    if layer:    args["layer"]    = layer
    if cartridge: args["cartridge"] = cartridge
    if tags:     args["tags"]     = [t.strip() for t in tags.split(",") if t.strip()]
    if datasets: args["datasets"] = [d.strip() for d in datasets.split(",") if d.strip()]
    result = await _refinement_invoke("get_data_catalog", args)
    return result


@app.post("/api/catalog/entries", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_catalog_upsert(body: dict):
    return await _refinement_invoke("upsert_catalog_entries", body)


@app.post("/api/catalog/relationships", dependencies=[Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
async def api_catalog_relationship(body: dict):
    return await _refinement_invoke("register_relationship", body)


async def _refinement_invoke(tool: str, args: dict):
    import httpx
    refinement_url = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "console"}, timeout=30) as client:
        r = await client.post(f"{refinement_url}/mcp/invoke",
                              json={"tool": tool, "args": args})
        r.raise_for_status()
        return r.json()


# ── Monitoring MCP server — MCP-compatible wrapper (used by registry) ─────────

@app.get("/monitoring/mcp/tools")
async def monitoring_mcp_tools():
    """MCP-compatible tools endpoint so the registry can discover monitoring tools."""
    t = await monitoring_tools()
    return t  # already returns {"tools": [...]}


@app.post("/monitoring/mcp/invoke")
async def monitoring_mcp_invoke(body: dict):
    """MCP-compatible invoke endpoint so the assistant can call monitoring tools."""
    return await monitoring_invoke(body)


# ── Studio-ops MCP server — cartridge & entity management tools ───────────────

STUDIO_OPS_WRITE_TOOLS = {"rename_entity", "delete_entity", "update_entity"}


def _role_name(user: dict) -> str:
    return user.get("workspace_role") or user.get("role") or ""


def _require_studio_ops_write_role(user: dict) -> None:
    if _role_name(user) not in {ROLE_ADMIN, ROLE_WORKSPACE_ADMIN}:
        raise HTTPException(403, "admin or workspace_admin role required")


@app.get("/studio_ops/mcp/tools")
async def studio_ops_tools(user: dict = Depends(require_authenticated)):
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


@app.post("/studio_ops/mcp/invoke")
async def studio_ops_invoke(body: dict, user: dict = Depends(require_authenticated)):
    tool = body.get("tool")
    args = body.get("args", {})

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
        import asyncpg as _asyncpg, os as _os
        _dsn = _os.environ.get("DATABASE_URL","").replace("postgresql+psycopg2://","postgresql://")
        runs_map = {}
        pool = None
        try:
            pool = await _asyncpg.create_pool(_dsn, min_size=1, max_size=2)
            rows = await pool.fetch(
                "SELECT DISTINCT ON (entity) entity, status, started_at, finished_at, record_count, error_message "
                "FROM pipeline_runs WHERE cartridge_id=$1 ORDER BY entity, started_at DESC",
                cartridge_id,
            )
            for r in rows:
                runs_map[r["entity"]] = {"status": r["status"],
                                         "started_at":  str(r["started_at"])[:16]  if r["started_at"]  else None,
                                         "finished_at": str(r["finished_at"])[:10] if r["finished_at"] else None,
                                         "record_count": r["record_count"],
                                         "error": r["error_message"]}
        except Exception:
            pass
        finally:
            if pool:
                await pool.close()
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
        import asyncpg as _asyncpg, os as _os
        _dsn = _os.environ.get("DATABASE_URL","").replace("postgresql+psycopg2://","postgresql://")
        last_run = None
        pool = None
        try:
            pool = await _asyncpg.create_pool(_dsn, min_size=1, max_size=2)
            row  = await pool.fetchrow(
                "SELECT dag_id, airflow_dag_run_id, status, mode, "
                "       started_at, finished_at, record_count, error_message, extra "
                "FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                "ORDER BY started_at DESC LIMIT 1",
                cartridge_id, entity,
            )
            if row:
                last_run = dict(row)
        except Exception as exc:
            return {"error": f"DB error: {exc}"}
        finally:
            if pool:
                await pool.close()

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
                                                    {"dag_id": dag_id, "limit": 20})
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
                                                    "task_id":    "extract"})
                airflow_logs = (logs_r or {}).get("logs", "")
        except Exception as exc:
            airflow_logs = f"(No se pudieron obtener logs de Airflow: {exc})"

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

CONSOLE_URL = os.environ.get("CONSOLE_URL", "http://localhost:8000")

@app.get("/monitoring/tools")
async def monitoring_tools():
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


@app.post("/monitoring/invoke")
async def monitoring_invoke(body: dict):
    tool = body.get("tool")
    args = body.get("args", {})

    if tool == "view_job":
        job_id = args["job_id"]
        job = await job_service.get(job_id)
        entity = (job.get("args") or {}).get("entity", "")
        return {
            "url":     f"{CONSOLE_URL}/viewer/jobs/{job_id}",
            "label":   f"Ver job {job_id}" + (f" — {entity}" if entity else ""),
            "status":  job.get("status", "unknown"),
            "message": job.get("message", ""),
        }

    if tool == "view_jobs":
        return {
            "url":   f"{CONSOLE_URL}/viewer/jobs",
            "label": "Ver todos los jobs",
        }

    if tool == "view_schema":
        source = args["source"]
        return {
            "url":   f"{CONSOLE_URL}/viewer/schema?source={source}",
            "label": f"Ver schema de {source}",
        }

    if tool == "view_dataset":
        name = args["name"]
        return {
            "url":   f"{CONSOLE_URL}/viewer/datasets/{name}",
            "label": f"Ver dataset {name}",
        }

    if tool == "view_datasets":
        return {
            "url":   f"{CONSOLE_URL}/viewer/datasets",
            "label": "Ver todos los datasets",
        }

    if tool == "view_semantic":
        cartridge = args.get("cartridge", "replicon")
        return {
            "url":   f"{CONSOLE_URL}/viewer/semantic?cartridge={cartridge}",
            "label": f"Ver modelo semantico de {cartridge}",
        }

    if tool == "view_pipeline":
        return {
            "url":   f"{CONSOLE_URL}/viewer/pipeline",
            "label": "Pipeline Monitor — Bronze → Silver → Gold",
        }

    raise HTTPException(400, f"Unknown tool: {tool}")


# ── DAG graph parser ──────────────────────────────────────────────────────────

@app.post("/api/dags/parse")
async def api_dag_parse(body: dict):
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


@app.get("/decisions")
async def viewer_decisions():
    return FileResponse(STATIC / "decisions.html")


def _dec_visible_clause(uid: int, is_admin: bool, params: list) -> str:
    """Returns a SQL clause that filters decisions visible to this user."""
    if is_admin:
        return "TRUE"
    params.append(uid)
    p = f"${len(params)}"
    return f"(visibility = 'shared' OR created_by_id = {p} OR assignee_id = {p})"


async def _dec_load_with_visibility(decision_id: int, user: dict) -> dict | None:
    pool = await _dec_pool()
    is_admin = user.get("role") == "admin"
    params: list = [decision_id]
    sql = "SELECT * FROM decisions WHERE id = $1"
    if not is_admin:
        params.append(user["id"])
        sql += f" AND (visibility = 'shared' OR created_by_id = ${len(params)} OR assignee_id = ${len(params)})"
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
    where.append(_dec_visible_clause(user["id"], user.get("role") == "admin", params))
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


@app.post("/api/decisions")
async def api_decisions_create(body: dict, user: dict = Depends(require_authenticated)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    pool = await _dec_pool()
    row = await pool.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
           RETURNING *""",
        title,
        body.get("description") or "",
        _coerce_date(body.get("commitment_date")),
        _json_dec.dumps(body.get("kpis") or []),
        user["id"],
        body.get("assignee_id"),
        body.get("visibility") if body.get("visibility") in ("private", "shared") else "private",
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


@app.patch("/api/decisions/{decision_id}")
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
    params.append(decision_id)
    sql = f"UPDATE decisions SET {', '.join(sets)} WHERE id = ${len(params)} RETURNING *"
    pool = await _dec_pool()
    row = await pool.fetchrow(sql, *params)
    return _dec_row_to_dict(row)


@app.delete("/api/decisions/{decision_id}")
async def api_decisions_delete(decision_id: int, user: dict = Depends(require_authenticated)):
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_delete(existing, user):
        raise HTTPException(403, "only the creator or an admin can delete a decision")
    pool = await _dec_pool()
    await pool.execute("DELETE FROM decisions WHERE id = $1", decision_id)
    return {"deleted": True, "id": decision_id}


@app.post("/api/decisions/{decision_id}/actions")
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

@app.get("/api/users")
async def api_users_list(user: dict = Depends(require_role(ROLE_ADMIN))):
    return {"users": await _auth.list_users(active_only=True)}


# ── Admin user management ───────────────────────────────────────────────────

@app.get("/admin/users")
async def viewer_admin_users(request: Request):
    require_admin(request)
    return FileResponse(STATIC / "admin_users.html")


@app.get("/api/admin/users")
async def api_admin_users_list(admin_user: dict = Depends(require_role(ROLE_ADMIN))):
    return {"users": await _auth.list_users(active_only=False)}


@app.post("/api/admin/users")
async def api_admin_users_create(body: dict, admin_user: dict = Depends(require_role(ROLE_ADMIN))):
    email = (body.get("email") or "").strip().lower()
    pw    = body.get("password") or ""
    if not email or not pw:
        raise HTTPException(400, "email and password are required")
    if await _auth.get_user_by_email(email):
        raise HTTPException(409, f"user with email {email} already exists")
    role = body.get("role") if body.get("role") in ("user", "admin") else "user"
    target_user = await _auth.create_user(email=email, password=pw, name=body.get("name"), role=role)
    return target_user


@app.patch("/api/admin/users/{user_id}")
async def api_admin_users_update(user_id: int, body: dict, admin_user: dict = Depends(require_role(ROLE_ADMIN))):
    # Don't let an admin demote / disable themselves accidentally
    if user_id == admin_user["id"] and (body.get("role") == "user" or body.get("is_active") is False):
        raise HTTPException(400, "you cannot demote or disable your own account")
    target_user = await _auth.update_user(
        user_id,
        name=body.get("name"),
        role=body.get("role") if body.get("role") in ("user", "admin") else None,
        is_active=body.get("is_active"),
        password=body.get("password"),
    )
    if not target_user:
        raise HTTPException(404, "user not found")
    return target_user


@app.delete("/api/admin/users/{user_id}")
async def api_admin_users_delete(user_id: int, admin_user: dict = Depends(require_role(ROLE_ADMIN))):
    if user_id == admin_user["id"]:
        raise HTTPException(400, "you cannot delete your own account")
    ok = await _auth.delete_user(user_id)
    if not ok:
        raise HTTPException(404, "user not found")
    return {"deleted": True, "id": user_id}


@app.post("/api/admin/users/invite")
async def api_admin_users_invite(body: dict, admin_user: dict = Depends(require_role(ROLE_ADMIN))):
    """Invite a new user by email. Creates an inactive user with no password,
    issues an invitation token, and emails the activation link."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email is required")
    existing = await _auth.get_user_by_email(email)
    if existing:
        raise HTTPException(409, f"user with email {email} already exists")
    role = body.get("role") if body.get("role") in ("user", "admin") else "user"
    target_user = await _auth.create_invited_user(email=email, name=body.get("name"), role=role)
    tok, _ = await _tokens.create(target_user["id"], "invite")
    subject, html = _email.render_invitation(target_user.get("name"), email, _activation_link(tok), INVITE_TTL_HOURS)
    sent = await _email.send_email(email, subject, html)
    return {"invited": True, "user": target_user, "email_sent": sent}


@app.post("/api/admin/users/{user_id}/reinvite")
async def api_admin_users_reinvite(user_id: int, admin_user: dict = Depends(require_role(ROLE_ADMIN))):
    """Re-issue an invitation email (only for users that have not activated yet)."""
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    if target_user.get("is_active"):
        raise HTTPException(400, "user already active; use password reset instead")
    tok, _ = await _tokens.create(user_id, "invite")
    subject, html = _email.render_invitation(
        target_user.get("name"), target_user["email"], _activation_link(tok), INVITE_TTL_HOURS,
    )
    sent = await _email.send_email(target_user["email"], subject, html)
    return {"reinvited": True, "email_sent": sent}


@app.post("/api/admin/users/{user_id}/send-reset")
async def api_admin_users_send_reset(user_id: int, admin: dict = Depends(require_role(ROLE_ADMIN))):
    """Email a password reset link to an existing active user."""
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user or not target_user.get("is_active"):
        raise HTTPException(404, "user not found or inactive")
    tok, _ = await _tokens.create(user_id, "reset")
    subject, html = _email.render_password_reset(target_user.get("name"), _reset_link(tok), RESET_TTL_HOURS)
    sent = await _email.send_email(target_user["email"], subject, html)
    return {"sent": sent}


from app.routers import mcp, pages, security

app.include_router(pages.router)
app.include_router(mcp.router)
app.include_router(security.router)
