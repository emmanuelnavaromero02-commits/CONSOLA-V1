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

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated
from app.services import auth


router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


# Cartridge IDs the platform ships with — matches the registry in
# console/app/services/mcp_registry.py and the router map in
# console/app/routers/cartridges.py. Adding a new cartridge means
# updating those + this list (the test guards parity).
_CARTRIDGES = ("replicon", "sap_hcm", "sap_s4hana", "sap_successfactors")


def _freshness_label(age_hours: float | None) -> str:
    """Map an extraction-age in hours to a UI-facing status code."""
    if age_hours is None:
        return "never"
    if age_hours <= 24:
        return "fresh"
    if age_hours <= 72:
        return "stale"
    return "very_stale"


async def _cartridge_counts(pool) -> dict:
    """Healthy vs disconnected per the mcp_servers registry."""
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE category = 'cartridge') AS total,
            COUNT(*) FILTER (WHERE category = 'cartridge' AND healthy IS TRUE)  AS connected,
            COUNT(*) FILTER (WHERE category = 'cartridge' AND healthy IS NOT TRUE) AS disconnected
        FROM mcp_servers
        """
    ) or {}
    return {
        "total":        int(row.get("total") or 0),
        "connected":    int(row.get("connected") or 0),
        "disconnected": int(row.get("disconnected") or 0),
    }


async def _extraction_counts(pool) -> dict:
    """Extraction runs started today + over the last 7 days.

    Uses ``started_at`` (not finished_at) because migration 40 only
    indexes ``idx_extraction_runs_started_at`` — querying finished_at
    forces a seq-scan on every 30s dashboard poll, which compounds
    fast with multiple operators open. The KPI semantics are
    equivalent for a daily window (an extraction started 2 minutes
    ago that hasn't finished still counts as "today's work").
    """
    today = await pool.fetchval(
        """
        SELECT COUNT(*)
          FROM extraction_runs
         WHERE started_at >= date_trunc('day', NOW())
        """
    )
    week = await pool.fetchval(
        """
        SELECT COUNT(*)
          FROM extraction_runs
         WHERE started_at >= NOW() - INTERVAL '7 days'
        """
    )
    return {
        "today": int(today or 0),
        "week":  int(week or 0),
    }


async def _freshness_per_cartridge(pool) -> dict:
    """Hours since the latest successful extraction per cartridge."""
    rows = await pool.fetch(
        """
        SELECT cartridge_id,
               EXTRACT(EPOCH FROM (NOW() - MAX(finished_at))) / 3600.0 AS age_hours
          FROM extraction_runs
         WHERE status = 'success'
         GROUP BY cartridge_id
        """
    )
    by_id: dict = {}
    for row in rows:
        cid = row["cartridge_id"]
        age = float(row["age_hours"]) if row["age_hours"] is not None else None
        by_id[cid] = age
    out: dict = {}
    for cart in _CARTRIDGES:
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
    return {
        "cartridges":    await _cartridge_counts(pool),
        "extractions":   await _extraction_counts(pool),
        "data_freshness": await _freshness_per_cartridge(pool),
        "users":         await _user_counts(pool),
        "copilot":       await _copilot_counts(pool),
        "audit":         await _audit_counts(pool),
    }
