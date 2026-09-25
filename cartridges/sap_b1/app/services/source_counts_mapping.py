from __future__ import annotations

from datetime import datetime
from typing import Any

ENTITY = "SourceCounts"
COLUMNS = ("entity", "window_start", "window_end", "source_rows", "counted_at", "error")


def records(
    company: str,
    counts: list[dict[str, Any]],
    *,
    window_start: datetime | None,
    window_end: datetime,
    counted_at: datetime,
) -> list[dict[str, Any]]:
    return [
        {
            "entity": item["entity"],
            "window_start": window_start if item.get("dated") else None,
            "window_end": window_end,
            "source_rows": item.get("rows"),
            "counted_at": counted_at,
            "error": item.get("error"),
            "_company": company,
            "_source_updated_at": None,
        }
        for item in counts
    ]


def arrow_schema():
    import pyarrow as pa

    from app.services.b1_queries import COMPANY_COLUMN, METADATA_COLUMNS, SOURCE_UPDATED_COLUMN

    return pa.schema([
        pa.field("entity", pa.string()),
        pa.field("window_start", pa.timestamp("us")),
        pa.field("window_end", pa.timestamp("us")),
        pa.field("source_rows", pa.int64()),
        pa.field("counted_at", pa.timestamp("us")),
        pa.field("error", pa.string()),
        *(pa.field(column, pa.string()) for column in (COMPANY_COLUMN, SOURCE_UPDATED_COLUMN, *METADATA_COLUMNS)),
    ])
