from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.sap_client import SAPClientError, SapSfClient
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run
from app.services.watermark_service import get_watermark, update_watermark

# Flush a parquet file every BATCH_SIZE rows. Buffer is drained after every
# OData page is appended, so memory stays bounded regardless of total volume —
# important for S/4HANA entities like JournalEntryItem (millions of rows).
BATCH_SIZE = 10_000
WATERMARK_BUFFER_MINUTES = 5
CARTRIDGE_ID = "sap_successfactors"
DEFAULT_EFFECTIVE_FROM_DATE = "1900-01-01"
DEFAULT_EFFECTIVE_TO_DATE = "9999-12-31"
SAP_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d+)?\)/$")
logger = logging.getLogger(__name__)


def _watermark_value_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    if SAP_DATE_RE.match(text):
        return "sap_date_ms"
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return "numeric"
    if re.match(r"^\d{4}-\d{2}-\d{2}([T\s]\d{2}:\d{2}:\d{2})?", text):
        return "iso8601"
    return "unknown"


def _parse_watermark_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = SAP_DATE_RE.match(text)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        try:
            number = float(text)
            # SAP payloads usually use milliseconds. Accept seconds for small
            # epoch values so old watermarks do not become year 53900 dates.
            if abs(number) > 9_999_999_999:
                number = number / 1000
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        normalized = text.replace("Z", "+00:00").replace(" ", "T")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _successfactors_datetime_literal(value: Any) -> str | None:
    parsed = _parse_watermark_datetime(value)
    if parsed is None:
        return None
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        return None
    # SuccessFactors OData v2 accepts datetime literals for Edm.DateTime
    # filters. Do not send raw SAP /Date(ms)/ payloads as quoted strings.
    return "datetime'" + parsed.strftime("%Y-%m-%dT%H:%M:%S") + "'"


def _build_incremental_filter(
    *,
    entity: str,
    watermark_field: str | None,
    watermark_value: str | None,
) -> tuple[str | None, dict[str, Any]]:
    value_type = _watermark_value_type(watermark_value)
    plan: dict[str, Any] = {
        "entity": entity,
        "watermark_field": watermark_field,
        "watermark_value_type": value_type,
        "filter_strategy": "none",
        "fallback_reason": "",
        "generated_filter_sanitized": "",
    }
    if not watermark_field or not watermark_value:
        plan["fallback_reason"] = "no_watermark"
        return None, plan
    literal = _successfactors_datetime_literal(watermark_value)
    if literal is None:
        plan["filter_strategy"] = "full_snapshot"
        plan["fallback_reason"] = (
            "future_or_unparseable_watermark"
            if value_type != "empty"
            else "no_watermark"
        )
        return None, plan
    filter_expr = f"{watermark_field} gt {literal}"
    plan["filter_strategy"] = "server_filter"
    plan["generated_filter_sanitized"] = filter_expr
    return filter_expr, plan


def _is_incremental_filter_rejected(exc: Exception) -> bool:
    if not isinstance(exc, SAPClientError):
        return False
    text = str(exc).lower()
    if "oauth/token" in text or "token request" in text or "saml bearer" in text:
        return False
    return ("400" in text or "bad request" in text) and (
        "$filter" in text
        or "filter" in text
        or "get " in text
    )


def _log_odata_plan(
    *,
    entity: str,
    mode: str,
    config: dict[str, Any],
    plan: dict[str, Any],
    from_date: str | None,
    to_date: str | None,
    select_fields: list[str],
) -> None:
    logger.info(
        "sap_successfactors_odata_plan %s",
        json.dumps(
            {
                "entity": entity,
                "odata_entity": config.get("odata_entity", entity),
                "mode": mode,
                "watermark_field": config.get("watermark_field"),
                "watermark_value_type": plan.get("watermark_value_type"),
                "filter_strategy": plan.get("filter_strategy"),
                "fallback_reason": plan.get("fallback_reason"),
                "generated_filter_sanitized": plan.get("generated_filter_sanitized"),
                "effective_dated": bool(config.get("effective_dated")),
                "from_date": from_date,
                "to_date": to_date,
                "select_fields_count": len(select_fields),
            },
            sort_keys=True,
        ),
    )


def _max_watermark(rows: list[dict[str, Any]], watermark_field: str | None) -> str | None:
    if not rows or not watermark_field:
        return None
    values = [r[watermark_field] for r in rows if r.get(watermark_field) is not None]
    return max(values) if values else None


def _page_signature(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    sample = [len(rows), rows[0], rows[-1]]
    payload = json.dumps(sample, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _apply_watermark_filter(
    rows: list[dict[str, Any]],
    watermark_field: str,
    watermark_value: str,
) -> list[dict[str, Any]]:
    watermark_dt = _parse_watermark_datetime(watermark_value)
    if watermark_dt is not None:
        filtered: list[dict[str, Any]] = []
        for row in rows:
            row_dt = _parse_watermark_datetime(row.get(watermark_field))
            if row_dt is not None and row_dt > watermark_dt:
                filtered.append(row)
        return filtered
    return [r for r in rows if str(r.get(watermark_field, "")) > watermark_value]


def _apply_date_range_filter(
    rows: list[dict[str, Any]],
    date_field: str | None,
    from_date: str | None,
    to_date: str | None,
) -> list[dict[str, Any]]:
    if not date_field or (not from_date and not to_date):
        return rows
    out = rows
    if from_date:
        out = [r for r in out if str(r.get(date_field, "")) >= from_date]
    if to_date:
        out = [r for r in out if str(r.get(date_field, "")) <= to_date]
    return out


def _effective_date_window(
    config: dict[str, Any],
    from_date: str | None,
    to_date: str | None,
) -> tuple[str | None, str | None]:
    """Return OData fromDate/toDate for effective-dated SuccessFactors entities."""
    if not config.get("effective_dated"):
        return from_date, to_date
    return (
        from_date
        or config.get("effective_from_date")
        or config.get("default_from_date")
        or DEFAULT_EFFECTIVE_FROM_DATE,
        to_date
        or config.get("effective_to_date")
        or config.get("default_to_date")
        or DEFAULT_EFFECTIVE_TO_DATE,
    )


def run_entity(
    config: dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    entity = config["entity"]
    watermark_field = config.get("watermark_field")
    page_size = config.get("page_size", 200)
    raw_select_fields = config.get("select_fields", [])
    if isinstance(raw_select_fields, (list, tuple)):
        select_fields = list(raw_select_fields)
    elif isinstance(raw_select_fields, str) and raw_select_fields:
        select_fields = [raw_select_fields]
    else:
        select_fields = []
    date_field = config.get("date_field")

    if from_date or to_date:
        mode = "historical"
    else:
        mode = config.get("mode", "full")
    odata_from_date, odata_to_date = _effective_date_window(config, from_date, to_date)
    security_context = config.get("security_context")
    conn_id = (str(config.get("conn_id") or config.get("connection_id") or "").strip() or None)
    serialized_security_context = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
    idempotency_key = str(config.get("idempotency_key") or "").strip() or None
    parent_idempotency_key = str(config.get("parent_idempotency_key") or "").strip() or None
    if not conn_id:
        raise ValueError(
            f"SAP SuccessFactors entity {entity} requires entity_config.connection_id "
            "or an explicit conn_id; no environment/default credential fallback is allowed."
        )
    raw_expected_select_fields = config.get("expected_select_fields") or select_fields
    expected_select_fields = (
        list(raw_expected_select_fields)
        if isinstance(raw_expected_select_fields, (list, tuple))
        else [str(raw_expected_select_fields)]
        if raw_expected_select_fields
        else []
    )
    expected_columns = list(dict.fromkeys([
        *(expected_select_fields or []),
        *([watermark_field] if watermark_field else []),
        *([date_field] if date_field else []),
    ]))

    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=entity,
        run_type=mode,
        status="running",
        started_at=datetime.now(timezone.utc),
        requested_run_id=idempotency_key,
    )

    try:
        client = SapSfClient(conn_id=conn_id, security_context=serialized_security_context)

        watermark: str | None = None
        if mode == "incremental" and watermark_field:
            watermark = get_watermark(entity)
        filter_expr, filter_plan = _build_incremental_filter(
            entity=entity,
            watermark_field=watermark_field,
            watermark_value=watermark,
        )
        watermark_for_client_filter = watermark if filter_expr else None
        filter_fallback_reason = str(filter_plan.get("fallback_reason") or "")
        filter_retried_as_full_snapshot = False
        _log_odata_plan(
            entity=entity,
            mode=mode,
            config=config,
            plan=filter_plan,
            from_date=odata_from_date,
            to_date=odata_to_date,
            select_fields=select_fields,
        )

        # Streaming buffer — flushed every BATCH_SIZE rows.
        buffer: list[dict[str, Any]] = []
        offset = 0
        batch_num = 0
        storage_uri = ""
        total_records = 0
        max_wm: str | None = None
        seen_page_signatures: set[str] = set()
        pagination_status: str | None = None
        pagination_warning: str | None = None

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
                watermark_field=watermark_field,
                expected_columns=expected_columns,
                security_context=security_context,
            )
            batch_num += 1
            buffer = []

        while True:
            try:
                page = client.fetch_entity(
                    entity=config.get("odata_entity", entity),
                    select=select_fields,
                    page_size=page_size,
                    skip=offset,
                    filter_expr=filter_expr,
                    from_date=odata_from_date,
                    to_date=odata_to_date,
                )
            except Exception as exc:
                if (
                    mode == "incremental"
                    and filter_expr
                    and not filter_retried_as_full_snapshot
                    and offset == 0
                    and total_records == 0
                    and _is_incremental_filter_rejected(exc)
                ):
                    filter_retried_as_full_snapshot = True
                    filter_fallback_reason = "incremental_filter_rejected_full_snapshot"
                    filter_plan = {
                        **filter_plan,
                        "filter_strategy": "full_snapshot",
                        "fallback_reason": filter_fallback_reason,
                        "generated_filter_sanitized": "",
                    }
                    filter_expr = None
                    watermark_for_client_filter = None
                    logger.warning(
                        "sap_successfactors_incremental_filter_rejected entity=%s odata_entity=%s fallback=full_snapshot error_type=%s",
                        entity,
                        config.get("odata_entity", entity),
                        type(exc).__name__,
                    )
                    continue
                raise
            server_page_len = len(page)
            if not page:
                break
            signature = _page_signature(page)
            if offset and signature in seen_page_signatures:
                pagination_status = "repeated_page_truncated"
                pagination_warning = (
                    f"server returned a repeated page for entity={entity} "
                    f"at skip={offset}; stopping extraction to avoid an infinite loop"
                )
                logger.warning("sap_successfactors_pagination_repeated_page %s", pagination_warning)
                break
            seen_page_signatures.add(signature)

            # Belt-and-suspenders client-side filters (the OData server
            # MIGHT have ignored $filter — re-apply locally).
            if mode == "incremental" and watermark_for_client_filter and watermark_field:
                page = _apply_watermark_filter(page, watermark_field, watermark_for_client_filter)
            page = _apply_date_range_filter(page, date_field, from_date, to_date)

            page_wm = _max_watermark(page, watermark_field)
            if page_wm and (max_wm is None or page_wm > max_wm):
                max_wm = page_wm

            buffer.extend(page)
            total_records += len(page)
            offset += page_size

            if len(buffer) >= BATCH_SIZE:
                _flush_buffer()
            if server_page_len < page_size:
                break

        # Drain any remainder. If we never received any rows, write an empty
        # parquet so consumers can still observe a (zero-row) Bronze artifact.
        if buffer or total_records == 0:
            _flush_buffer(allow_empty=total_records == 0)

        if mode == "incremental" and watermark_field and max_wm:
            safe_watermark = max_wm
            dt = _parse_watermark_datetime(max_wm)
            if dt is not None:
                safe_watermark = (
                    dt - timedelta(minutes=WATERMARK_BUFFER_MINUTES)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")

            update_watermark(
                entity_name=entity,
                watermark_field=watermark_field,
                last_watermark_value=safe_watermark,
                last_run_id=run_id,
            )

        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=total_records,
            storage_uri=storage_uri,
            finished_at=datetime.now(timezone.utc),
        )

        return {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "parent_idempotency_key": parent_idempotency_key,
            "entity": entity,
            "mode": mode,
            "record_count": total_records,
            "storage_uri": storage_uri,
            "watermark_used": watermark,
            "watermark_updated_to": max_wm,
            "batches": batch_num,
            "status": "success",
            "incremental_filter_strategy": filter_plan.get("filter_strategy"),
            "incremental_fallback_reason": filter_fallback_reason,
            "retried_as_full_snapshot": filter_retried_as_full_snapshot,
            "metadata_status": config.get("metadata_status") or pagination_status,
            "metadata_pruned_fields": config.get("metadata_pruned_fields") or [],
            "metadata_missing_watermark_field": config.get("metadata_missing_watermark_field"),
            "metadata_missing_date_field": config.get("metadata_missing_date_field"),
            "pagination_status": pagination_status,
            "pagination_warning": pagination_warning,
        }

    except Exception as exc:
        fail_run(
            run_id=run_id,
            error_message=str(exc),
            finished_at=datetime.now(timezone.utc),
        )
        raise
