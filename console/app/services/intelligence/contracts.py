from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from app.services.intelligence.utils import CONTRACT_PATH, repo_root


def _packaged_contract_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "intelligence_contracts"


def _contract_candidates() -> Iterable[tuple[str, Path]]:
    seen: set[Path] = set()
    configured_dir = os.environ.get("INTELLIGENCE_CONTRACTS_DIR", "").strip()
    packaged_dirs = [
        Path(configured_dir) if configured_dir else None,
        _packaged_contract_dir(),
    ]
    for directory in packaged_dirs:
        if not directory:
            continue
        for path in sorted(directory.glob("*.yaml")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path.stem, path

    repo_paths = [
        repo_root() / "cartridges",
        Path("/app/cartridges"),
        Path("/cartridges"),
    ]
    for cartridges_dir in repo_paths:
        for path in sorted(cartridges_dir.glob(f"*/{CONTRACT_PATH}")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path.parts[-4], path


def load_contracts(cartridge_ids: set[str] | None = None) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for cartridge_id, path in _contract_candidates():
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
