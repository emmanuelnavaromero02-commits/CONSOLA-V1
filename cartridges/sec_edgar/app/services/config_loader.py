from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@dataclass(frozen=True)
class FactConfig:
    metric_name: str
    taxonomy: str
    concept: str
    unit: str
    freshness_sla_days: int


@dataclass(frozen=True)
class CompanyConfig:
    cik: str
    ticker: str
    expected_name: str
    entity_type: str
    sic: str
    sic_description: str
    country_code: str
    overlap_days: int
    facts: tuple[FactConfig, ...]


def _read_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8")) or {}


def load_company_configs() -> tuple[CompanyConfig, ...]:
    entities = _read_yaml("entities.yaml").get("entities") or []
    facts_entity = next((entry for entry in entities if entry.get("entity") == "company_facts"), None)
    if not facts_entity:
        raise RuntimeError("company_facts entity is missing")
    return tuple(_company(item) for item in facts_entity.get("companies") or [])


def companies_by_cik() -> dict[str, CompanyConfig]:
    return {item.cik: item for item in load_company_configs()}


def validate_requested_companies(requested: list[str] | None) -> tuple[CompanyConfig, ...]:
    known = companies_by_cik()
    if not requested:
        return tuple(known.values())
    clean = [_cik10(item) for item in requested]
    unknown = [item for item in clean if item not in known]
    if unknown:
        raise ValueError(f"CIK not allowlisted: {', '.join(sorted(unknown))}")
    return tuple(known[item] for item in clean)


def _company(item: dict[str, Any]) -> CompanyConfig:
    return CompanyConfig(
        cik=_cik10(str(item["cik"])),
        ticker=str(item["ticker"]),
        expected_name=str(item["expected_name"]),
        entity_type=str(item["entity_type"]),
        sic=str(item["sic"]),
        sic_description=str(item["sic_description"]),
        country_code=str(item["country_code"]),
        overlap_days=int(item["overlap_days"]),
        facts=tuple(_fact(row) for row in item.get("facts") or []),
    )


def _fact(row: dict[str, Any]) -> FactConfig:
    return FactConfig(
        metric_name=str(row["metric_name"]),
        taxonomy=str(row["taxonomy"]),
        concept=str(row["concept"]),
        unit=str(row["unit"]),
        freshness_sla_days=int(row["freshness_sla_days"]),
    )


def _cik10(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits or len(digits) > 10:
        raise ValueError("invalid SEC CIK")
    return digits.zfill(10)
