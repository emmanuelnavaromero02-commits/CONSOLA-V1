"""Server-owned, RLS-scoped discovery for scheduled monitor agents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter

from app.services.db_scope import scoped_db


_MAX_WINDOW = timedelta(minutes=15)
_PAGE_SIZE = 200


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
        iterator = croniter(expression, start.astimezone(zone) - timedelta(seconds=1))
        fire = iterator.get_next(datetime)
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=zone)
        fire_utc = fire.astimezone(timezone.utc)
    except Exception as exc:
        raise ValueError("schedule cron is invalid") from exc
    return fire_utc if start <= fire_utc < end else None


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
    """Discover due agents through one fresh RLS transaction per workspace."""
    start, end = validate_window(window_start, window_end)
    scopes = await _active_workspace_scopes(pool)
    if not scopes:
        return {
            "status": "no_eligible_workspaces",
            "due": [],
            "workspaces": 0,
            "failures": [],
        }
    due: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for scope in scopes:
        tenant_id = scope["tenant_id"]
        workspace_id = scope["workspace_id"]
        try:
            async with scoped_db(pool, tenant_id, workspace_id) as conn:
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
    }
