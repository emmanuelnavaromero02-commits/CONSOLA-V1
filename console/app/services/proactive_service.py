"""Sprint v1.44.2 (Tarea F) — proactive copilot heuristics.

The copilot's "Buenos días" briefing on the dashboard surfaces a
small number of highlights pulled from rule-based heuristics:

  * analyze_freshness          — cartridge hasn't extracted in >24h
  * analyze_volume_anomaly     — yesterday's row count differs from
                                 the 7-day median by more than ±30%
  * analyze_pending_actions    — open jobs older than 24h
  * analyze_extraction_failures — failed extraction_runs in the last 24h

Each analyzer returns a list of ``Highlight`` dicts with the same
shape so the router can concat + sort + filter dismissed ones in
one pass.

Heuristic-only — no ML, no LLM call. The brief is explicit on this:
proactive UX matters, prediction does not.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services import auth


logger = logging.getLogger(__name__)


# A Highlight is a small dict with these keys; declared here as a type
# hint instead of a TypedDict so the existing audit_service.record_event
# signature (metadata: dict | None) can swallow it without coercion.
Highlight = dict[str, Any]


_KNOWN_CARTRIDGES = ("replicon", "sap_hcm", "sap_s4hana", "sap_successfactors")


# ── Severity rank for the final sort. Highest first in the briefing. ─────

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _make_highlight(
    *,
    highlight_id: str,
    severity:     str,
    title:        str,
    body:         str,
    action_label: str | None = None,
    action_href:  str | None = None,
    cartridge:    str | None = None,
    category:     str,
) -> Highlight:
    """Construct a Highlight dict with the documented shape.

    ``highlight_id`` is the **stable** key used by the dismissal
    endpoint — it must be deterministic for the same condition so a
    user can permanently dismiss e.g. ``freshness:sap_hcm`` and not
    see it reappear five minutes later.
    """
    return {
        "id":           highlight_id,
        "severity":     severity,
        "title":        title,
        "body":         body,
        "category":     category,
        "cartridge":    cartridge,
        "action_label": action_label,
        "action_href":  action_href,
    }


# ── 1. Freshness ─────────────────────────────────────────────────────────


_STALE_HOURS = 24


async def analyze_freshness() -> list[Highlight]:
    """Any cartridge whose last successful extraction is older than
    24 hours surfaces as a warning. Pre-existing index from migration
    49 (idx_extraction_runs_cartridge_finished_success) covers this
    query so it stays cheap to run on every briefing request."""
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT cartridge_id,
               EXTRACT(EPOCH FROM (NOW() - MAX(finished_at))) / 3600.0 AS age_hours
          FROM extraction_runs
         WHERE status = 'success'
         GROUP BY cartridge_id
        """
    )
    by_id = {r["cartridge_id"]: r for r in rows}

    out: list[Highlight] = []
    for cart in _KNOWN_CARTRIDGES:
        row = by_id.get(cart)
        if row is None or row["age_hours"] is None:
            out.append(_make_highlight(
                highlight_id=f"freshness:{cart}:never",
                severity="warning",
                title=f"{cart} sin extracciones",
                body=f"Nunca se ejecutó una extracción exitosa para {cart}.",
                action_label="Configurar",
                action_href=f"/cartridges/{cart}",
                cartridge=cart,
                category="freshness",
            ))
            continue
        age_hours = float(row["age_hours"])
        if age_hours >= _STALE_HOURS * 3:
            severity = "critical"
        elif age_hours >= _STALE_HOURS:
            severity = "warning"
        else:
            continue
        out.append(_make_highlight(
            highlight_id=f"freshness:{cart}",
            severity=severity,
            title=f"Datos de {cart} con {round(age_hours)} h sin actualizar",
            body=(
                f"La última extracción exitosa de {cart} fue hace "
                f"{round(age_hours)} horas."
            ),
            action_label="Investigar",
            action_href=f"/cartridges/{cart}",
            cartridge=cart,
            category="freshness",
        ))
    return out


# ── 2. Volume anomaly ────────────────────────────────────────────────────


_VOLUME_DELTA_PCT = 30


async def analyze_volume_anomaly() -> list[Highlight]:
    """Compare yesterday's extraction count vs the median of the
    prior 7 days. A delta of more than ±30% surfaces as warning.

    Uses extraction_runs.records_extracted (not row count) so a single
    run that pulled 1 M rows doesn't get diluted by a same-day run
    that pulled 100 rows. NULL records_extracted is treated as 0 so
    cartridges that haven't reported volumes don't surface here.
    """
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        WITH per_day AS (
            SELECT cartridge_id,
                   date_trunc('day', finished_at) AS day,
                   SUM(COALESCE(records_extracted, 0)) AS records
              FROM extraction_runs
             WHERE status = 'success'
               AND finished_at >= NOW() - INTERVAL '8 days'
             GROUP BY cartridge_id, day
        ),
        latest AS (
            SELECT cartridge_id, records AS yesterday
              FROM per_day
             WHERE day = date_trunc('day', NOW() - INTERVAL '1 day')
        ),
        baseline AS (
            SELECT cartridge_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY records) AS median
              FROM per_day
             WHERE day BETWEEN date_trunc('day', NOW() - INTERVAL '8 days')
                           AND date_trunc('day', NOW() - INTERVAL '2 days')
             GROUP BY cartridge_id
        )
        SELECT l.cartridge_id, l.yesterday, b.median
          FROM latest l
          JOIN baseline b USING (cartridge_id)
         WHERE b.median > 0
        """
    )
    out: list[Highlight] = []
    for row in rows:
        cart = row["cartridge_id"]
        yesterday = float(row["yesterday"] or 0)
        median = float(row["median"] or 0)
        if median == 0:
            continue
        delta_pct = ((yesterday - median) / median) * 100
        if abs(delta_pct) < _VOLUME_DELTA_PCT:
            continue
        direction = "subió" if delta_pct > 0 else "bajó"
        out.append(_make_highlight(
            highlight_id=f"volume:{cart}",
            severity="warning",
            title=f"Volumen de {cart} {direction} {abs(round(delta_pct))}%",
            body=(
                f"Ayer {cart} procesó {int(yesterday):,} filas vs una "
                f"mediana de {int(median):,} en la semana previa."
            ),
            action_label="Ver detalle",
            action_href=f"/cartridges/{cart}",
            cartridge=cart,
            category="volume",
        ))
    return out


# ── 3. Pending actions ───────────────────────────────────────────────────


async def analyze_pending_actions() -> list[Highlight]:
    """Jobs that have been in 'running' state for more than 24h are
    almost certainly stuck — surface as warning so the user can
    investigate (orphaned worker, missing wakeup, etc.)."""
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT job_id, tool,
               EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600.0 AS age_hours
          FROM jobs
         WHERE status = 'running'
           AND created_at < NOW() - INTERVAL '24 hours'
         ORDER BY created_at
         LIMIT 5
        """
    )
    out: list[Highlight] = []
    for row in rows:
        out.append(_make_highlight(
            highlight_id=f"pending:{row['job_id']}",
            severity="warning",
            title=f"Trabajo colgado {row['tool']}",
            body=(
                f"El job {row['job_id']} lleva {round(float(row['age_hours']))} "
                "horas en ejecución. Probablemente está atascado."
            ),
            action_label="Revisar",
            action_href="/operations",
            category="pending",
        ))
    return out


# ── 4. Extraction failures ───────────────────────────────────────────────


async def analyze_extraction_failures() -> list[Highlight]:
    """Count failed extraction_runs in the last 24h per cartridge.
    More than one failure → warning; more than three → critical."""
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT cartridge_id, COUNT(*) AS failures
          FROM extraction_runs
         WHERE status = 'failed'
           AND finished_at >= NOW() - INTERVAL '24 hours'
         GROUP BY cartridge_id
        """
    )
    out: list[Highlight] = []
    for row in rows:
        cart = row["cartridge_id"]
        failures = int(row["failures"])
        if failures == 0:
            continue
        severity = "critical" if failures >= 3 else "warning"
        out.append(_make_highlight(
            highlight_id=f"failures:{cart}",
            severity=severity,
            title=f"{failures} extracciones fallidas en {cart}",
            body=(
                f"Hubo {failures} extracciones con status='failed' en "
                f"{cart} en las últimas 24 horas."
            ),
            action_label="Ver logs",
            action_href=f"/cartridges/{cart}",
            cartridge=cart,
            category="failures",
        ))
    return out


# ── Aggregator ───────────────────────────────────────────────────────────


async def briefing_for_user(user_id: int, *, limit: int = 6) -> list[Highlight]:
    """Run every analyzer, drop dismissed entries for this user, sort
    by severity, return up to ``limit`` highlights.

    The 4 analyzers are awaited sequentially — each is a single
    bounded query, parallelism doesn't help and complicates error
    handling. If one analyzer raises (e.g. a schema-drift bug) the
    whole briefing returns empty rather than partial; the alternative
    would be a half-rendered card list that confuses the user.
    """
    try:
        groups = (
            await analyze_freshness(),
            await analyze_volume_anomaly(),
            await analyze_pending_actions(),
            await analyze_extraction_failures(),
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("briefing_for_user: analyzer raised; returning empty")
        return []

    all_highlights: list[Highlight] = [h for group in groups for h in group]

    # Subtract dismissed IDs for this user.
    if all_highlights:
        pool = await auth.pool()
        dismissed_rows = await pool.fetch(
            """
            SELECT highlight_id
              FROM copilot_briefing_dismissed
             WHERE user_id = $1
            """,
            user_id,
        )
        dismissed = {r["highlight_id"] for r in dismissed_rows}
        all_highlights = [h for h in all_highlights if h["id"] not in dismissed]

    all_highlights.sort(key=lambda h: _SEVERITY_ORDER.get(h["severity"], 99))
    return all_highlights[:limit]


async def dismiss_highlight(user_id: int, highlight_id: str) -> bool:
    """Record a dismissal so the highlight stops surfacing for this
    user. Idempotent via ON CONFLICT — repeated POSTs are no-ops."""
    pool = await auth.pool()
    await pool.execute(
        """
        INSERT INTO copilot_briefing_dismissed (user_id, highlight_id, dismissed_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (user_id, highlight_id) DO NOTHING
        """,
        user_id, highlight_id,
    )
    return True
