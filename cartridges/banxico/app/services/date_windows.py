from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.config_loader import SeriesConfig


@dataclass(frozen=True)
class SeriesWindow:
    from_date: date
    to_date: date
    series: tuple[SeriesConfig, ...]


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def build_windows(
    series: tuple[SeriesConfig, ...],
    watermarks: dict[str, str | None],
    *,
    mode: str,
    from_date: date | None,
    to_date: date,
    default_start: date,
) -> list[SeriesWindow]:
    grouped: dict[tuple[date, date], list[SeriesConfig]] = defaultdict(list)
    for item in series:
        if mode == "full":
            start = from_date or default_start
        elif from_date:
            start = from_date
        else:
            watermark = parse_date(watermarks.get(item.series_id))
            start = (watermark - timedelta(days=item.overlap_days)) if watermark else default_start
        grouped[(start, to_date)].append(item)
    return [
        SeriesWindow(from_date=start, to_date=end, series=tuple(items))
        for (start, end), items in sorted(grouped.items())
    ]


def banxico_date(value: str) -> str:
    day, month, year = value.split("/")
    return f"{year}-{month}-{day}"
