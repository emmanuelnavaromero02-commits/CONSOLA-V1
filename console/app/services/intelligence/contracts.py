from __future__ import annotations

from typing import Any

import yaml
from fastapi import HTTPException

from app.services.intelligence.utils import CONTRACT_PATH, repo_root


def load_contracts(cartridge_ids: set[str] | None = None) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    cartridges_dir = repo_root() / "cartridges"
    for path in sorted(cartridges_dir.glob(f"*/{CONTRACT_PATH}")):
        cartridge_id = path.parts[-4]
        if cartridge_ids and cartridge_id not in cartridge_ids:
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise HTTPException(500, f"invalid intelligence contract: {cartridge_id}") from exc
        if not isinstance(raw, dict):
            continue
        raw["cartridge"] = str(raw.get("cartridge") or cartridge_id)
        raw["_path"] = str(path)
        contracts.append(raw)
    return contracts


def validate_metric(contract: dict[str, Any], metric: dict[str, Any]) -> None:
    required = ("id", "dataset", "value_field", "time_field", "entity")
    missing = [field for field in required if not metric.get(field)]
    if missing:
        cartridge = contract.get("cartridge") or "unknown"
        raise HTTPException(500, f"intelligence metric missing {','.join(missing)} in {cartridge}")
    entity = metric.get("entity")
    if not isinstance(entity, dict) or not entity.get("id_field"):
        raise HTTPException(500, f"intelligence metric entity is invalid in {metric.get('id')}")


def configured_horizons(metric: dict[str, Any], requested: list[int] | None = None) -> list[int]:
    if requested is not None:
        return sorted({day for day in requested if day > 0})
    prediction = metric.get("prediction") if isinstance(metric.get("prediction"), dict) else {}
    if prediction.get("enabled") is False:
        return []
    values = prediction.get("horizon_days")
    if not isinstance(values, list):
        return []
    horizons: set[int] = set()
    for value in values:
        try:
            horizon = int(value)
        except (TypeError, ValueError):
            continue
        if horizon > 0:
            horizons.add(horizon)
    return sorted(horizons)


def contract_sources(contract: dict[str, Any], metric: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for value in contract.get("external_sources", []), metric.get("external_sources", []):
        if isinstance(value, list):
            sources.extend(item for item in value if isinstance(item, dict))
    return sources
