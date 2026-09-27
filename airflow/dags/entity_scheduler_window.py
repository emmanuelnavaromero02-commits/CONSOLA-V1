from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(moment.timestamp(), tz=timezone.utc)


def _zone(tz_name: str | None):
    from zoneinfo import ZoneInfo

    return ZoneInfo(str(tz_name or "UTC"))


def _cron_fields(expression: str):
    from croniter import croniter

    expanded, nth_weekday = croniter.expand(expression)
    plain = not nth_weekday and len(expanded) == 5 and all(
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
    """First UTC minute in [start, end) at which cron_expr (evaluated in tz_name) fires, else None."""
    expression = str(cron_expr or "").strip()
    if not expression:
        return None
    try:
        from croniter import croniter

        zone = _zone(tz_name)
        start = _utc(window_start)
        end = _utc(window_end)
        fields, plain = _cron_fields(expression)
        mark = start.replace(second=0, microsecond=0)
        if mark < start:
            mark += timedelta(minutes=1)
        while mark < end:
            local = mark.astimezone(zone)
            if plain:
                if _wall_matches(fields, local) or _gap_backfill_matches(fields, mark, local, zone):
                    return mark
            elif croniter.match(expression, local):
                return mark
            mark += timedelta(minutes=1)
    except Exception:
        return None
    return None
