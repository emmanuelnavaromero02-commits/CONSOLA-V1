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


async def _extraction_counts(pool, active_cartridges: tuple[str, ...]) -> dict:
    """Extraction runs started today + over the last 7 days.

    Uses ``started_at`` (not finished_at) because migration 40 only
    indexes ``idx_extraction_runs_started_at`` — querying finished_at
    forces a seq-scan on every 30s dashboard poll, which compounds
    fast with multiple operators open. The KPI semantics are
    equivalent for a daily window (an extraction started 2 minutes
    ago that hasn't finished still counts as "today's work").
    """
    if not active_cartridges:
        return {
            "today": 0,
            "week": 0,
            "productive_failures_today": 0,
            "unscope_noise_failures_today": 0,
        }
    today = await pool.fetchval(
        """
        SELECT COUNT(*)
          FROM extraction_runs
         WHERE started_at >= date_trunc('day', NOW())
           AND cartridge_id = ANY($1::text[])
        """,
        list(active_cartridges),
    )
    week = await pool.fetchval(
        """
        SELECT COUNT(*)
          FROM extraction_runs
         WHERE started_at >= NOW() - INTERVAL '7 days'
           AND cartridge_id = ANY($1::text[])
        """,
        list(active_cartridges),
    )
    productive_failures = await pool.fetchval(
        """
        SELECT COUNT(*)
          FROM extraction_runs
         WHERE started_at >= date_trunc('day', NOW())
           AND status = 'failed'
           AND cartridge_id = ANY($1::text[])
        """,
        list(active_cartridges),
    )
    unscope_noise = await pool.fetchval(
        """
        SELECT COUNT(*)
          FROM extraction_runs
         WHERE started_at >= date_trunc('day', NOW())
           AND status = 'failed'
           AND NOT (cartridge_id = ANY($1::text[]))
        """,
        list(active_cartridges),
    )
    return {
        "today": int(today or 0),
        "week":  int(week or 0),
        "productive_failures_today": int(productive_failures or 0),
        "unscope_noise_failures_today": int(unscope_noise or 0),
    }


async def _freshness_per_cartridge(pool, active_cartridges: tuple[str, ...]) -> dict:
    """Hours since the latest successful extraction per cartridge."""
    if not active_cartridges:
        return {}
    rows = await pool.fetch(
        """
        SELECT cartridge_id,
               EXTRACT(EPOCH FROM (NOW() - MAX(finished_at))) / 3600.0 AS age_hours
          FROM extraction_runs
         WHERE status = 'success'
           AND cartridge_id = ANY($1::text[])
         GROUP BY cartridge_id
        """,
        list(active_cartridges),
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


async def _user_counts(pool) -> dict:
    """Daily-active = distinct emails with a successful login today.

    The login_attempts table is authoritative for "did user X log in
    on date Y" (login_security migration v1.32) — querying users.last_login
    would race the JWT-refresh path. Column is ``created_at`` (see
    infra/init/17_login_security.sql:8) and is covered by
    ``idx_login_attempts_email_created_at``.
    """
    active_today = await pool.fetchval(
        """
        SELECT COUNT(DISTINCT email)
          FROM login_attempts
         WHERE success = TRUE
           AND created_at >= date_trunc('day', NOW())
        """
    )
    total = await pool.fetchval("SELECT COUNT(*) FROM users")
    return {
        "active_today": int(active_today or 0),
        "total":        int(total or 0),
    }


async def _copilot_counts(pool) -> dict:
    """Copilot activity today — conversations started + tool
    invocations.

    Table is ``conversations`` (created by migration 38) — NOT
    ``copilot_conversations``. The to_regclass guard handles
    pre-v1.42 DBs where the table doesn't exist yet (returns NULL
    → counts default to 0).
    """
    conversations = 0
    has_convs = await pool.fetchval(
        "SELECT to_regclass('public.conversations')"
    )
    if has_convs:
        conversations = await pool.fetchval(
            """
            SELECT COUNT(*) FROM conversations
             WHERE created_at >= date_trunc('day', NOW())
            """
        ) or 0

    tools_today = await pool.fetchval(
        """
        SELECT COUNT(*) FROM audit_events
         WHERE tool_name IS NOT NULL
           AND created_at >= date_trunc('day', NOW())
        """
    )
    return {
        "conversations_today": int(conversations or 0),
        "tools_invoked_today": int(tools_today or 0),
    }


async def _audit_counts(pool) -> dict:
    total = await pool.fetchval(
        """
        SELECT COUNT(*) FROM audit_events
         WHERE created_at >= date_trunc('day', NOW())
        """
    )
    destructive = await pool.fetchval(
        """
        SELECT COUNT(*) FROM audit_events
         WHERE risk_level = 'destructive'
           AND created_at >= date_trunc('day', NOW())
        """
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
    # Scope KPIs to cartridges with an active scoped Vault connection. When
    # NONE are connected yet (fresh install / local / E2E / demo stack), fall
    # back to the full built-in catalog so the dashboard is never a dead
    # surface. Once a real connection exists (e.g. FEMSA's femsa_sf) the view
    # scopes down to it automatically.
    active_cartridges = await _active_scoped_cartridges(user) or _CARTRIDGES
    return {
        "active_cartridges": list(active_cartridges),
        "cartridges":    await _cartridge_counts(pool, active_cartridges),
        "extractions":   await _extraction_counts(pool, active_cartridges),
        "data_freshness": await _freshness_per_cartridge(pool, active_cartridges),
        "users":         await _user_counts(pool),
        "copilot":       await _copilot_counts(pool),
        "audit":         await _audit_counts(pool),
    }
