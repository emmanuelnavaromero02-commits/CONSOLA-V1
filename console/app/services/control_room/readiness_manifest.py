from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


MANIFEST_PATH = Path(__file__).with_name("data_readiness_manifest.yaml")
VALID_READINESS_STATES = {"partial", "stub"}


def load_data_readiness_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: manifest must be a mapping")
    if data.get("version") != 1:
        raise ValueError(f"{path}: unsupported data readiness manifest version")
    return data


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("manifest list field must be a list")
    return tuple(str(item) for item in value if str(item).strip())


def dataset_readiness_registry(path: Path = MANIFEST_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    registry: dict[tuple[str, str], dict[str, Any]] = {}
    for item in load_data_readiness_manifest(path).get("datasets", []):
        if not isinstance(item, dict):
            raise ValueError("dataset readiness entries must be mappings")
        cartridge = str(item.get("cartridge") or "").strip()
        dataset = str(item.get("dataset") or "").strip()
        readiness = str(item.get("readiness") or "").strip()
        reason = str(item.get("reason") or "").strip()
        blockers = _as_tuple(item.get("blockers"))
        if not cartridge or not dataset:
            raise ValueError("dataset readiness entries require cartridge and dataset")
        if readiness not in VALID_READINESS_STATES:
            raise ValueError(f"{cartridge}/{dataset}: invalid readiness {readiness!r}")
        if not reason or not blockers:
            raise ValueError(f"{cartridge}/{dataset}: reason and blockers are required")
        key = (cartridge, dataset)
        if key in registry:
            raise ValueError(f"{cartridge}/{dataset}: duplicate readiness entry")
        registry[key] = {
            "data_readiness": readiness,
            "reason": reason,
            "blockers": blockers,
            "warnings": _as_tuple(item.get("warnings")),
        }
    return registry


def knowledge_bit_readiness_registry(path: Path = MANIFEST_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    registry: dict[tuple[str, str], dict[str, Any]] = {}
    for item in load_data_readiness_manifest(path).get("knowledge_bits", []):
        if not isinstance(item, dict):
            raise ValueError("knowledge bit readiness entries must be mappings")
        cartridge = str(item.get("cartridge") or "").strip()
        kb_id = str(item.get("id") or "").strip()
        readiness = str(item.get("readiness") or "").strip()
        reason = str(item.get("reason") or "").strip()
        blockers = _as_tuple(item.get("blockers"))
        datasets = _as_tuple(item.get("datasets"))
        if not cartridge or not kb_id:
            raise ValueError("knowledge bit readiness entries require cartridge and id")
        if readiness not in VALID_READINESS_STATES:
            raise ValueError(f"{cartridge}/{kb_id}: invalid readiness {readiness!r}")
        if not reason or not blockers or not datasets:
            raise ValueError(f"{cartridge}/{kb_id}: reason, blockers, and datasets are required")
        key = (cartridge, kb_id)
        if key in registry:
            raise ValueError(f"{cartridge}/{kb_id}: duplicate readiness entry")
        registry[key] = {
            "data_readiness": readiness,
            "reason": reason,
            "blockers": blockers,
            "datasets": datasets,
        }
    return registry
