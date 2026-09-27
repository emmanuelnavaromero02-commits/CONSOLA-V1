from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter

from app.services.db_scope import scoped_db


_MAX_WINDOW = timedelta(minutes=15)
_PAGE_SIZE = 200


async def reconcile_expired_runs(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
) -> int:

    candidates = await conn.fetch(
        """
        SELECT id
          FROM agent_schedule_runs
         WHERE tenant_id = $1::uuid
           AND workspace_id = $2::uuid
           AND status = 'running'
           AND (
               lease_expires_at <= clock_timestamp() - INTERVAL '1 hour'
               OR (lease_expires_at IS NULL
                   AND started_at <= clock_timestamp() - INTERVAL '2 hours')
           )
         ORDER BY id
        """,
        tenant_id,
        workspace_id,
    )
    expired: list[Any] = []
    for candidate in candidates:
        run_id = candidate["id"]
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            f"agent_schedule_effect:{run_id}",
        )
        retired = await conn.fetchrow(
            """
            UPDATE agent_schedule_runs
               SET status = 'error',
                   finished_at = clock_timestamp(),
                   lease_expires_at = NULL,
                   error_message = 'scheduled-run lease expired',
                   metadata = COALESCE(metadata, '{}'::jsonb)
                       || '{"reconciliation":"lease_expired"}'::jsonb
             WHERE id = $1
               AND tenant_id = $2::uuid
               AND workspace_id = $3::uuid
               AND status = 'running'
               AND (
                   lease_expires_at <= clock_timestamp() - INTERVAL '1 hour'
                   OR (lease_expires_at IS NULL
                       AND started_at <= clock_timestamp() - INTERVAL '2 hours')
               )
            RETURNING agent_run_id
            """,
            run_id,
            tenant_id,
            workspace_id,
        )
        if retired is not None:
            expired.append(retired)
    run_ids = [int(row["agent_run_id"]) for row in expired if row["agent_run_id"]]
    if run_ids:
        await conn.execute(
            """
            UPDATE agent_runs
               SET status = 'error',
                   finished_at = COALESCE(finished_at, clock_timestamp()),
                   error_message = COALESCE(error_message, 'scheduled-run lease expired')
             WHERE tenant_id = $1::uuid
               AND workspace_id = $2::uuid
               AND id = ANY($3::bigint[])
               AND status = 'running'
            """,
            tenant_id,
            workspace_id,
            run_ids,
        )
    return len(expired)


def validate_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("runtime window must be timezone-aware")
    normalized_start = start.astimezone(timezone.utc)
    normalized_end = end.astimezone(timezone.utc)
    width = normalized_end - normalized_start
    if width <= timedelta(0) or width > _MAX_WINDOW:
        raise ValueError("runtime window must be positive and at most 15 minutes")
    return normalized_start, normalized_end


async def _active_workspace_scopes(pool: Any) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    cursor_created: datetime | None = None
    cursor_id: str | None = None
    while True:
        rows = await pool.fetch(
            """
            SELECT w.tenant_id::text AS tenant_id, w.id::text AS workspace_id,
                   w.created_at
              FROM workspaces w
              JOIN tenants t ON t.id = w.tenant_id
             WHERE t.status = 'active'
               AND ($1::timestamptz IS NULL OR (w.created_at, w.id) > ($1, $2::uuid))
             ORDER BY w.created_at, w.id
             LIMIT $3
            """,
            cursor_created,
            cursor_id,
            _PAGE_SIZE,
        )
        for row in rows:
            scopes.append(
                {
                    "tenant_id": str(row["tenant_id"]),
                    "workspace_id": str(row["workspace_id"]),
                }
            )
        if len(rows) < _PAGE_SIZE:
            return scopes
        cursor_created = rows[-1]["created_at"]
        cursor_id = str(rows[-1]["workspace_id"])


def _cron_fields(expression: str):
    expanded, nth_weekday = croniter.expand(expression)
    if len(expanded) != 5:
        raise ValueError("only five-field cron expressions are supported")
    plain = not nth_weekday and all(
        entry == "*" or isinstance(entry, int) for field in expanded for entry in field
    )
    return expanded, plain


def _wall_matches(fields, wall: datetime) -> bool:
    minutes, hours, dom, months, dows = fields

    def has(field, value) -> bool:
        return "*" in field or value in field

    if not (has(minutes, wall.minute) and has(hours, wall.hour) and has(months, wall.month)):
        return False
    dom_ok = has(dom, wall.day)
    dow_ok = has(dows, wall.isoweekday() % 7)
    if "*" in dom or "*" in dows:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def _fold_skips(fields, local: datetime) -> bool:
    """Vixie semantics: fixed-time jobs run once in a repeated DST hour; wildcard jobs run each hour."""
    return local.fold == 1 and "*" not in fields[0] and "*" not in fields[1]


def _gap_backfill_matches(fields, mark: datetime, local: datetime, zone) -> bool:
    """True when a DST gap ends at this minute and the cron matches a skipped wall time."""
    prev_local = (mark - timedelta(minutes=1)).astimezone(zone)
    end_wall = local.replace(tzinfo=None)
    wall = prev_local.replace(tzinfo=None) + timedelta(minutes=1)
    if end_wall - wall <= timedelta(0):
        return False
    while wall < end_wall:
        if _wall_matches(fields, wall):
            return True
        wall += timedelta(minutes=1)
    return False


def _cron_fires_at_minute(expression: str, mark: datetime, zone) -> bool:
    fields, plain = _cron_fields(expression)
    local = mark.astimezone(zone)
    if plain:
        wall_hit = not _fold_skips(fields, local) and _wall_matches(fields, local)
        return wall_hit or _gap_backfill_matches(fields, mark, local, zone)
    return bool(croniter.match(expression, local))


def cron_fires_at(expression: str, tz_name: Any, instant: datetime) -> bool:
    """True when the cron (evaluated in tz_name) fires at this UTC minute, DST gaps backfilled."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(str(tz_name or "UTC"))
    mark = instant.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return _cron_fires_at_minute(expression, mark, zone)


def _fire_in_window(
    schedule: dict[str, Any],
    start: datetime,
    end: datetime,
) -> datetime | None:
    expression = str(
        schedule.get("cron") or schedule.get("cron_expression") or ""
    ).strip()
    if schedule.get("enabled") is False:
        return None
    if not expression:
        raise ValueError("schedule cron is invalid")
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(str(schedule.get("tz") or "UTC"))
    except Exception as exc:
        raise ValueError("schedule timezone is invalid") from exc
    try:
        mark = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
        if mark < start:
            mark += timedelta(minutes=1)
        while mark < end:
            if _cron_fires_at_minute(expression, mark, zone):
                return mark
            mark += timedelta(minutes=1)
    except Exception as exc:
        raise ValueError("schedule cron is invalid") from exc
    return None


def _due_row(
    row: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any] | None:
    extra = row["extra"]
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, json.JSONDecodeError):
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    schedule = extra.get("schedule") if isinstance(extra.get("schedule"), dict) else {}
    fire_at = _fire_in_window(schedule, start, end)
    if fire_at is None:
        return None
    return {
        "id": str(row["id"]),
        "cartridge_id": str(row["cartridge_id"]),
        "slug": str(row["slug"]),
        "name": str(row["name"]),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "prompt": str(schedule.get("prompt") or "Ejecuta tu tarea programada."),
        "scheduled_fire_at": fire_at.isoformat(),
        "schedule_key": str(schedule.get("key") or "default")[:120],
    }


async def find_due_agents(
    pool: Any,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    start, end = validate_window(window_start, window_end)
    scopes = await _active_workspace_scopes(pool)
    if not scopes:
        return {
            "status": "no_eligible_workspaces",
            "due": [],
            "workspaces": 0,
            "failures": [],
            "reconciled_expired_runs": 0,
        }
    due: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    reconciled_expired_runs = 0
    for scope in scopes:
        tenant_id = scope["tenant_id"]
        workspace_id = scope["workspace_id"]
        try:
            async with scoped_db(pool, tenant_id, workspace_id) as conn:
                reconciled_expired_runs += await reconcile_expired_runs(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                rows = await conn.fetch(
                    """
                    SELECT id, cartridge_id, slug, name, extra
                      FROM agents
                     WHERE tenant_id = $1::uuid
                       AND workspace_id = $2::uuid
                       AND is_active = TRUE
                       AND extra ? 'schedule'
                     ORDER BY id
                    """,
                    tenant_id,
                    workspace_id,
                )
            for row in rows:
                candidate = _due_row(
                    row,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    start=start,
                    end=end,
                )
                if candidate:
                    due.append(candidate)
        except Exception as exc:  # noqa: BLE001 - isolate scopes, report class only
            failures.append(
                {"workspace_id": workspace_id, "error_code": type(exc).__name__}
            )
    return {
        "status": "partial" if failures else "ready",
        "due": due,
        "workspaces": len(scopes),
        "failures": failures,
        "reconciled_expired_runs": reconciled_expired_runs,
    }
