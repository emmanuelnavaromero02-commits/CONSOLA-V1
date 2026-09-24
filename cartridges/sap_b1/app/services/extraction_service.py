"""Read Business One tables company by company into Bronze parquet.

One run covers one entity across every configured company. Rows carry
``_company`` (the alias) and ``_source_updated_at`` (the header stamp) so the
silver layer can keep the latest version of a document per company. The
watermark is tracked per ``entity@alias`` in ``entity_watermarks``.

Each company is flushed and its watermark committed before the next company
starts: a schema that fails half-way through the group never makes the
others re-read what they already delivered. The watermark never advances
past the source clock read at the start of the run, so a document edited
behind the cursor during a long read is still reached by the next cycle.

A run that reads zero changed rows on an incremental cycle is a success
with zero rows and writes no file: nothing changed. A connection or query
failure raises after the run is marked ``failed``; it is never reported as
zero rows.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.b1_source import B1Client
from app.services.b1_queries import (
    MODES,
    WATERMARK_UPDATE_TS,
    EntityPlan,
    Watermark,
    arrow_schema,
    keyset_cursor,
    next_watermark,
    plan_from_config,
    rows_to_records,
    select_sql,
    watermark_key,
)
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_service import get_watermark, update_watermark

logger = logging.getLogger(__name__)

# Flush a parquet file every BATCH_SIZE rows so memory stays bounded on the
# initial load (JDT1 and OINM run into the millions on a live company).
BATCH_SIZE = 10_000
# Re-read the last minutes before the stored stamp: a document committed
# while the previous cycle was reading may carry an older stamp than the
# newest row that cycle saw. Duplicates are resolved downstream.
WATERMARK_BUFFER_MINUTES = 5
CARTRIDGE_ID = "sap_b1"


def _effective_mode(config: dict[str, Any], plan: EntityPlan, from_date: str | None, to_date: str | None) -> str:
    if from_date or to_date:
        return "historical"
    mode = str(config.get("mode") or "full").strip().lower()
    if mode not in MODES:
        raise ValueError(f"{plan.entity}: unknown mode {mode!r}")
    if mode == "incremental" and not plan.incremental_capable:
        # Snapshot tables (OITW, OBTQ, ORTT...) have no stamp: read them whole.
        return "full"
    return mode


def run_entity(
    config: dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    plan = plan_from_config(config)
    entity = plan.entity
    mode = _effective_mode(config, plan, from_date, to_date)
    if mode == "historical" and not plan.date_field:
        raise ValueError(f"{entity}: historical mode needs a date_field")

    security_context = config.get("security_context")
    serialized_security_context = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
    expected_columns = list(plan.output_columns)
    schema = arrow_schema(plan)

    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=entity,
        run_type=mode,
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    try:
        client = B1Client(security_context=serialized_security_context)
        client.require_configured()

        buffer: list[dict[str, Any]] = []
        batch_num = 0
        storage_uri = ""
        total_records = 0
        companies: dict[str, dict[str, Any]] = {}
        # An incremental cycle with no stored watermark anywhere is the first
        # read of the table: a whole-table read, whatever the catalogue says.
        bootstrap = mode == "incremental"

        def _flush_buffer(allow_empty: bool = False) -> None:
            nonlocal buffer, batch_num, storage_uri
            if not buffer and not allow_empty:
                return
            batch_run_id = run_id if batch_num == 0 else f"{run_id}-b{batch_num}"
            storage_uri = write_parquet_and_upload(
                entity=entity,
                rows=buffer if buffer else [],
                run_id=batch_run_id,
                load_type=mode,
                watermark_field=plan.watermark_field,
                expected_columns=expected_columns,
                security_context=security_context,
                arrow_schema=schema,
            )
            batch_num += 1
            buffer = []

        with client.connection() as connection:
            # The clock of the source, once: the ceiling for every watermark
            # this run may record.
            clock_cap = (
                Watermark.from_stamp(connection.source_now())
                if plan.watermark_kind == WATERMARK_UPDATE_TS
                else None
            )

            for company in client.companies:
                key = watermark_key(entity, company.alias)
                stored = get_watermark(key) if mode == "incremental" else None
                watermark = Watermark.parse(plan.watermark_kind, stored) if stored else None
                if watermark is not None:
                    bootstrap = False
                if stored and watermark is None:
                    logger.warning(
                        "watermark for %s is not parseable; reading the whole table for this company",
                        key,
                    )
                effective = watermark.with_backoff(WATERMARK_BUFFER_MINUTES) if watermark else None

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
                    total_records += len(records)
                    company_count += len(records)
                    if len(buffer) >= BATCH_SIZE:
                        _flush_buffer()

                    after_key = keyset_cursor(plan, records[-1])
                    if after_key is None or len(rows) < plan.page_size:
                        break

                # This company's rows land before its watermark moves, and
                # before the next company is touched.
                _flush_buffer()
                new_text = None
                if mode in ("incremental", "full") and company_max is not None:
                    if clock_cap is not None and clock_cap < company_max:
                        company_max = clock_cap
                    if watermark is None or watermark < company_max:
                        update_watermark(
                            entity_name=key,
                            watermark_field=plan.watermark_field or "",
                            last_watermark_value=company_max.text(),
                            last_run_id=run_id,
                        )
                        new_text = company_max.text()
                companies[company.alias] = {
                    "record_count": company_count,
                    "watermark_used": stored,
                    "watermark_updated_to": new_text,
                }

        if total_records == 0 and (mode == "full" or bootstrap):
            # A whole-table read that found nothing (a full load, or the first
            # incremental read of a table that has no rows yet) still leaves a
            # zero-row artifact, so every silver reading the table finds a
            # file with the declared schema. A later incremental cycle with
            # no changes leaves nothing, on purpose.
            _flush_buffer(allow_empty=True)

        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=total_records,
            storage_uri=storage_uri,
            finished_at=datetime.now(timezone.utc),
        )

        return {
            "run_id": run_id,
            "entity": entity,
            "mode": mode,
            "record_count": total_records,
            "storage_uri": storage_uri,
            "batches": batch_num,
            "companies": companies,
            "status": "success",
        }

    except Exception as exc:
        fail_run(
            run_id=run_id,
            error_message=str(exc)[:4000],
            finished_at=datetime.now(timezone.utc),
        )
        raise
