from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from omega_lakehouse import LakehouseStorage, storage_from_env

from app.core.sec_client import SECClient
from app.services.bronze_records import fact_rows, metadata_rows, utc_now_iso
from app.services.bronze_writer import BronzeWriter
from app.services.config_loader import CompanyConfig, validate_requested_companies
from app.services.date_windows import build_windows, parse_date
from app.services.hash_utils import canonical_json_bytes, request_hash, sha256_bytes
from app.services.preflight_service import validate_facts_payload, validate_metadata_payload
from app.services.watermark_service import WatermarkStore, default_store

DEFAULT_START = date(2018, 1, 1)
CARTRIDGE_ID = "sec_edgar"
SOURCE_AUTHORITY = "SEC"


def run_company_facts(
    *,
    tenant_id: str,
    workspace_id: str,
    mode: str = "incremental",
    from_date: str | None = None,
    to_date: str | None = None,
    ciks: list[str] | None = None,
    run_id: str | None = None,
    conn_id: str | None = None,
    security_context: str | None = None,
    client: SECClient | None = None,
    storage: LakehouseStorage | None = None,
    watermarks: WatermarkStore | None = None,
) -> dict[str, Any]:
    _require_scope(tenant_id, workspace_id)
    run_id = run_id or str(uuid.uuid4())
    mode = "full" if mode == "full" else "incremental"
    selected = validate_requested_companies(ciks)
    client = client or SECClient(conn_id=conn_id, security_context=security_context)
    storage = storage or storage_from_env()
    watermarks = watermarks or default_store()

    metadata_payload = client.get_metadata([item.cik for item in selected])
    validate_metadata_payload(metadata_payload, selected)
    watermark_keys = _watermark_keys(selected)
    before = watermarks.get_many(tenant_id, workspace_id, watermark_keys)
    windows = build_windows(
        selected,
        before,
        mode=mode,
        from_date=parse_date(from_date),
        to_date=parse_date(to_date) or date.today(),
        default_start=DEFAULT_START,
    )
    writer = BronzeWriter(storage)
    metadata_result = _write_metadata(writer, metadata_payload, selected, tenant_id, workspace_id, run_id)
    total_rows = 0
    after: dict[str, str] = {}
    fact_batches = []
    for window in windows:
        payload = client.get_company_facts([item.cik for item in window.companies])
        validate_facts_payload(payload, window.companies)
        rows = _fact_rows(payload, window.companies, tenant_id, workspace_id, run_id, window.from_date, window.to_date)
        total_rows += len(rows)
        after.update(_max_dates(rows))
        fact_batches.append(
            _write_facts(
                writer,
                payload,
                rows,
                window.companies,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=run_id,
                from_date=window.from_date.isoformat(),
                to_date=window.to_date.isoformat(),
                watermarks_before=before,
                watermarks_after=after,
            )
        )
    if after:
        watermarks.update_many(tenant_id, workspace_id, after, run_id=run_id)
    return {
        "status": "success",
        "run_id": run_id,
        "entity": "company_facts",
        "record_count": total_rows,
        "metadata_batch": metadata_result,
        "fact_batches": fact_batches,
        "watermark_updated_to": after,
    }


def _write_metadata(
    writer: BronzeWriter,
    payload: dict[str, Any],
    companies: tuple[CompanyConfig, ...],
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    ids = [item.cik for item in companies]
    req_hash = request_hash("metadata", ",".join(sorted(ids)), "sec_edgar")
    p_hash = sha256_bytes(canonical_json_bytes(payload))
    provenance = _provenance("company_metadata", req_hash, p_hash, run_id, tenant_id, workspace_id)
    rows = metadata_rows(payload, retrieved_at=utc_now_iso(), provenance=provenance)
    return writer.write_batch(
        entity="company_metadata",
        rows=rows,
        source_payload=payload,
        manifest=_manifest("company_metadata", tenant_id, workspace_id, run_id, req_hash, p_hash, ids),
    )


def _write_facts(
    writer: BronzeWriter,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    companies: tuple[CompanyConfig, ...],
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    from_date: str,
    to_date: str,
    watermarks_before: dict[str, str | None],
    watermarks_after: dict[str, str],
) -> dict[str, Any]:
    ids = [item.cik for item in companies]
    req_hash = request_hash("company_facts", ",".join(sorted(ids)), from_date, to_date)
    p_hash = sha256_bytes(canonical_json_bytes(payload))
    batch_run_id = f"{run_id}-{req_hash[:12]}"
    rows = _stamp(rows, req_hash, p_hash, batch_run_id)
    manifest = _manifest("company_facts", tenant_id, workspace_id, batch_run_id, req_hash, p_hash, ids)
    manifest.update(
        {
            "from_date": from_date,
            "to_date": to_date,
            "watermarks_before": {key: watermarks_before.get(key) for key in _watermark_keys(companies)},
            "watermarks_after": {key: watermarks_after.get(key) for key in _watermark_keys(companies)},
        }
    )
    return writer.write_batch(entity="company_facts", rows=rows, source_payload=payload, manifest=manifest)


def _fact_rows(
    payload: dict[str, Any],
    companies: tuple[CompanyConfig, ...],
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    p_hash = sha256_bytes(canonical_json_bytes(payload))
    req_hash = request_hash("company_facts", ",".join(sorted(item.cik for item in companies)), "pending")
    provenance = _provenance("company_facts", req_hash, p_hash, run_id, tenant_id, workspace_id)
    return fact_rows(
        payload,
        configs={item.cik: item for item in companies},
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        retrieved_at=utc_now_iso(),
        provenance=provenance,
    )


def _manifest(entity: str, tenant_id: str, workspace_id: str, run_id: str, req_hash: str, payload_hash: str, ciks: list[str]) -> dict[str, Any]:
    load_date = date.today().isoformat()
    final_prefix = f"raw/sec_edgar/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/load_date={load_date}/batch_id={run_id}/"
    staging_prefix = f"raw/sec_edgar/_staging/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/load_date={load_date}/batch_id={run_id}/"
    return {
        "manifest_version": 1,
        "status": "pending",
        "cartridge_id": CARTRIDGE_ID,
        "entity": entity,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "load_date": load_date,
        "run_id": run_id,
        "request_hash": req_hash,
        "payload_hash": payload_hash,
        "ciks": ciks,
        "final_prefix": final_prefix,
        "staging_prefix": staging_prefix,
    }


def _provenance(entity: str, request_hash_value: str, payload_hash: str, run_id: str, tenant_id: str, workspace_id: str) -> dict[str, str]:
    return {
        "_source_authority": SOURCE_AUTHORITY,
        "_source_host": "data.sec.gov",
        "_entity": entity,
        "_request_hash": request_hash_value,
        "_payload_hash": payload_hash,
        "_run_id": run_id,
        "_tenant_id": tenant_id,
        "_workspace_id": workspace_id,
    }


def _stamp(rows: list[dict[str, Any]], req_hash: str, payload_hash: str, batch_run_id: str) -> list[dict[str, Any]]:
    stamped = []
    for row in rows:
        updated = dict(row)
        updated["_request_hash"] = req_hash
        updated["_payload_hash"] = payload_hash
        updated["_run_id"] = batch_run_id
        stamped.append(updated)
    return stamped


def _max_dates(rows: list[dict[str, Any]]) -> dict[str, str]:
    values: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        key = f"{row.get('cik')}:{row.get('metric_name')}"
        if row.get("end_date"):
            values[key].append(str(row["end_date"]))
    return {key: max(items) for key, items in values.items() if items}


def _watermark_keys(companies: tuple[CompanyConfig, ...]) -> list[str]:
    return [f"{company.cik}:{fact.metric_name}" for company in companies for fact in company.facts]


def _require_scope(tenant_id: str, workspace_id: str) -> None:
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required")
