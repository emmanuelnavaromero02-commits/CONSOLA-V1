from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from omega_lakehouse import LakehouseStorage, storage_from_env

from app.core.inegi_client import INEGIClient
from app.services.bronze_records import metadata_rows, observation_rows, utc_now_iso
from app.services.bronze_writer import BronzeWriter
from app.services.config_loader import SeriesConfig, validate_requested_series
from app.services.date_windows import build_windows, parse_date
from app.services.hash_utils import canonical_json_bytes, request_hash, sha256_bytes
from app.services.preflight_service import validate_metadata_payload
from app.services.watermark_service import WatermarkStore, default_store

DEFAULT_START = date(2018, 1, 1)
CARTRIDGE_ID = "inegi"
SOURCE_AUTHORITY = "INEGI"


def run_series_observations(
    *,
    tenant_id: str,
    workspace_id: str,
    mode: str = "incremental",
    from_date: str | None = None,
    to_date: str | None = None,
    series_ids: list[str] | None = None,
    run_id: str | None = None,
    conn_id: str | None = None,
    security_context: str | None = None,
    client: INEGIClient | None = None,
    storage: LakehouseStorage | None = None,
    watermarks: WatermarkStore | None = None,
) -> dict[str, Any]:
    _require_scope(tenant_id, workspace_id)
    run_id = run_id or str(uuid.uuid4())
    mode = "full" if mode == "full" else "incremental"
    selected = validate_requested_series(series_ids)
    client = client or INEGIClient(conn_id=conn_id, security_context=security_context)
    storage = storage or storage_from_env()
    watermarks = watermarks or default_store()

    source_by_id = {item.series_id: item.source_dataset for item in selected}
    metadata_payload = client.get_metadata([item.series_id for item in selected], source_by_id=source_by_id)
    validate_metadata_payload(metadata_payload, selected)
    before = watermarks.get_many(tenant_id, workspace_id, [item.series_id for item in selected])
    end_date = parse_date(to_date) or date.today()
    windows = build_windows(
        selected,
        before,
        mode=mode,
        from_date=parse_date(from_date),
        to_date=end_date,
        default_start=DEFAULT_START,
    )
    writer = BronzeWriter(storage)
    metadata_result = _write_metadata(
        writer,
        metadata_payload,
        selected,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    total_rows = 0
    after: dict[str, str] = {}
    observation_batches = []
    for window in windows:
        payload = client.get_observations(
            [item.series_id for item in window.series],
            window.from_date.isoformat(),
            window.to_date.isoformat(),
            source_by_id=source_by_id,
        )
        rows = _observation_rows(client, payload, window.series, tenant_id, workspace_id, run_id)
        total_rows += len(rows)
        for series_id, watermark in _max_dates(rows).items():
            after[series_id] = watermark
        observation_batches.append(
            _write_observations(
                writer,
                payload,
                rows,
                window.series,
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
        "entity": "series_observations",
        "record_count": total_rows,
        "metadata_batch": metadata_result,
        "observation_batches": observation_batches,
        "watermark_updated_to": after,
    }


def _write_metadata(
    writer: BronzeWriter,
    payload: dict[str, Any],
    series: tuple[SeriesConfig, ...],
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    retrieved_at = utc_now_iso()
    ids = [item.series_id for item in series]
    req_hash = request_hash("metadata", ",".join(sorted(ids)), "es")
    p_hash = sha256_bytes(canonical_json_bytes(payload))
    provenance = _provenance("series_metadata", req_hash, p_hash, run_id, tenant_id, workspace_id)
    rows = metadata_rows(payload, retrieved_at=retrieved_at, provenance=provenance)
    return writer.write_batch(
        entity="series_metadata",
        rows=rows,
        source_payload=payload,
        manifest=_manifest("series_metadata", tenant_id, workspace_id, run_id, req_hash, p_hash, ids),
    )


def _write_observations(
    writer: BronzeWriter,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    series: tuple[SeriesConfig, ...],
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    from_date: str,
    to_date: str,
    watermarks_before: dict[str, str | None],
    watermarks_after: dict[str, str],
) -> dict[str, Any]:
    ids = [item.series_id for item in series]
    req_hash = request_hash("observations", ",".join(sorted(ids)), from_date, to_date, "es")
    p_hash = sha256_bytes(canonical_json_bytes(payload))
    batch_run_id = f"{run_id}-{req_hash[:12]}"
    rows = _stamp_observation_provenance(rows, req_hash, p_hash, batch_run_id)
    manifest = _manifest("series_observations", tenant_id, workspace_id, batch_run_id, req_hash, p_hash, ids)
    manifest.update(
        {
            "from_date": from_date,
            "to_date": to_date,
            "watermarks_before": {series_id: watermarks_before.get(series_id) for series_id in ids},
            "watermarks_after": {series_id: watermarks_after.get(series_id) for series_id in ids},
            "series_overlap_days": {item.series_id: item.overlap_days for item in series},
        }
    )
    return writer.write_batch(
        entity="series_observations",
        rows=rows,
        source_payload=payload,
        manifest=manifest,
    )


def _observation_rows(
    client: INEGIClient,
    payload: dict[str, Any],
    series: tuple[SeriesConfig, ...],
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    p_hash = sha256_bytes(canonical_json_bytes(payload))
    req_hash = request_hash("observations", ",".join(sorted(item.series_id for item in series)), "pending")
    provenance = _provenance("series_observations", req_hash, p_hash, run_id, tenant_id, workspace_id)
    configs = {item.series_id: item for item in series}
    return observation_rows(payload, configs=configs, retrieved_at=utc_now_iso(), provenance=provenance)


def _stamp_observation_provenance(
    rows: list[dict[str, Any]],
    req_hash: str,
    payload_hash: str,
    batch_run_id: str,
) -> list[dict[str, Any]]:
    stamped = []
    for row in rows:
        updated = dict(row)
        updated["_request_hash"] = req_hash
        updated["_payload_hash"] = payload_hash
        updated["_run_id"] = batch_run_id
        stamped.append(updated)
    return stamped


def _manifest(
    entity: str,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    req_hash: str,
    payload_hash: str,
    series_ids: list[str],
) -> dict[str, Any]:
    load_date = date.today().isoformat()
    final_prefix = (
        f"raw/inegi/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/"
        f"load_date={load_date}/batch_id={run_id}/"
    )
    staging_prefix = (
        f"raw/inegi/_staging/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/"
        f"load_date={load_date}/batch_id={run_id}/"
    )
    return {
        "manifest_version": 1,
        "status": "pending",
        "cartridge_id": CARTRIDGE_ID,
        "entity": entity,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "load_date": load_date,
        "created_at": utc_now_iso(),
        "source_authority": SOURCE_AUTHORITY,
        "series_ids": sorted(series_ids),
        "request_hash": req_hash,
        "payload_hash": payload_hash,
        "final_prefix": final_prefix,
        "staging_prefix": staging_prefix,
    }


def _provenance(entity: str, req_hash: str, payload_hash: str, run_id: str, tenant_id: str, workspace_id: str) -> dict[str, str]:
    return {
        "_source_authority": SOURCE_AUTHORITY,
        "_source_url": "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml",
        "_source_host": "www.inegi.org.mx",
        "_endpoint": entity,
        "_request_hash": req_hash,
        "_payload_hash": payload_hash,
        "_run_id": run_id,
        "_tenant_id": tenant_id,
        "_workspace_id": workspace_id,
    }


def _max_dates(rows: list[dict[str, Any]]) -> dict[str, str]:
    values: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        values[str(row["series_id"])].append(str(row["observation_date"]))
    return {series_id: max(dates) for series_id, dates in values.items() if dates}


def _require_scope(tenant_id: str, workspace_id: str) -> None:
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required")
