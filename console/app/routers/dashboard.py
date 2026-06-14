"""Sprint v1.44.1 (Tarea E backend) — dashboard KPI aggregator.

GET /api/dashboard/kpis returns the per-tile data the dashboard
home renders. Designed to be fast (one or two short queries per
section, no JOINs across hot tables) so the 30s frontend poll
doesn't move the Postgres needle.

The endpoint is authenticated. RBAC: today every authenticated
user sees the same KPIs — the brief mentions "different fields
visible according to role (admin vs viewer)" as a future
refinement. The hooks for that are wired through the
``viewer_only`` short-circuits below; the role plumbing follows
once the dashboard ships and we know what viewers actually want
to hide.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated
from app.security import get_internal_api_key
from app.services import auth
from app.services.security_context import build_security_context


router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


# Cartridge IDs the platform ships with — matches the registry in
# console/app/services/mcp_registry.py and the router map in
# console/app/routers/cartridges.py. Adding a new cartridge means
# updating those + this list (the test guards parity).
_CARTRIDGES = ("replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors")
VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8300").rstrip("/")


def _freshness_label(age_hours: float | None) -> str:
    """Map an extraction-age in hours to a UI-facing status code."""
    if age_hours is None:
        return "never"
    if age_hours <= 24:
        return "fresh"
    if age_hours <= 72:
        return "stale"
    return "very_stale"


def _vault_headers_for_user(user: dict | None) -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_VAULT") or get_internal_api_key()
    return {
        "x-api-key": key,
        "x-internal-service": "console",
        "x-security-context": json.dumps(build_security_context(user), ensure_ascii=False),
    }


async def _vault_connections_for_cartridge(cartridge_id: str, user: dict | None) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=6.0) as client:
            response = await client.get(f"{VAULT_URL}/connections/{quote(cartridge_id, safe='')}")
    except Exception:
        return []
    if response.status_code in {404, 204} or response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    raw = payload.get("connections") if isinstance(payload, dict) else []
    if not isinstance(raw, list):
        return []
    return [conn for conn in raw if isinstance(conn, dict) and str(conn.get("conn_id") or conn.get("id") or "").strip()]


async def _active_scoped_cartridges(user: dict | None) -> tuple[str, ...]:
    active: list[str] = []
    for cartridge_id in _CARTRIDGES:
        if await _vault_connections_for_cartridge(cartridge_id, user):
            active.append(cartridge_id)
    return tuple(active)


async def _cartridge_counts(pool, active_cartridges: tuple[str, ...]) -> dict:
    """Count only cartridges with an active scoped Vault connection."""
    if not active_cartridges:
        return {"total": 0, "connected": 0, "disconnected": 0}
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE category = 'cartridge' AND id = ANY($1::text[])) AS connected
        FROM mcp_servers
        """,
        list(active_cartridges),
    ) or {}
    connected = int(row.get("connected") or 0)
    return {
        "total": len(active_cartridges),
        "connected": connected,
        "disconnected": max(0, len(active_cartridges) - connected),
    }


def _run_scope(is_global: bool, workspace_id: str | None, idx: int) -> tuple[str, list]:
    """Tenant scoping for run queries against pipeline_runs. A global admin
    sees every run; a scoped user sees only runs tagged with their workspace.
    Returns the extra WHERE fragment and its params (param ``$idx``)."""
    if is_global or not workspace_id:
        return "", []
    return f" AND workspace_id = ${idx}::uuid", [workspace_id]


async def _extraction_counts(
    pool,
    active_cartridges: tuple[str, ...],
    workspace_id: str | None = None,
    is_global: bool = True,
) -> dict:
    """Extraction runs today + last 7 days, from pipeline_runs (the scoped
    run table). Scoped per workspace for non-global users so two tenants
    sharing a cartridge never see each other's runs."""
    if not active_cartridges:
        return {
            "today": 0,
            "week": 0,
            "productive_failures_today": 0,
            "unscope_noise_failures_today": 0,
        }
    scope, sp = _run_scope(is_global, workspace_id, 2)
    today = await pool.fetchval(
        f"""
        SELECT COUNT(*) FROM pipeline_runs
         WHERE started_at >= date_trunc('day', NOW())
           AND cartridge_id = ANY($1::text[]){scope}
        """,
        list(active_cartridges), *sp,
    )
    week = await pool.fetchval(
        f"""
        SELECT COUNT(*) FROM pipeline_runs
         WHERE started_at >= NOW() - INTERVAL '7 days'
           AND cartridge_id = ANY($1::text[]){scope}
        """,
        list(active_cartridges), *sp,
    )
    productive_failures = await pool.fetchval(
        f"""
        SELECT COUNT(*) FROM pipeline_runs
         WHERE started_at >= date_trunc('day', NOW())
           AND status = 'failed'
           AND cartridge_id = ANY($1::text[]){scope}
        """,
        list(active_cartridges), *sp,
    )
    return {
        "today": int(today or 0),
        "week":  int(week or 0),
        "productive_failures_today": int(productive_failures or 0),
        "unscope_noise_failures_today": 0,
    }


async def _freshness_per_cartridge(
    pool,
    active_cartridges: tuple[str, ...],
    workspace_id: str | None = None,
    is_global: bool = True,
) -> dict:
    """Hours since the latest successful extraction per cartridge, scoped to
    the workspace for non-global users (from pipeline_runs)."""
    if not active_cartridges:
        return {}
    scope, sp = _run_scope(is_global, workspace_id, 2)
    rows = await pool.fetch(
        f"""
        SELECT cartridge_id,
               EXTRACT(EPOCH FROM (NOW() - MAX(finished_at))) / 3600.0 AS age_hours
          FROM pipeline_runs
         WHERE status = 'success'
           AND cartridge_id = ANY($1::text[]){scope}
         GROUP BY cartridge_id
        """,
        list(active_cartridges), *sp,
    )
    by_id: dict = {}
    for row in rows:
        cid = row["cartridge_id"]
        age = float(row["age_hours"]) if row["age_hours"] is not None else None
        by_id[cid] = age
    out: dict = {}
    for cart in active_cartridges:
        age = by_id.get(cart)
        out[cart] = {
            "age_hours": age,
            "status":    _freshness_label(age),
        }
    return out


async def _user_counts(pool, user: dict | None = None) -> dict:
    """Daily-active = distinct emails with a successful login today.

    Scoped per tenant: a global admin sees platform-wide counts; any scoped
    role sees only users that belong to a workspace in their own tenant — a
    tenant_admin must not see other tenants' user totals.

    The login_attempts table is authoritative for "did user X log in on date Y"
    (login_security migration v1.32).
    """
    role = str((user or {}).get("role") or "").lower()
    is_global = role in {"owner", "super_admin", "admin"}
    tenant_id = str((user or {}).get("active_tenant_id") or (user or {}).get("tenant_id") or "").strip()

    if is_global or not tenant_id:
        active_today = await pool.fetchval(
            """
            SELECT COUNT(DISTINCT email)
              FROM login_attempts
             WHERE success = TRUE
               AND created_at >= date_trunc('day', NOW())
            """
        )
        total = await pool.fetchval("SELECT COUNT(*) FROM users")
    else:
        active_today = await pool.fetchval(
            """
            SELECT COUNT(DISTINCT la.email)
              FROM login_attempts la
              JOIN users u ON u.email = la.email
              JOIN user_workspace_roles uwr ON uwr.user_id = u.id
              JOIN workspaces w ON w.id = uwr.workspace_id
             WHERE la.success = TRUE
               AND la.created_at >= date_trunc('day', NOW())
               AND w.tenant_id = $1::uuid
            """,
            tenant_id,
        )
        total = await pool.fetchval(
            """
            SELECT COUNT(DISTINCT u.id)
              FROM users u
              JOIN user_workspace_roles uwr ON uwr.user_id = u.id
              JOIN workspaces w ON w.id = uwr.workspace_id
             WHERE w.tenant_id = $1::uuid
            """,
            tenant_id,
        )
    return {
        "active_today": int(active_today or 0),
        "total":        int(total or 0),
    }


async def _copilot_counts(pool, user: dict | None = None) -> dict:
    """Copilot activity today — conversations started + tool
    invocations.

    Table is ``conversations`` (created by migration 38) — NOT
    ``copilot_conversations``. The to_regclass guard handles
    pre-v1.42 DBs where the table doesn't exist yet (returns NULL
    → counts default to 0).
    """
    role = str((user or {}).get("role") or "").lower()
    is_global = role in {"owner", "super_admin", "admin"}
    tenant_id = str((user or {}).get("active_tenant_id") or (user or {}).get("tenant_id") or "").strip()
    workspace_id = str((user or {}).get("active_workspace_id") or (user or {}).get("workspace_id") or "").strip()
    scoped = not is_global and bool(tenant_id)

    conversations = 0
    has_convs = await pool.fetchval("SELECT to_regclass('public.conversations')")
    if has_convs:
        if scoped and workspace_id:
            # conversations carry workspace_id directly.
            conversations = await pool.fetchval(
                "SELECT COUNT(*) FROM conversations "
                "WHERE created_at >= date_trunc('day', NOW()) AND workspace_id = $1::uuid",
                workspace_id,
            ) or 0
        else:
            conversations = await pool.fetchval(
                "SELECT COUNT(*) FROM conversations WHERE created_at >= date_trunc('day', NOW())"
            ) or 0

    if scoped:
        tools_today = await pool.fetchval(
            f"SELECT COUNT(*) FROM audit_events ae "
            f"WHERE ae.tool_name IS NOT NULL AND ae.created_at >= date_trunc('day', NOW()) "
            f"AND {_TENANT_ACTOR_SUBQUERY}",
            tenant_id,
        )
    else:
        tools_today = await pool.fetchval(
            "SELECT COUNT(*) FROM audit_events ae "
            "WHERE ae.tool_name IS NOT NULL AND ae.created_at >= date_trunc('day', NOW())"
        )
    return {
        "conversations_today": int(conversations or 0),
        "tools_invoked_today": int(tools_today or 0),
    }


# Actor (user_id) belongs to a workspace of the given tenant. audit_events has
# no tenant column, so we scope by who performed the action.
_TENANT_ACTOR_SUBQUERY = (
    "ae.user_id IN ("
    "SELECT DISTINCT uwr.user_id FROM user_workspace_roles uwr "
    "JOIN workspaces w ON w.id = uwr.workspace_id WHERE w.tenant_id = $1::uuid)"
)


async def _audit_counts(pool, user: dict | None = None) -> dict:
    """Audit events today. Scoped per tenant for non-global users (by actor),
    so a tenant_admin doesn't see platform-wide event counts."""
    role = str((user or {}).get("role") or "").lower()
    is_global = role in {"owner", "super_admin", "admin"}
    tenant_id = str((user or {}).get("active_tenant_id") or (user or {}).get("tenant_id") or "").strip()

    if is_global or not tenant_id:
        total = await pool.fetchval(
            "SELECT COUNT(*) FROM audit_events ae WHERE ae.created_at >= date_trunc('day', NOW())"
        )
        destructive = await pool.fetchval(
            "SELECT COUNT(*) FROM audit_events ae "
            "WHERE ae.risk_level = 'destructive' AND ae.created_at >= date_trunc('day', NOW())"
        )
    else:
        total = await pool.fetchval(
            f"SELECT COUNT(*) FROM audit_events ae "
            f"WHERE ae.created_at >= date_trunc('day', NOW()) AND {_TENANT_ACTOR_SUBQUERY}",
            tenant_id,
        )
        destructive = await pool.fetchval(
            f"SELECT COUNT(*) FROM audit_events ae "
            f"WHERE ae.risk_level = 'destructive' AND ae.created_at >= date_trunc('day', NOW()) "
            f"AND {_TENANT_ACTOR_SUBQUERY}",
            tenant_id,
        )
    return {
        "events_today":              int(total or 0),
        "destructive_actions_today": int(destructive or 0),
    }


@router.get("/kpis")
async def dashboard_kpis(user: dict = Depends(require_authenticated)):
    """Aggregate dashboard payload. One round-trip; the per-section
    helpers run sequentially (cheap; no I/O parallelism win on
    small queries) and return the shape documented in the v1.44.1
    brief."""
    pool = await auth.pool()
    # Scope the dashboard to the ACTIVE WORKSPACE's activated data cartridges.
    # `allowed_cartridges` is the per-(tenant,workspace) entitlement set; we keep
    # only the data cartridges (platform/meta are excluded from the freshness
    # view). A tenant with nothing activated must see an empty surface — NOT the
    # full built-in catalog. The old `or _CARTRIDGES` fallback leaked the whole
    # catalog to every tenant; removed.
    allowed = {str(c).strip() for c in (user.get("allowed_cartridges") or [])}
    workspace_cartridges = tuple(c for c in _CARTRIDGES if c in allowed)
    connected_list: list[str] = []
    for _c in workspace_cartridges:
        if await _vault_connections_for_cartridge(_c, user):
            connected_list.append(_c)
    connected_cartridges = tuple(connected_list)

    role = str(user.get("role") or "").lower()
    is_global = role in {"owner", "super_admin", "admin"}
    workspace_id = str(user.get("active_workspace_id") or user.get("workspace_id") or "").strip() or None

    return {
        "active_cartridges": list(workspace_cartridges),
        "cartridges": {
            "total":        len(workspace_cartridges),
            "connected":    len(connected_cartridges),
            "disconnected": len(workspace_cartridges) - len(connected_cartridges),
        },
        "extractions":   await _extraction_counts(pool, workspace_cartridges, workspace_id, is_global),
        "data_freshness": await _freshness_per_cartridge(pool, workspace_cartridges, workspace_id, is_global),
        "users":         await _user_counts(pool, user),
        "copilot":       await _copilot_counts(pool, user),
        "audit":         await _audit_counts(pool, user),
    }
