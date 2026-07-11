from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@dataclass(frozen=True)
class SeriesConfig:
    series_id: str
    metric_name: str
    expected_title: str
    unit: str
    frequency: str
    expected_unit_code: str
    expected_frequency_code: str
    overlap_days: int
    expected_min: float
    expected_max: float


@dataclass(frozen=True)
class EntityConfig:
    entity: str
    mode: str
    series: tuple[SeriesConfig, ...]


def _read_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8")) or {}


def load_series_configs() -> tuple[SeriesConfig, ...]:
    entities = _read_yaml("entities.yaml").get("entities") or []
    observations = next((entry for entry in entities if entry.get("entity") == "series_observations"), None)
    if not observations:
        raise RuntimeError("series_observations entity is missing")
    series = observations.get("series") or []
    return tuple(
        SeriesConfig(
            series_id=str(item["series_id"]),
            metric_name=str(item["metric_name"]),
            expected_title=str(item["expected_title"]),
            unit=str(item["unit"]),
            frequency=str(item["frequency"]),
            expected_unit_code=str(item.get("expected_unit_code") or ""),
            expected_frequency_code=str(item.get("expected_frequency_code") or ""),
            overlap_days=int(item["overlap_days"]),
            expected_min=float(item["expected_min"]),
            expected_max=float(item["expected_max"]),
        )
        for item in series
    )


def series_by_id() -> dict[str, SeriesConfig]:
    return {item.series_id: item for item in load_series_configs()}


def validate_requested_series(requested: list[str] | None) -> tuple[SeriesConfig, ...]:
    known = series_by_id()
    if not requested:
        return tuple(known.values())
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise ValueError(f"series not allowlisted: {', '.join(sorted(unknown))}")
    return tuple(known[item] for item in requested)
