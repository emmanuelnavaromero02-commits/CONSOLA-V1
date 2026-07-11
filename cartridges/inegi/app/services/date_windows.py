from __future__ import annotations

import re
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


def inegi_date(value: str) -> str:
    text = value.strip()
    if not text:
        return "1900-01-01"
    quarterly = re.match(r"^(\d{4})[-/]?Q([1-4])$", text, flags=re.IGNORECASE)
    if quarterly:
        month = 1 + (int(quarterly.group(2)) - 1) * 3
        return f"{quarterly.group(1)}-{month:02d}-01"
    parts = re.split(r"[-/]", text)
    if len(parts) == 3:
        year, month, day = _ymd(parts)
        return f"{year:04d}-{month:02d}-{day:02d}"
    if len(parts) == 2:
        year, month = int(parts[0]), int(parts[1])
        return f"{year:04d}-{month:02d}-01"
    if len(parts) == 1 and parts[0].isdigit():
        return f"{int(parts[0]):04d}-01-01"
    return date.fromisoformat(text[:10]).isoformat()


def _ymd(parts: list[str]) -> tuple[int, int, int]:
    first = int(parts[0])
    if first > 31:
        return first, int(parts[1]), int(parts[2])
    return int(parts[2]), int(parts[1]), first
