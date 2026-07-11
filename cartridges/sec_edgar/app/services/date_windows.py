from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.config_loader import CompanyConfig


@dataclass(frozen=True)
class DateWindow:
    companies: tuple[CompanyConfig, ...]
    from_date: date
    to_date: date


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def build_windows(
    companies: tuple[CompanyConfig, ...],
    watermarks: dict[str, str | None],
    *,
    mode: str,
    from_date: date | None,
    to_date: date,
    default_start: date,
) -> list[DateWindow]:
    buckets: dict[tuple[date, date], list[CompanyConfig]] = {}
    for company in companies:
        start = from_date or default_start
        if mode != "full":
            marks = [
                parse_date(watermarks.get(_watermark_key(company.cik, fact.metric_name)))
                for fact in company.facts
            ]
            latest = max((mark for mark in marks if mark), default=None)
            if latest:
                start = max(default_start, latest - timedelta(days=company.overlap_days))
        buckets.setdefault((start, to_date), []).append(company)
    return [DateWindow(tuple(items), start, end) for (start, end), items in sorted(buckets.items())]


def _watermark_key(cik: str, metric_name: str) -> str:
    return f"{cik}:{metric_name}"
