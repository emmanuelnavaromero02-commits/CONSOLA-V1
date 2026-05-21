"""
ΩMEGA by EPIUSE — Workspace container (end-user view).

Standalone FastAPI service that hosts:
  - The consumer assistant (RAG + semantic catalog + GOLD queries)
  - The published analytic apps gallery and HTML
  - The data API consumed by those apps (proxy to refinement)
  - Decision management scoped to the connected user

Authentication: shares the `users` / `user_sessions` tables with console.
                Login itself lives in console — workspace just reads the cookie.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.services import session as _session, consumer_assistant as _ca
from app.services.rate_limiter import get_rate_limiter
from app.security import get_internal_api_key
# Sprint v1.41.1 — structured JSON logs so request_id correlates here too.
from app.logging_config import setup_logging  # noqa: E402
from app.middleware.request_id import request_id_var  # noqa: E402

setup_logging(service_name="workspace")

def _app_env() -> str:
    return os.environ.get("APP_ENV", "production").strip().lower()


def _is_production_env() -> bool:
    return _app_env() in {"production", "prod"}


def _public_url(env_name: str, development_default: str = "") -> str:
    raw = os.environ.get(env_name)
    if raw:
        return raw.rstrip("/")
    if _is_production_env():
        return ""
    return development_default.rstrip("/")


REFINEMENT_URL       = os.environ.get("REFINEMENT_URL",       "http://refinement:8500")
MCP_INFRA_URL        = os.environ.get("MCP_INFRA_URL",        "http://mcp-infra:8010")
CONSOLE_URL          = _public_url("CONSOLE_URL", "http://localhost:8000")
WORKSPACE_PUBLIC_URL = _public_url("WORKSPACE_PUBLIC_URL", "http://localhost:8001")
DATABASE_URL         = os.environ.get("DATABASE_URL", "")
DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

INTERNAL_API_KEY = get_internal_api_key()
WORKSPACE_RATE_LIMITS = {
    "/workspace/chat": (60, 60),
    "/workspace/chat/stream": (40, 60),
    "/api/data": (180, 60),
    "/api/decisions": (120, 60),
}


def _key_for(server: str) -> str:
    """Sprint v1.12: pick the per-pair INTERNAL_API_KEY_WORKSPACE_TO_<SERVER>
    secret if present, falling back to the shared legacy INTERNAL_API_KEY only
    outside production.
    ``server`` is one of ``CONSOLE`` / ``REFINEMENT`` / ``MCP_INFRA``."""
    pair = os.environ.get(f"INTERNAL_API_KEY_WORKSPACE_TO_{server}")
    if pair:
        return pair
    if _is_production_env():
        raise RuntimeError(f"Missing INTERNAL_API_KEY_WORKSPACE_TO_{server}; legacy fallback disabled in production")
    if INTERNAL_API_KEY:
        return INTERNAL_API_KEY
    raise RuntimeError(f"Missing INTERNAL_API_KEY_WORKSPACE_TO_{server} (no legacy fallback either)")


def _hdr_for(server: str) -> dict[str, str]:
    headers = {"x-api-key": _key_for(server), "x-internal-service": "workspace"}
    rid = request_id_var.get()
    if rid:
        headers["x-request-id"] = rid
    return headers


def _rate_limit_disabled() -> bool:
    return os.environ.get("RATE_LIMIT_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"} or _app_env() == "test"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _rate_limit_workspace_surface(request: Request, path: str, user: dict | None) -> None:
    if _rate_limit_disabled():
        return
    matched = None
    for prefix in WORKSPACE_RATE_LIMITS:
        if path == prefix or path.startswith(prefix + "/"):
            matched = prefix
            break
    if not matched:
        return
    limit, window = WORKSPACE_RATE_LIMITS[matched]
    ip = _client_ip(request)
    user_key = str((user or {}).get("id") or (user or {}).get("email") or "-")
    limiter = get_rate_limiter()
    for key in (f"{matched}:{ip}:{user_key}", f"{matched}:{ip}:-"):
        if not await limiter.check(key, limit, window, sensitive=True):
            raise HTTPException(status_code=429, detail="too many requests")


app = FastAPI(title="ΩMEGA by EPIUSE Workspace")

# Sprint v1.41.1 / v1.42.1 — correlation IDs. Import here but register
# at the BOTTOM of this module (after every @app.middleware decorator
# below) so the outer middleware order ends up correct — see the
# matching note in console/app/main.py for the why.
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402


def _allowed_origins() -> list[str]:
    raw_env = os.environ.get("ALLOWED_ORIGINS")
    raw = raw_env if raw_env is not None else ("" if _is_production_env() else "http://localhost:8000")
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


def _rls_user_context(user: dict | None) -> dict:
    # Forward only the fields refinement's RLS layer consumes. Avoid sending
    # the raw session dict downstream — it may carry fields we don't want the
    # internal API surface to depend on.
    #
    # Admin bypass is computed downstream from role + server-trusted context;
    # do not forward a standalone flag that a tool/request body could learn
    # to depend on.
    if not user:
        return {}
    role = user.get("role")
    return {
        "id": user.get("id"),
        "email": user.get("email", ""),
        "name": user.get("name") or user.get("email", ""),
        "role": role,
        "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
        "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        "workspace_role": user.get("workspace_role"),
        "_server_trusted_context": True,
    }


async def _assert_dataset_visible(user: dict, dataset: str) -> None:
    ws_id = user.get("active_workspace_id") or user.get("workspace_id")
    if not ws_id:
        raise HTTPException(404, f"Dataset '{dataset}' not found")
    p = await pg()
    row = await p.fetchrow(
        "SELECT name FROM datasets WHERE name = $1 AND workspace_id = $2",
        dataset, ws_id,
    )
    if not row:
        raise HTTPException(404, f"Dataset '{dataset}' not found")


def _security_context(user: dict | None) -> dict:
    ctx = _rls_user_context(user)
    if not ctx:
        return {"trusted": False, "permissions": []}
    role = ctx.get("role") or "workspace_user"
    admin = role in {"admin", "owner", "super_admin"}
    explicit_cartridges = (user or {}).get("allowed_cartridges") or (user or {}).get("cartridges") or []
    allowed_cartridges = explicit_cartridges or (["*"] if admin else [])
    if admin or "*" in allowed_cartridges:
        allowed_prefixes = ["raw/", "silver/", "gold/", "uploads/", "cartridges/"]
    else:
        allowed_prefixes = [
            f"{layer}/{str(cart).strip().strip('/')}/"
            for cart in allowed_cartridges
            for layer in ("raw", "silver", "gold", "uploads", "cartridges")
            if str(cart).strip().strip("/")
        ]
    return {
        "trusted": True,
        "source": "workspace",
        "user_id": ctx.get("id"),
        "email": ctx.get("email", ""),
        "role": role,
        "workspace_role": ctx.get("workspace_role"),
        "tenant_id": ctx.get("tenant_id"),
        "workspace_id": ctx.get("workspace_id"),
        "permissions": ["datasets.read", "apps.read", "workspace.access"],
        "allowed_cartridges": allowed_cartridges,
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": allowed_prefixes,
    }


def _mcp_payload(tool: str, args: dict, user: dict | None = None) -> dict:
    payload = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = _security_context(user)
    return payload


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Sprint v1.24 (audit B6): script-src dropped 'unsafe-inline'. Every
    # script in the workspace shell now ships as an external .js file
    # (see workspace/app/static/js/) and inline event handlers are
    # bound via addEventListener — same pattern console adopted in
    # v1.18+. style-src KEEPS 'unsafe-inline' on purpose (separate
    # refactor; the audit blocker was script-src).
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


# Sprint v1.24: published analytic apps under /apps/* are USER CONTENT
# — analysts upload self-contained HTML pages with inline <script>,
# inline <style>, and CDN libraries (chart.js etc.). Locking those to
# script-src 'self' would brick every published app instantly.
# Path-based dispatch: shell paths get strict CSP; /apps/* keeps the
# relaxed pre-v1.24 CSP. Same pattern console used in v1.11.
_APPS_WRAPPER_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "frame-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'none'"
)
_APPS_CONTENT_CSP = (
    "default-src 'none'; "
    "sandbox allow-scripts; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com https://unpkg.com; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
    "img-src data: blob:; "
    "font-src data: https://fonts.gstatic.com; "
    "connect-src 'none'; "
    "frame-ancestors 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "navigate-to 'none'"
)


def _apply_security_headers(response: Response, path: str = "") -> Response:
    # Path-based CSP dispatch. The non-CSP headers are uniform.
    headers = dict(SECURITY_HEADERS)
    if path.startswith("/apps/"):
        if path.endswith("/content"):
            headers["Content-Security-Policy"] = _APPS_CONTENT_CSP
            headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            headers["Content-Security-Policy"] = _APPS_WRAPPER_CSP
    for name, value in headers.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# ── Postgres pool (apps + sessions) ────────────────────────────────────────

_PG_POOL: asyncpg.Pool | None = None


async def pg() -> asyncpg.Pool:
    global _PG_POOL
    if _PG_POOL is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        _PG_POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    return _PG_POOL


# ── Auth middleware ────────────────────────────────────────────────────────

_PUBLIC_EXACT  = {"/healthz", "/auth/me"}
_PUBLIC_PREFIX = ("/static/",)
_API_PREFIX    = ("/api/", "/workspace/", "/auth/")


def _is_api(path: str, accept: str) -> bool:
    if any(path.startswith(p) for p in _API_PREFIX):
        return True
    return "application/json" in (accept or "")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Always resolve the session if a cookie is present so soft-auth endpoints
    # like /auth/me can introspect it.
    token = request.cookies.get(_session.COOKIE_NAME)
    user  = await _session.get_session_user(token) if token else None
    request.state.user = user

    is_public = path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIX)
    if is_public:
        return await call_next(request)

    if not user:
        if _is_api(path, request.headers.get("accept", "")):
            return _apply_security_headers(JSONResponse({"detail": "authentication required"}, status_code=401))
        # Bounce to console login with an absolute return URL pointing back to us
        return_url = f"{WORKSPACE_PUBLIC_URL}{path}"
        if request.url.query:
            return_url += "?" + request.url.query
        return _apply_security_headers(RedirectResponse(url=f"{CONSOLE_URL}/login?next={return_url}"))

    if user.get("must_change_password"):
        # Forced change runs in the console (where the form lives)
        if _is_api(path, request.headers.get("accept", "")):
            return _apply_security_headers(JSONResponse(
                {"detail": "password change required", "must_change_password": True},
                status_code=403,
            ))
        return _apply_security_headers(RedirectResponse(url=f"{CONSOLE_URL}/me"))

    try:
        await _rate_limit_workspace_surface(request, path, user)
    except HTTPException as exc:
        return _apply_security_headers(JSONResponse({"detail": exc.detail}, status_code=exc.status_code))

    return await call_next(request)


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "authentication required")
    return u


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(STATIC / "workspace.html")


@app.get("/auth/me")
async def auth_me(request: Request):
    return {"user": current_user(request)}


@app.get("/api/config")
async def api_config(request: Request):
    """Public-ish runtime config (URLs only, no secrets)."""
    return {"console_url": CONSOLE_URL}


@app.post("/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(_session.COOKIE_NAME)
    if token:
        await _session.destroy_session(token)
    resp = JSONResponse({"logged_out": True})
    resp.delete_cookie(_session.COOKIE_NAME, path="/")
    return resp


# ── Consumer chat ──────────────────────────────────────────────────────────

@app.post("/workspace/chat")
async def workspace_chat(request: Request, body: dict):
    user = require_user(request)
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not message:
        raise HTTPException(400, "message is required")
    return await _ca.chat(message, history, user=user)


@app.post("/workspace/chat/refresh-context")
async def workspace_refresh(request: Request):
    require_user(request)
    _ca.invalidate_caches()
    return {"refreshed": True}


@app.post("/workspace/chat/stream")
async def workspace_chat_stream(request: Request, body: dict):
    """SSE-style streaming chat: emits tool_use / tool_result / text / done / error
    events as the assistant runs, so the UI can show a live reasoning trail."""
    user = require_user(request)
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not message:
        raise HTTPException(400, "message is required")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            result = await _ca.chat(message, history, user=user, on_event=on_event)
            await queue.put({"type": "done", **result})
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})

    asyncio.create_task(run())

    async def event_stream():
        # Initial tick so clients can observe the connection is live.
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


# ── Apps ───────────────────────────────────────────────────────────────────

@app.get("/api/apps")
async def api_apps(request: Request):
    user = require_user(request)
    p = await pg()
    is_admin = user.get("role") == "admin"
    if is_admin:
        rows = await p.fetch(
            """SELECT name, title, description, updated_at, created_by_id, visibility
                 FROM analytic_apps ORDER BY updated_at DESC NULLS LAST"""
        )
    else:
        rows = await p.fetch(
            """SELECT name, title, description, updated_at, created_by_id, visibility
                 FROM analytic_apps
                WHERE visibility = 'shared' OR created_by_id = $1
                ORDER BY updated_at DESC NULLS LAST""",
            user["id"],
        )
    return {"apps": [
        {**dict(r), "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None}
        for r in rows
    ]}


def _datasets_from_html(html: str) -> list[str]:
    return sorted(set(re.findall(r"/api/data/([a-zA-Z_][a-zA-Z0-9_]*)", html or "")))


async def _load_visible_app(user: dict, name: str) -> dict:
    """Return app HTML after enforcing the same visibility contract for
    wrappers and sandboxed content. Published apps are user content; the
    caller must never receive the raw HTML unless they can view that app."""
    if DATASET_NAME_RE.fullmatch(name or ""):
        static_app = STATIC / "apps" / f"{name}.html"
        if static_app.is_file():
            html = static_app.read_text(encoding="utf-8")
            return {"html": html, "datasets_used": _datasets_from_html(html)}
    p = await pg()
    row = await p.fetchrow(
        """SELECT html, created_by_id, visibility, datasets_used
             FROM analytic_apps WHERE name = $1""",
        name,
    )
    if not row:
        raise HTTPException(404, f"App '{name}' not found")
    # Visibility check: shared apps are public to all logged-in users; private
    # apps are visible only to creator and admins. Returning 404 (not 403) so we
    # don't leak that the app exists.
    is_admin = user.get("role") == "admin"
    if row["visibility"] != "shared" and row["created_by_id"] != user["id"] and not is_admin:
        raise HTTPException(404, f"App '{name}' not found")
    datasets_used = row["datasets_used"] or _datasets_from_html(row["html"])
    return {"html": row["html"], "datasets_used": [str(d) for d in datasets_used]}


_APP_BRIDGE_SCRIPT = r"""
<script>
(() => {
  const pending = new Map();
  let seq = 0;
  window.fetch = function omegaSandboxFetch(input, init) {
    const rawUrl = typeof input === "string" ? input : (input && input.url);
    const opts = init || {};
    const method = String(opts.method || "GET").toUpperCase();
    let parsed;
    try { parsed = new URL(rawUrl, window.location.href); } catch (err) { return Promise.reject(err); }
    if (!parsed.pathname.startsWith("/api/data/")) {
      return Promise.reject(new Error("Published apps can only call /api/data/*"));
    }
    if (!["GET", "POST"].includes(method)) {
      return Promise.reject(new Error("Published app data bridge only allows GET/POST"));
    }
    const id = "appfetch:" + (++seq);
    const body = typeof opts.body === "string" ? opts.body : null;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      parent.postMessage({
        type: "omega-app-fetch",
        id,
        method,
        url: parsed.pathname + parsed.search,
        body
      }, "*");
      window.setTimeout(() => {
        const item = pending.get(id);
        if (!item) return;
        pending.delete(id);
        item.reject(new Error("Published app data request timed out"));
      }, 30000);
    });
  };
  window.addEventListener("message", (event) => {
    const msg = event.data || {};
    if (event.source !== window.parent) return;
    if (msg.type !== "omega-app-fetch-result" || !pending.has(msg.id)) return;
    const item = pending.get(msg.id);
    pending.delete(msg.id);
    const headers = new Headers({"Content-Type": msg.contentType || "application/json"});
    item.resolve(new Response(msg.body || "", {
      status: Number(msg.status || 500),
      statusText: msg.ok ? "OK" : "ERROR",
      headers
    }));
  });
})();
</script>
"""


def _inject_app_bridge(raw_html: str) -> str:
    match = re.search(r"<head\b[^>]*>", raw_html, flags=re.IGNORECASE)
    if match:
        return raw_html[:match.end()] + _APP_BRIDGE_SCRIPT + raw_html[match.end():]
    return _APP_BRIDGE_SCRIPT + raw_html


def _app_wrapper_html(name: str, datasets_used: list[str]) -> str:
    content_src = f"/apps/{quote(name, safe='')}/content"
    content_src_json = json.dumps(content_src)
    allowed_datasets_json = json.dumps(sorted({
        d for d in datasets_used if DATASET_NAME_RE.fullmatch(str(d))
    }))
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>App - OMEGA</title>
  <style>
    html, body {{ margin:0; min-height:100%; background:#0f1822; }}
    iframe {{ display:block; width:100vw; height:100vh; border:0; background:white; }}
  </style>
</head>
<body>
  <iframe id="omega-app-frame" title="Published app" sandbox="allow-scripts" referrerpolicy="same-origin" src={json.dumps(content_src)}></iframe>
  <script>
    (() => {{
      const frame = document.getElementById("omega-app-frame");
      const allowedSrc = {content_src_json};
      const allowedDatasets = new Set({allowed_datasets_json});
      window.addEventListener("message", async (event) => {{
        if (!frame || event.source !== frame.contentWindow) return;
        const msg = event.data || {{}};
        if (msg.type !== "omega-app-fetch" || !msg.id) return;
        let status = 500, ok = false, body = "{{}}", contentType = "application/json";
        try {{
          const url = new URL(String(msg.url || ""), window.location.origin);
          const method = String(msg.method || "GET").toUpperCase();
          if (!url.pathname.startsWith("/api/data/")) throw new Error("blocked app data URL");
          if (!["GET", "POST"].includes(method)) throw new Error("blocked app data method");
          const parts = url.pathname.split("/").filter(Boolean);
          const dataset = parts.length >= 3 && parts[0] === "api" && parts[1] === "data" ? parts[2] : "";
          if (!allowedDatasets.has(dataset)) throw new Error("dataset not declared by published app");
          const headers = {{}};
          let requestBody;
          if (method === "POST") {{
            headers["Content-Type"] = "application/json";
            requestBody = typeof msg.body === "string" ? msg.body : null;
          }}
          const response = await fetch(url.pathname + url.search, {{
            method,
            headers,
            body: requestBody,
            credentials: "same-origin"
          }});
          status = response.status;
          ok = response.ok;
          contentType = response.headers.get("content-type") || "application/json";
          body = await response.text();
        }} catch (err) {{
          status = 403;
          ok = false;
          body = JSON.stringify({{ detail: err instanceof Error ? err.message : "blocked app data request" }});
        }}
        frame.contentWindow.postMessage({{
          type: "omega-app-fetch-result",
          id: msg.id,
          ok,
          status,
          contentType,
          body
        }}, "*");
      }});
    }})();
  </script>
</body>
</html>"""


@app.get("/apps/{name}/content")
async def serve_app_content(request: Request, name: str):
    user = require_user(request)
    app_info = await _load_visible_app(user, name)
    return Response(content=_inject_app_bridge(app_info["html"]), media_type="text/html")


@app.get("/apps/{name}")
async def serve_app(request: Request, name: str):
    user = require_user(request)
    app_info = await _load_visible_app(user, name)
    return Response(content=_app_wrapper_html(name, app_info["datasets_used"]), media_type="text/html")


# ── Data API consumed by the published apps ────────────────────────────────

@app.get("/api/data/{dataset}")
async def api_data(request: Request, dataset: str, limit: int = 5000):
    user = require_user(request)
    _validate_dataset_name(dataset)
    # Sprint v1.3 RLS hardening (CRIT-4): the dataset name alone is no
    # longer sufficient — confirm the dataset is registered to this
    # caller's workspace before proxying the query downstream. Returning
    # 404 (not 403) so the response cannot be used to enumerate datasets
    # in other tenants.
    await _assert_dataset_visible(user, dataset)
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
async def api_data_options(request: Request, dataset: str, columns: str = ""):
    """Distinct values per column for filter dropdowns."""
    user = require_user(request)
    _validate_dataset_name(dataset)
    await _assert_dataset_visible(user, dataset)
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else []
    if not cols:
        raise HTTPException(400, "columns param required")
    import re as _re
    # Strict identifier regex — no spaces. Allowing whitespace lets a caller
    # smuggle `col1 UNION SELECT secrets ...` past validation since the regex
    # has no semantic understanding of SQL.
    for col in cols:
        if not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            raise HTTPException(400, f"Invalid column name: {col}")
    sqls = [
        f"SELECT DISTINCT {col} AS val, '{col}' AS col "
        f"FROM pggold.gold_{dataset} WHERE {col} IS NOT NULL"
        for col in cols
    ]
    union_sql = " UNION ALL ".join(sqls) + " ORDER BY col, val"

    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload(
                             "preview_transform",
                             {"sql": union_sql, "limit": 5000, "user_context": _rls_user_context(user)},
                             user,
                         ))
    result = r.json()
    rows = result.get("data", [])
    options: dict = {col: [] for col in cols}
    for row in rows:
        col_key = row.get("col")
        if col_key in options and row.get("val") is not None:
            options[col_key].append(str(row["val"]))
    return options


@app.post("/api/data/{dataset}/query")
async def api_data_query(request: Request, dataset: str, body: dict):
    """Filtered query against a gold dataset (mirrors console for app compat)."""
    user = require_user(request)
    _validate_dataset_name(dataset)
    await _assert_dataset_visible(user, dataset)
    import re as _re
    filters = body.get("filters", {})
    limit   = min(int(body.get("limit", 2000)), 10000)
    columns = body.get("columns", ["*"])

    safe_cols = []
    for col in columns:
        if col == "*" or _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            safe_cols.append(col)
    select_clause = ", ".join(safe_cols) if safe_cols else "*"

    if not isinstance(filters, dict):
        raise HTTPException(400, "filters must be an object")
    if len(filters) > 20:
        raise HTTPException(400, "Too many filters (max 20)")

    # Parameterised filter values — earlier code interpolated strings with
    # `'`-doubling, which breaks the moment an attacker uses backslashes or
    # newlines that DuckDB recognises in dollar-quoted contexts. Use real
    # placeholders so refinement binds the values via the driver.
    params: list = []

    def _add_param(v) -> str:
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
            fy_expr = ("(CASE WHEN EXTRACT(MONTH FROM mes)<=2 "
                       "THEN EXTRACT(YEAR FROM mes)-1 ELSE EXTRACT(YEAR FROM mes) END)")
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
                             {"sql": sql, "params": params, "limit": limit, "user_context": _rls_user_context(user)},
                             user,
                         ))
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Query failed")
    result = r.json()
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result.get("data", [])


# ── Users (assignee picker) ────────────────────────────────────────────────

@app.get("/api/users")
async def api_users_list(request: Request):
    require_user(request)
    p = await pg()
    rows = await p.fetch(
        "SELECT id, email, name, role FROM users WHERE is_active = TRUE ORDER BY email"
    )
    return {"users": [dict(r) for r in rows]}


# ── Dataset metadata for KPI editor ─────────────────────────────────────────

@app.get("/api/datasets")
async def api_datasets_list(request: Request):
    require_user(request)
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=20) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "list_datasets", "args": {}})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "datasets unavailable")
    return r.json()


@app.get("/api/datasets/{name}/schema")
async def api_dataset_schema(request: Request, name: str):
    require_user(request)
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=20) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "get_schema", "args": {"name": name}})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "schema unavailable")
    return r.json()


# ── Decisions (mirrors console, scoped to logged-in user) ───────────────────

def _coerce_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _coerce_dt(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _dec_row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("created_at", "closed_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    if d.get("commitment_date"):
        d["commitment_date"] = d["commitment_date"].isoformat()
    if isinstance(d.get("kpis"), str):
        try:    d["kpis"] = json.loads(d["kpis"])
        except Exception: d["kpis"] = []
    return d


def _dec_visible_clause(uid: int, is_admin: bool, params: list) -> str:
    if is_admin:
        return "TRUE"
    params.append(uid)
    p = f"${len(params)}"
    return f"(visibility = 'shared' OR created_by_id = {p} OR assignee_id = {p})"


def _current_workspace_id(user: dict) -> str | None:
    return user.get("active_workspace_id") or user.get("workspace_id")


async def _dec_load(decision_id: int, user: dict) -> dict | None:
    is_admin = user.get("role") == "admin"
    ws_id = _current_workspace_id(user)
    if not ws_id:
        return None
    params: list = [decision_id, ws_id]
    sql = "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2"
    if not is_admin:
        params.append(user["id"])
        sql += (f" AND (visibility = 'shared' OR created_by_id = ${len(params)} "
                f"OR assignee_id = ${len(params)})")
    p = await pg()
    row = await p.fetchrow(sql, *params)
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
async def api_decisions_list(request: Request, status: str = "", overdue: str = "",
                             scope: str = ""):
    """List visible decisions. scope=mine restricts to created_by_id=user (excludes shared)."""
    user = require_user(request)
    ws_id = _current_workspace_id(user)
    if not ws_id:
        return {"decisions": []}
    where, params = [], [ws_id]
    where.append("workspace_id = $1")
    is_admin = user.get("role") == "admin"
    if scope == "mine":
        params.append(user["id"])
        where.append(f"(created_by_id = ${len(params)} OR assignee_id = ${len(params)})")
    else:
        where.append(_dec_visible_clause(user["id"], is_admin, params))
    if status in ("open", "closed"):
        params.append(status)
        where.append(f"status = ${len(params)}")
    if overdue.lower() == "true":
        where.append("status = 'open' AND commitment_date IS NOT NULL "
                     "AND commitment_date < CURRENT_DATE")
    sql = "SELECT * FROM decisions WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 500"
    p = await pg()
    rows = await p.fetch(sql, *params)
    return {"decisions": [_dec_row_to_dict(r) for r in rows]}


@app.post("/api/decisions")
async def api_decisions_create(request: Request, body: dict):
    user = require_user(request)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    ws_id = _current_workspace_id(user)
    if not ws_id:
        raise HTTPException(400, "workspace_id is required")
    p = await pg()
    row = await p.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
           RETURNING *""",
        title,
        body.get("description") or "",
        _coerce_date(body.get("commitment_date")),
        json.dumps(body.get("kpis") or []),
        user["id"],
        body.get("assignee_id"),
        body.get("visibility") if body.get("visibility") in ("private", "shared") else "private",
        ws_id,
    )
    return _dec_row_to_dict(row)


@app.get("/api/decisions/{decision_id}")
async def api_decisions_get(request: Request, decision_id: int):
    user = require_user(request)
    row = await _dec_load(decision_id, user)
    if not row:
        raise HTTPException(404, f"Decision {decision_id} not found")
    p = await pg()
    actions = await p.fetch(
        "SELECT * FROM decision_actions WHERE decision_id = $1 ORDER BY ts DESC",
        decision_id,
    )
    out = _dec_row_to_dict(row)
    out["actions"] = [
        {**dict(a), "ts": a["ts"].isoformat() if a["ts"] else None} for a in actions
    ]
    return out


@app.patch("/api/decisions/{decision_id}")
async def api_decisions_update(request: Request, decision_id: int, body: dict):
    user = require_user(request)
    existing = await _dec_load(decision_id, user)
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
            params.append(json.dumps(v))
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
    decision_ref = f"${len(params)}"
    params.append(existing["workspace_id"])
    workspace_ref = f"${len(params)}"
    sql = (
        f"UPDATE decisions SET {', '.join(sets)} "
        f"WHERE id = {decision_ref} AND workspace_id = {workspace_ref} RETURNING *"
    )
    p = await pg()
    row = await p.fetchrow(sql, *params)
    return _dec_row_to_dict(row)


@app.delete("/api/decisions/{decision_id}")
async def api_decisions_delete(request: Request, decision_id: int):
    user = require_user(request)
    existing = await _dec_load(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_delete(existing, user):
        raise HTTPException(403, "only the creator or an admin can delete a decision")
    p = await pg()
    await p.execute(
        "DELETE FROM decisions WHERE id = $1 AND workspace_id = $2",
        decision_id,
        existing["workspace_id"],
    )
    return {"deleted": True, "id": decision_id}


@app.post("/api/decisions/{decision_id}/actions")
async def api_decisions_add_action(request: Request, decision_id: int, body: dict):
    user = require_user(request)
    existing = await _dec_load(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_edit(existing, user):
        raise HTTPException(403, "only creator/assignee/admin can add to bitácora")
    action_text = (body.get("action_text") or "").strip()
    if not action_text:
        raise HTTPException(400, "action_text is required")
    p = await pg()
    row = await p.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        decision_id, action_text, body.get("note"), user.get("email") or "user",
    )
    return {**dict(row), "ts": row["ts"].isoformat() if row["ts"] else None}


# v1.42.1 auditor finding: register RequestIDMiddleware AFTER every
# @app.middleware decorator above so it ends up as the OUTERMOST
# wrapper in the ASGI stack. Otherwise responses produced inside the
# auth middleware never reach its send-wrapper and the X-Request-ID
# header is lost.
app.add_middleware(RequestIDMiddleware)
