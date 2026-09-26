from __future__ import annotations

from datetime import date, datetime

import pyarrow as pa

from app.services import b1_queries as q
from app.services import source_counts_mapping as mapping


def test_the_counts_entity_has_a_fixed_typed_schema_like_the_other_configured_entities():
    schema = mapping.arrow_schema()
    assert mapping.ENTITY == "SourceCounts"
    assert mapping.COLUMNS == ("entity", "window_start", "window_end", "source_rows", "counted_at", "error")
    assert schema.names == [*mapping.COLUMNS, q.COMPANY_COLUMN, q.SOURCE_UPDATED_COLUMN, *q.METADATA_COLUMNS]
    assert (mapping.COMPANY_COLUMN, mapping.SOURCE_UPDATED_COLUMN) == (q.COMPANY_COLUMN, q.SOURCE_UPDATED_COLUMN)
    kinds = {field.name: field.type for field in schema}
    assert kinds["window_start"] == kinds["window_end"] == kinds["counted_at"] == pa.timestamp("us")
    assert kinds["source_rows"] == pa.int64()
    assert all(kinds[name] == pa.string() for name in schema.names if name not in {"window_start", "window_end", "counted_at", "source_rows"})


def test_records_are_sorted_and_carry_the_company_alias():
    clock = datetime(2026, 9, 25, 10, 30)
    counts = [
        mapping.SourceCount("mx_b", "OINV", datetime(2026, 7, 1), datetime(2026, 9, 25), 7, clock),
        mapping.SourceCount("mx_a", "OITW", None, datetime(2026, 9, 25), None, clock, "x" * 900),
        mapping.SourceCount("mx_a", "OINV", datetime(2026, 7, 1), datetime(2026, 9, 25), 3, clock),
    ]
    rows = mapping.records(counts)
    assert [(r["entity"], r["_company"]) for r in rows] == [("OINV", "mx_a"), ("OINV", "mx_b"), ("OITW", "mx_a")]
    assert rows[0] == {
        "entity": "OINV", "window_start": datetime(2026, 7, 1), "window_end": datetime(2026, 9, 25), "source_rows": 3,
        "counted_at": clock, "error": None, "_company": "mx_a", "_source_updated_at": None,
    }
    assert len(rows[2]["error"]) == mapping.ERROR_TEXT and rows[2]["source_rows"] is None
    assert mapping.records([]) == []


def test_the_count_window_starts_on_the_first_day_of_the_month_and_ends_today_exclusive():
    assert mapping.count_window(datetime(2026, 9, 25, 23, 59), 24) == (date(2024, 9, 1), date(2026, 9, 25))
    assert mapping.count_window(datetime(2026, 1, 5), 1) == (date(2025, 12, 1), date(2026, 1, 5))
    assert mapping.count_window(datetime(2026, 3, 31), 0) == (date(2026, 3, 1), date(2026, 3, 31))
