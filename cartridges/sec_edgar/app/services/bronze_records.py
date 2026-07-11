from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.source_security import BASE_URL, sanitize_source_url
from app.services.config_loader import CompanyConfig, FactConfig


def metadata_rows(payload: dict[str, Any], *, retrieved_at: str, provenance: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("sec_edgar", {}).get("metadata", []):
        if not isinstance(item, dict):
            continue
        cik = str(item.get("cik") or "")
        rows.append(
            {
                "cik": cik,
                "company_name": str(item.get("name") or ""),
                "tickers": ",".join(str(v) for v in item.get("tickers") or []),
                "exchanges": ",".join(str(v) for v in item.get("exchanges") or []),
                "entity_type": str(item.get("entity_type") or ""),
                "sic": str(item.get("sic") or ""),
                "sic_description": str(item.get("sic_description") or ""),
                "fiscal_year_end": str(item.get("fiscal_year_end") or ""),
                **provenance,
                "_source_url": _source_url(cik, "submissions"),
                "_retrieved_at": retrieved_at,
            }
        )
    return rows


def fact_rows(
    payload: dict[str, Any],
    *,
    configs: dict[str, CompanyConfig],
    from_date: str,
    to_date: str,
    retrieved_at: str,
    provenance: dict[str, str],
) -> list[dict[str, Any]]:
    rows = []
    for company in payload.get("sec_edgar", {}).get("companies", []):
        if not isinstance(company, dict):
            continue
        cik = str(company.get("cik") or "")
        config = configs.get(cik)
        raw = company.get("raw_facts") if isinstance(company.get("raw_facts"), dict) else {}
        facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
        for fact in (config.facts if config else ()):
            rows.extend(_rows_for_fact(cik, config, fact, facts, from_date, to_date, retrieved_at, provenance))
    return rows


def _rows_for_fact(
    cik: str,
    company: CompanyConfig,
    fact: FactConfig,
    facts: dict[str, Any],
    from_date: str,
    to_date: str,
    retrieved_at: str,
    provenance: dict[str, str],
) -> list[dict[str, Any]]:
    concept = facts.get(fact.taxonomy, {}).get(fact.concept, {})
    units = concept.get("units") if isinstance(concept, dict) else {}
    values = units.get(fact.unit) if isinstance(units, dict) else None
    if values is None and isinstance(units, dict) and len(units) == 1:
        values = next(iter(units.values()))
    rows = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        end_date = str(item.get("end") or "")
        if from_date and end_date < from_date:
            continue
        if to_date and end_date > to_date:
            continue
        rows.append(
            {
                "cik": cik,
                "ticker": company.ticker,
                "company_name": company.expected_name,
                "metric_name": fact.metric_name,
                "taxonomy": fact.taxonomy,
                "concept": fact.concept,
                "unit": fact.unit,
                "country_code": company.country_code,
                "fiscal_year": item.get("fy"),
                "fiscal_period": str(item.get("fp") or ""),
                "form": str(item.get("form") or ""),
                "filed": str(item.get("filed") or ""),
                "start_date": str(item.get("start") or ""),
                "end_date": end_date,
                "value_raw": str(item.get("val") if item.get("val") is not None else ""),
                "accession_number": str(item.get("accn") or ""),
                "frame": str(item.get("frame") or ""),
                "freshness_sla_days": fact.freshness_sla_days,
                **provenance,
                "_source_url": _source_url(cik, "companyfacts"),
                "_retrieved_at": retrieved_at,
            }
        )
    return rows


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source_url(cik: str, kind: str) -> str:
    path = f"/api/xbrl/companyfacts/CIK{cik}.json" if kind == "companyfacts" else f"/submissions/CIK{cik}.json"
    return sanitize_source_url(f"{BASE_URL}{path}")
