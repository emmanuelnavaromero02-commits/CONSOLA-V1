from __future__ import annotations

from typing import Any

from app.core.sec_client import MetadataDriftError, SECClient
from app.services.config_loader import CompanyConfig


def validate_metadata(client: SECClient, companies: tuple[CompanyConfig, ...]) -> list[dict[str, str]]:
    metadata = client.get_metadata([item.cik for item in companies])
    facts = client.get_company_facts([item.cik for item in companies])
    validate_metadata_payload(metadata, companies)
    validate_facts_payload(facts, companies)
    by_cik = {str(row.get("cik") or ""): row for row in metadata.get("sec_edgar", {}).get("metadata", [])}
    return [{"cik": item.cik, "name": str(by_cik[item.cik].get("name") or ""), "status": "ok"} for item in companies]


def validate_metadata_payload(payload: dict[str, Any], companies: tuple[CompanyConfig, ...]) -> None:
    rows = payload.get("sec_edgar", {}).get("metadata", [])
    by_cik = {str(row.get("cik") or ""): row for row in rows if isinstance(row, dict)}
    missing = [item.cik for item in companies if item.cik not in by_cik]
    if missing:
        raise MetadataDriftError(f"SEC CIK metadata missing: {', '.join(missing)}")
    for company in companies:
        row = by_cik[company.cik]
        tickers = {str(ticker).upper() for ticker in row.get("tickers") or []}
        checks = {
            "name": str(row.get("name") or "") == company.expected_name,
            "ticker": company.ticker.upper() in tickers,
            "entity_type": str(row.get("entity_type") or "") == company.entity_type,
            "sic": str(row.get("sic") or "") == company.sic,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise MetadataDriftError(f"SEC CIK {company.cik} metadata drift: {', '.join(failed)}")


def validate_facts_payload(payload: dict[str, Any], companies: tuple[CompanyConfig, ...]) -> None:
    by_cik = {str(row.get("cik") or ""): row.get("raw_facts") for row in payload.get("sec_edgar", {}).get("companies", [])}
    for company in companies:
        raw = by_cik.get(company.cik)
        if not isinstance(raw, dict):
            raise MetadataDriftError(f"SEC CIK {company.cik} facts missing")
        facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
        missing = [
            f"{fact.taxonomy}:{fact.concept}"
            for fact in company.facts
            if not isinstance(facts.get(fact.taxonomy), dict) or fact.concept not in facts[fact.taxonomy]
        ]
        if missing:
            raise MetadataDriftError(f"SEC CIK {company.cik} XBRL facts missing: {', '.join(missing)}")
