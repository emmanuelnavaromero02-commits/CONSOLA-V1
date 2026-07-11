from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class SECInsufficientData(ValueError):
    pass


@dataclass(frozen=True)
class SECManifest:
    entity: str
    key: str
    parquet_key: str
    parquet_uri: str
    load_date: str
    run_id: str


FACT_CONFIG: tuple[dict[str, Any], ...] = (
    {"cik": "0001061736", "ticker": "FMX", "metric_name": "revenue", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0001061736", "ticker": "FMX", "metric_name": "profit_loss", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0001061736", "ticker": "FMX", "metric_name": "assets", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0001061736", "ticker": "FMX", "metric_name": "liabilities", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0000910631", "ticker": "KOF", "metric_name": "revenue", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0000910631", "ticker": "KOF", "metric_name": "profit_loss", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0000910631", "ticker": "KOF", "metric_name": "assets", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0000910631", "ticker": "KOF", "metric_name": "liabilities", "unit": "MXN", "freshness_sla_days": 550},
    {"cik": "0000021344", "ticker": "KO", "metric_name": "revenue", "unit": "USD", "freshness_sla_days": 550},
    {"cik": "0000021344", "ticker": "KO", "metric_name": "net_income", "unit": "USD", "freshness_sla_days": 550},
    {"cik": "0000021344", "ticker": "KO", "metric_name": "assets", "unit": "USD", "freshness_sla_days": 550},
)


def scope_from_context(user_context: dict | None) -> tuple[str, str]:
    ctx = user_context or {}
    tenant = str(ctx.get("tenant_id") or ctx.get("active_tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or ctx.get("active_workspace_id") or "").strip()
    if not tenant or not workspace:
        raise ValueError("SEC EDGAR materialization requires tenant_id and workspace_id")
    return tenant, workspace


def complete_manifests(storage: Any, entity: str, user_context: dict | None) -> list[SECManifest]:
    tenant, workspace = scope_from_context(user_context)
    prefix = f"raw/sec_edgar/{entity}/tenant_id={tenant}/workspace_id={workspace}/"
    manifests: list[SECManifest] = []
    for obj in storage.iter_list(prefix):
        key = str(getattr(obj, "key", "") or "")
        if not key.endswith("/manifest.json"):
            continue
        manifest = _load_manifest(storage, key)
        if not _is_complete_manifest(manifest, entity):
            continue
        parquet_key = _parquet_key(manifest, entity)
        if not parquet_key.startswith(str(manifest.get("final_prefix") or "")):
            continue
        manifests.append(
            SECManifest(
                entity=entity,
                key=key,
                parquet_key=parquet_key,
                parquet_uri=_uri_for(storage, parquet_key),
                load_date=str(manifest.get("load_date") or ""),
                run_id=str(manifest.get("run_id") or ""),
            )
        )
    return sorted(manifests, key=lambda item: (item.load_date, item.run_id, item.key))


def latest_manifest(manifests: list[SECManifest]) -> SECManifest | None:
    return manifests[-1] if manifests else None


def _load_manifest(storage: Any, key: str) -> dict[str, Any]:
    try:
        loaded = json.loads(storage.get_bytes(key).decode("utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_complete_manifest(manifest: dict[str, Any], entity: str) -> bool:
    return (
        manifest.get("status") == "complete"
        and manifest.get("cartridge_id") == "sec_edgar"
        and manifest.get("entity") == entity
        and str(manifest.get("final_prefix") or "").startswith(f"raw/sec_edgar/{entity}/")
    )


def _parquet_key(manifest: dict[str, Any], entity: str) -> str:
    expected_name = f"{entity}.parquet"
    for item in manifest.get("files") or []:
        if isinstance(item, dict) and item.get("name") == expected_name and item.get("key"):
            return str(item["key"])
    return f"{manifest.get('final_prefix') or ''}{expected_name}"


def _uri_for(storage: Any, key: str) -> str:
    if hasattr(storage, "uri_for"):
        return str(storage.uri_for(key))
    bucket = getattr(getattr(storage, "config", None), "bucket", "lakehouse")
    provider = getattr(getattr(storage, "config", None), "provider", "s3")
    return f"{'gs' if provider == 'gcs' else 's3'}://{bucket}/{key}"
