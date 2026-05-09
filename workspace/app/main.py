"""
MODecissionsPaaS — Workspace container (end-user view).

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

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.services import session as _session, consumer_assistant as _ca
from app.security import get_internal_api_key

REFINEMENT_URL       = os.environ.get("REFINEMENT_URL",       "http://refinement:8500")
MCP_INFRA_URL        = os.environ.get("MCP_INFRA_URL",        "http://mcp-infra:8010")
CONSOLE_URL          = os.environ.get("CONSOLE_URL",          "http://localhost:8000")
WORKSPACE_PUBLIC_URL = os.environ.get("WORKSPACE_PUBLIC_URL", "http://localhost:8001")
DATABASE_URL         = os.environ.get("DATABASE_URL", "")
DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

INTERNAL_API_KEY = get_internal_api_key()

app = FastAPI(title="MODecissionsPaaS Workspace")


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


def _apply_security_headers(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    return _apply_security_headers(response)


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


@app.get("/apps/{name}")
async def serve_app(request: Request, name: str):
    user = require_user(request)
    p = await pg()
    row = await p.fetchrow(
        """SELECT html, created_by_id, visibility
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
    return Response(content=row["html"], media_type="text/html")


# ── Data API consumed by the published apps ────────────────────────────────

@app.get("/api/data/{dataset}")
async def api_data(request: Request, dataset: str, limit: int = 5000):
    user = require_user(request)
    _validate_dataset_name(dataset)
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "workspace"}, timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "query_dataset",
                               "args": {"name": dataset, "limit": limit, "user_context": user}})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Dataset unavailable")
    data = r.json()
    return data.get("data", data)


@app.get("/api/data/{dataset}/options")
async def api_data_options(request: Request, dataset: str, columns: str = ""):
    """Distinct values per column for filter dropdowns."""
    user = require_user(request)
    _validate_dataset_name(dataset)
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else []
    if not cols:
        raise HTTPException(400, "columns param required")
    import re as _re
    for col in cols:
        if not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_ ]*$', col):
            raise HTTPException(400, f"Invalid column name: {col}")
    sqls = [
        f"SELECT DISTINCT {col} AS val, '{col}' AS col "
        f"FROM pggold.gold_{dataset} WHERE {col} IS NOT NULL"
        for col in cols
    ]
    union_sql = " UNION ALL ".join(sqls) + " ORDER BY col, val"

    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "workspace"}, timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "preview_transform",
                               "args": {"sql": union_sql, "limit": 5000, "user_context": user}})
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
    require_user(request)
    _validate_dataset_name(dataset)
    import re as _re
    filters = body.get("filters", {})
    limit   = min(int(body.get("limit", 2000)), 10000)
    columns = body.get("columns", ["*"])

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
            fy_expr = ("(CASE WHEN EXTRACT(MONTH FROM mes)<=2 "
                       "THEN EXTRACT(YEAR FROM mes)-1 ELSE EXTRACT(YEAR FROM mes) END)")
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

    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "workspace"}, timeout=60) as c:
        user = require_user(request)
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "preview_transform",
                               "args": {"sql": sql, "limit": limit, "user_context": user}})
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
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "workspace"}, timeout=20) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json={"tool": "list_datasets", "args": {}})
    if r.status_code != 200:
        raise HTTPException(r.status_code, "datasets unavailable")
    return r.json()


@app.get("/api/datasets/{name}/schema")
async def api_dataset_schema(request: Request, name: str):
    require_user(request)
    async with httpx.AsyncClient(headers={"x-api-key": INTERNAL_API_KEY, "x-internal-service": "workspace"}, timeout=20) as c:
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


async def _dec_load(decision_id: int, user: dict) -> dict | None:
    is_admin = user.get("role") == "admin"
    params: list = [decision_id]
    sql = "SELECT * FROM decisions WHERE id = $1"
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
    where, params = [], []
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
    p = await pg()
    row = await p.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
           RETURNING *""",
        title,
        body.get("description") or "",
        _coerce_date(body.get("commitment_date")),
        json.dumps(body.get("kpis") or []),
        user["id"],
        body.get("assignee_id"),
        body.get("visibility") if body.get("visibility") in ("private", "shared") else "private",
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
    sql = f"UPDATE decisions SET {', '.join(sets)} WHERE id = ${len(params)} RETURNING *"
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
    await p.execute("DELETE FROM decisions WHERE id = $1", decision_id)
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
