from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.core.b1_source import Company, Connection
from app.services.b1_queries import (
    MODES,
    WATERMARK_UPDATE_TS,
    EntityPlan,
    Watermark,
    keyset_cursor,
    next_watermark,
    rows_to_records,
    select_sql,
    watermark_key,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000
WATERMARK_BUFFER_MINUTES = 5

GetWatermark = Callable[[str], Any]
UpdateWatermark = Callable[[str, str], None]
WriteBatch = Callable[[list[dict[str, Any]]], None]


def effective_mode(config: dict[str, Any], plan: EntityPlan, from_date: str | None, to_date: str | None) -> str:
    if from_date or to_date:
        return "historical"
    mode = str(config.get("mode") or "full").strip().lower()
    if mode not in MODES:
        raise ValueError(f"{plan.entity}: unknown mode {mode!r}")
    if mode == "incremental" and not plan.incremental_capable:
        return "full"
    return mode


@dataclass
class ReadResult:
    total_records: int = 0
    batches: int = 0
    source_clock: Watermark | None = None
    companies: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_entity(
    plan: EntityPlan,
    connection: Connection,
    companies: Sequence[Company],
    *,
    mode: str,
    get_watermark: GetWatermark,
    update_watermark: UpdateWatermark,
    write_batch: WriteBatch,
    from_date: str | None = None,
    to_date: str | None = None,
    batch_size: int = BATCH_SIZE,
    buffer_minutes: int = WATERMARK_BUFFER_MINUTES,
) -> ReadResult:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "historical" and not plan.date_field:
        raise ValueError(f"{plan.entity}: historical mode needs a date_field")

    result = ReadResult()
    buffer: list[dict[str, Any]] = []
    bootstrap = mode == "incremental"

    def _flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        write_batch(buffer)
        result.batches += 1
        buffer = []

    clock_cap = (
        Watermark.from_stamp(connection.source_now())
        if plan.watermark_kind == WATERMARK_UPDATE_TS
        else None
    )
    result.source_clock = clock_cap

    for company in companies:
        key = watermark_key(plan.entity, company.alias)
        stored = get_watermark(key) if mode == "incremental" else None
        watermark = Watermark.parse(plan.watermark_kind, stored) if stored else None
        if watermark is not None:
            bootstrap = False
        if stored and watermark is None:
            logger.warning(
                "watermark for %s is not parseable; reading the whole table for this company",
                key,
            )
        effective = watermark.with_backoff(buffer_minutes) if watermark else None

        after_key = None
        company_count = 0
        company_max: Watermark | None = None
        while True:
            sql, params = select_sql(
                plan,
                company.schema,
                mode=mode,
                watermark=effective,
                after_key=after_key,
                from_date=from_date,
                to_date=to_date,
            )
            columns, rows = connection.fetch_all(sql, params)
            if not rows:
                break
            records = rows_to_records(plan, company.alias, columns, rows)
            page_max = next_watermark(plan, records)
            if page_max is not None and (company_max is None or company_max < page_max):
                company_max = page_max

            buffer.extend(records)
            result.total_records += len(records)
            company_count += len(records)
            if len(buffer) >= batch_size:
                _flush()

            after_key = keyset_cursor(plan, records[-1])
            if after_key is None or len(rows) < plan.page_size:
                break

        _flush()
        new_text = None
        if mode in ("incremental", "full") and company_max is not None:
            if clock_cap is not None and clock_cap < company_max:
                company_max = clock_cap
            if watermark is None or watermark < company_max:
                update_watermark(key, company_max.text())
                new_text = company_max.text()
        result.companies[company.alias] = {
            "record_count": company_count,
            "watermark_used": stored,
            "watermark_updated_to": new_text,
        }

    if result.total_records == 0 and (mode != "incremental" or bootstrap):
        write_batch([])
        result.batches += 1

    return result
