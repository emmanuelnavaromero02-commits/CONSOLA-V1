from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

ENTITY = "SourceCounts"
COLUMNS = ("entity", "window_start", "window_end", "source_rows", "counted_at", "error")
COMPANY_COLUMN = "_company"
SOURCE_UPDATED_COLUMN = "_source_updated_at"
ERROR_TEXT = 500


@dataclass(frozen=True)
class SourceCount:
    company: str
    entity: str
    window_start: datetime | None
    window_end: datetime
    source_rows: int | None
    counted_at: datetime
    error: str | None = None


def count_window(clock: datetime, months: int) -> tuple[date, date]:
    # [first day of the month `months` back, source-clock date): the initial load's window, end exclusive
    if months < 0:
        raise ValueError("months must not be negative")
    start = date(clock.year, clock.month, 1)
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)
    return start, clock.date()


def records(counts: Iterable[SourceCount]) -> list[dict[str, Any]]:
    return [
        {
            "entity": count.entity,
            "window_start": count.window_start,
            "window_end": count.window_end,
            "source_rows": count.source_rows,
            "counted_at": count.counted_at,
            "error": count.error[:ERROR_TEXT] if count.error else None,
            COMPANY_COLUMN: count.company,
            SOURCE_UPDATED_COLUMN: None,
        }
        for count in sorted(counts, key=lambda item: (item.entity, item.company))
    ]


def arrow_schema():
    import pyarrow as pa

    from app.services.b1_queries import METADATA_COLUMNS

    kinds = {
        "window_start": pa.timestamp("us"),
        "window_end": pa.timestamp("us"),
        "counted_at": pa.timestamp("us"),
        "source_rows": pa.int64(),
    }
    fields = [pa.field(column, kinds.get(column, pa.string())) for column in COLUMNS]
    fields.extend(pa.field(column, pa.string()) for column in (COMPANY_COLUMN, SOURCE_UPDATED_COLUMN, *METADATA_COLUMNS))
    return pa.schema(fields)
