from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class BanxicoInsufficientData(ValueError):
    pass


@dataclass(frozen=True)
class BanxicoManifest:
    entity: str
    key: str
    parquet_key: str
    parquet_uri: str
    load_date: str
    run_id: str


SERIES_CONFIG: tuple[dict[str, Any], ...] = (
    {
        "series_id": "SF43718",
        "metric_name": "usd_mxn_fix",
        "unit": "mxn_per_usd",
        "frequency": "daily",
        "freshness_sla_days": 7,
        "expected_min": 5.0,
        "expected_max": 50.0,
    },
    {
        "series_id": "SF61745",
        "metric_name": "target_rate",
        "unit": "percent_annual",
        "frequency": "event",
        "freshness_sla_days": 120,
        "expected_min": 0.0,
        "expected_max": 30.0,
    },
    {
        "series_id": "SF60648",
        "metric_name": "tiie_28d",
        "unit": "percent_annual",
        "frequency": "daily",
        "freshness_sla_days": 7,
        "expected_min": 0.0,
        "expected_max": 50.0,
    },
    {
        "series_id": "SP68257",
        "metric_name": "udi_value",
        "unit": "mxn",
        "frequency": "daily",
        "freshness_sla_days": 7,
        "expected_min": 1.0,
        "expected_max": 50.0,
    },
)


def scope_from_context(user_context: dict | None) -> tuple[str, str]:
    ctx = user_context or {}
    tenant = str(ctx.get("tenant_id") or ctx.get("active_tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or ctx.get("active_workspace_id") or "").strip()
    if not tenant or not workspace:
        raise ValueError("Banxico materialization requires tenant_id and workspace_id")
    return tenant, workspace


def complete_manifests(storage: Any, entity: str, user_context: dict | None) -> list[BanxicoManifest]:
    tenant, workspace = scope_from_context(user_context)
    prefix = f"raw/banxico/{entity}/tenant_id={tenant}/workspace_id={workspace}/"
    manifests: list[BanxicoManifest] = []
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
            BanxicoManifest(
                entity=entity,
                key=key,
                parquet_key=parquet_key,
                parquet_uri=_uri_for(storage, parquet_key),
                load_date=str(manifest.get("load_date") or ""),
                run_id=str(manifest.get("run_id") or ""),
            )
        )
    return sorted(manifests, key=lambda item: (item.load_date, item.run_id, item.key))


def parquet_uris(storage: Any, entity: str, user_context: dict | None) -> list[str]:
    manifests = complete_manifests(storage, entity, user_context)
    if not manifests:
        raise BanxicoInsufficientData(f"no complete Banxico manifests for {entity}")
    return [item.parquet_uri for item in manifests]


def latest_manifest(manifests: list[BanxicoManifest]) -> BanxicoManifest | None:
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
        and manifest.get("cartridge_id") == "banxico"
        and manifest.get("entity") == entity
        and str(manifest.get("final_prefix") or "").startswith(f"raw/banxico/{entity}/")
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
    scheme = "gs" if provider == "gcs" else "s3"
    return f"{scheme}://{bucket}/{key}"
