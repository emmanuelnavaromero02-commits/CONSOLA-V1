from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(moment.timestamp(), tz=timezone.utc)


def _zone(tz_name: str | None):
    from zoneinfo import ZoneInfo

    return ZoneInfo(str(tz_name or "UTC"))


def schedule_is_valid(cron_expr: str | None, tz_name: str | None) -> bool:
    try:
        from croniter import croniter

        _zone(tz_name)
        return bool(str(cron_expr or "").strip()) and croniter.is_valid(str(cron_expr).strip())
    except Exception:
        return False


def fire_in_window(
    cron_expr: str | None,
    tz_name: str | None,
    window_start: datetime,
    window_end: datetime,
) -> datetime | None:
    """Return the UTC fire time of cron_expr (evaluated in tz_name) inside [start, end), else None."""
    expression = str(cron_expr or "").strip()
    if not expression:
        return None
    try:
        from croniter import croniter

        zone = _zone(tz_name)
        start = _utc(window_start)
        end = _utc(window_end)
        fire = croniter(expression, start.astimezone(zone) - timedelta(seconds=1)).get_next(datetime)
    except Exception:
        return None
    if fire.tzinfo is None:
        fire = fire.replace(tzinfo=zone)
    fire_utc = fire.astimezone(timezone.utc)
    return fire_utc if start <= fire_utc < end else None
