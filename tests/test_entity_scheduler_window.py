from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from airflow.dags.entity_scheduler_window import fire_in_window, schedule_is_valid


REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _window(start: datetime, minutes: int = 5) -> tuple[datetime, datetime]:
    return start, start + timedelta(minutes=minutes)


def test_morning_in_mexico_city_fires_at_fourteen_utc():
    start, end = _window(datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    fire = fire_in_window("0 8 * * *", "America/Mexico_City", start, end)
    assert fire == datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    assert fire.tzinfo is not None and fire.utcoffset() == timedelta(0)


def test_morning_in_mexico_city_does_not_fire_at_eight_utc():
    start, end = _window(datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    assert fire_in_window("0 8 * * *", "America/Mexico_City", start, end) is None


def test_utc_default_keeps_legacy_behaviour():
    start, end = _window(datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    assert fire_in_window("0 8 * * *", "UTC", start, end) == start
    assert fire_in_window("0 8 * * *", None, start, end) == start
    assert fire_in_window("0 8 * * *", "", start, end) == start


def test_monday_in_utc_plus_nine_fires_on_sunday_utc():
    start, end = _window(datetime(2026, 9, 27, 23, 0, tzinfo=UTC))
    assert start.weekday() == 6
    fire = fire_in_window("0 8 * * 1", "Asia/Tokyo", start, end)
    assert fire == datetime(2026, 9, 27, 23, 0, tzinfo=UTC)
    monday_utc = _window(datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    assert fire_in_window("0 8 * * 1", "Asia/Tokyo", *monday_utc) is None


def test_weekday_end_of_day_skips_weekends_in_local_time():
    friday = _window(datetime(2026, 9, 26, 1, 0, tzinfo=UTC))
    assert fire_in_window("0 19 * * 1-5", "America/Mexico_City", *friday) == friday[0]
    saturday = _window(datetime(2026, 9, 27, 1, 0, tzinfo=UTC))
    assert fire_in_window("0 19 * * 1-5", "America/Mexico_City", *saturday) is None


def test_window_end_is_exclusive():
    start = datetime(2026, 9, 28, 13, 55, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    assert fire_in_window("0 8 * * *", "America/Mexico_City", start, end) is None


def test_naive_window_bounds_are_treated_as_utc():
    start, end = _window(datetime(2026, 9, 28, 14, 0))
    assert fire_in_window("0 8 * * *", "America/Mexico_City", start, end) == datetime(
        2026, 9, 28, 14, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "cron, tz",
    [
        ("0 8 * * *", "Mars/Olympus_Mons"),
        ("0 8 * * *", "../../etc/passwd"),
        ("0 8 * * *", "America/Mexico_City\n"),
        ("not a cron", "UTC"),
        ("0 25 * * *", "UTC"),
        ("", "UTC"),
        (None, "UTC"),
    ],
)
def test_invalid_timezone_or_cron_never_fires(cron, tz):
    start, end = _window(datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    assert fire_in_window(cron, tz, start, end) is None
    assert schedule_is_valid(cron, tz) is False


def test_schedule_is_valid_accepts_real_schedules():
    assert schedule_is_valid("0 8 * * *", "America/Mexico_City") is True
    assert schedule_is_valid("*/15 * * * *", "UTC") is True
    assert schedule_is_valid("0 19 * * 1-5", None) is True


def test_entity_scheduler_evaluates_cron_in_the_entity_timezone():
    source = (REPO / "airflow/dags/entity_scheduler.py").read_text(encoding="utf-8")
    assert "from entity_scheduler_window import fire_in_window, schedule_is_valid" in source
    assert "COALESCE(ec.cron_timezone, 'UTC') AS cron_timezone" in source
    assert "fire_in_window(cron_expr, cron_tz, logical_date, window_end)" in source
    assert "def _fire_in_window(" not in source
    assert "replace(tzinfo=None)" not in source
