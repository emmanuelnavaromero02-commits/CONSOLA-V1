from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from app.services import auth
from app.services.db_scope import scoped_db


logger = logging.getLogger(__name__)


Highlight = dict[str, Any]


_KNOWN_CARTRIDGES = ("replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1")


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _cartridge_href(cartridge_id: str) -> str:
    return f"/cartridges/viewer?id={quote(str(cartridge_id), safe='')}"


def _visible_cartridges(user_context: dict | None) -> set[str] | None:
    if not user_context or user_context.get("role") in {"owner", "super_admin", "admin"}:
        return None
    allowed = {
        str(item)
        for item in (user_context.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    return allowed


def _scope_values(user_context: dict | None) -> tuple[str, str, set[str] | None]:
    tenant_id = str((user_context or {}).get("active_tenant_id") or (user_context or {}).get("tenant_id") or "").strip()
    workspace_id = str((user_context or {}).get("active_workspace_id") or (user_context or {}).get("workspace_id") or "").strip()
    return tenant_id, workspace_id, _visible_cartridges(user_context)


def _cartridge_filter_sql(visible: set[str] | None, start_at: int) -> tuple[str, list[Any]]:
    if visible is None:
        return "", []
    if not visible:
        return " AND FALSE", []
    return f" AND cartridge_id = ANY(${start_at}::text[])", [sorted(visible)]


def _filter_visible_highlights(highlights: list[Highlight], user_context: dict | None) -> list[Highlight]:
    visible = _visible_cartridges(user_context)
    if visible is None:
        return highlights
    return [
        highlight
        for highlight in highlights
        if not highlight.get("cartridge") or str(highlight.get("cartridge")) in visible
    ]


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


_STALE_HOURS = 24


async def analyze_freshness(user_context: dict | None = None) -> list[Highlight]:
    pool = await auth.pool()
    tenant_id, workspace_id, visible = _scope_values(user_context)
    if workspace_id:
        cart_sql, cart_args = _cartridge_filter_sql(visible, 3)
        async with scoped_db(pool, tenant_id, workspace_id) as conn:
            rows = await conn.fetch(
                f"""
                SELECT cartridge_id,
                       EXTRACT(EPOCH FROM (NOW() - MAX(finished_at))) / 3600.0 AS age_hours
                  FROM pipeline_runs
                 WHERE status = 'success'
                   AND workspace_id = $1::uuid
                   AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
                   {cart_sql}
                 GROUP BY cartridge_id
                """,
                workspace_id,
                tenant_id or None,
                *cart_args,
            )
    else:
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
                action_href=_cartridge_href(cart),
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
            action_href=_cartridge_href(cart),
            cartridge=cart,
            category="freshness",
        ))
    return out


_VOLUME_DELTA_PCT = 30


async def analyze_volume_anomaly(user_context: dict | None = None) -> list[Highlight]:
    pool = await auth.pool()
    tenant_id, workspace_id, visible = _scope_values(user_context)
    source_table = "pipeline_runs" if workspace_id else "extraction_runs"
    volume_column = "record_count" if workspace_id else "records_extracted"
    cart_sql, cart_args = _cartridge_filter_sql(visible, 3 if workspace_id else 1)
    scope_sql = ""
    params: list[Any] = []
    if workspace_id:
        scope_sql = "AND workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)"
        params = [workspace_id, tenant_id or None]
    query = f"""
    WITH per_day AS (
        SELECT cartridge_id,
               date_trunc('day', finished_at) AS day,
               SUM(COALESCE({volume_column}, 0)) AS records
          FROM {source_table}
         WHERE status = 'success'
           AND finished_at >= NOW() - INTERVAL '8 days'
           {scope_sql}
           {cart_sql}
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
    if workspace_id:
        async with scoped_db(pool, tenant_id, workspace_id) as conn:
            rows = await conn.fetch(query, *params, *cart_args)
    else:
        rows = await pool.fetch(query, *params, *cart_args)
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
            action_href=_cartridge_href(cart),
            cartridge=cart,
            category="volume",
        ))
    return out


async def analyze_pending_actions(user_context: dict | None = None) -> list[Highlight]:
    pool = await auth.pool()
    tenant_id, workspace_id, visible = _scope_values(user_context)
    params: list[Any] = []
    scope_sql = ""
    if workspace_id:
        params.extend([tenant_id, workspace_id, sorted(visible or [])])
        scope_sql = """
           AND COALESCE(args->>'tenant_id', result->>'tenant_id', '') = $1
           AND COALESCE(args->>'workspace_id', result->>'workspace_id', '') = $2
           AND (
                '*' = ANY($3::text[])
                OR COALESCE(args->>'cartridge_id', args->>'cartridge',
                            result->>'cartridge_id', result->>'cartridge', '') = ANY($3::text[])
           )
        """
    rows = await pool.fetch(
        f"""
        SELECT job_id, tool,
               EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600.0 AS age_hours
          FROM jobs
         WHERE status = 'running'
           AND created_at < NOW() - INTERVAL '24 hours'
           {scope_sql}
         ORDER BY created_at
         LIMIT 5
        """,
        *params,
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


async def analyze_extraction_failures(user_context: dict | None = None) -> list[Highlight]:
    pool = await auth.pool()
    tenant_id, workspace_id, visible = _scope_values(user_context)
    if workspace_id:
        cart_sql, cart_args = _cartridge_filter_sql(visible, 3)
        async with scoped_db(pool, tenant_id, workspace_id) as conn:
            rows = await conn.fetch(
                f"""
                SELECT cartridge_id, COUNT(*) AS failures
                  FROM pipeline_runs
                 WHERE status = 'failed'
                   AND finished_at >= NOW() - INTERVAL '24 hours'
                   AND workspace_id = $1::uuid
                   AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
                   {cart_sql}
                 GROUP BY cartridge_id
                """,
                workspace_id,
                tenant_id or None,
                *cart_args,
            )
    else:
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
            action_href=_cartridge_href(cart),
            cartridge=cart,
            category="failures",
        ))
    return out


async def briefing_for_user(
    user_id: int,
    *,
    limit: int = 6,
    user_context: dict | None = None,
) -> list[Highlight]:
    try:
        groups = (
            await analyze_freshness(user_context=user_context),
            await analyze_volume_anomaly(user_context=user_context),
            await analyze_pending_actions(user_context=user_context),
            await analyze_extraction_failures(user_context=user_context),
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("briefing_for_user: analyzer raised; returning empty")
        return []

    all_highlights: list[Highlight] = [h for group in groups for h in group]
    all_highlights = _filter_visible_highlights(all_highlights, user_context)

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
