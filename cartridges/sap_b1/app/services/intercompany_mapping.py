from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.b1_source import B1ConfigurationError

ENTITY = "IntercompanyPartners"
COLUMNS = ("CardCode", "CounterpartyCompany", "MappingSource")
_CARD_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,15}$")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class IntercompanyPartner:
    company: str
    card_code: str
    counterparty: str


def parse_intercompany(spec: str) -> list[IntercompanyPartner]:
    partners: list[IntercompanyPartner] = []
    seen: set[tuple[str, str]] = set()
    for chunk in (spec or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        left, sep, counterparty = chunk.partition("=")
        company, sep2, card_code = left.partition(":")
        company, card_code, counterparty = company.strip(), card_code.strip(), counterparty.strip()
        if not (sep and sep2 and company and card_code and counterparty):
            raise B1ConfigurationError("SAP_B1_INTERCOMPANY entries must look like company:CARDCODE=counterparty")
        for alias in (company, counterparty):
            if not _ALIAS_RE.fullmatch(alias):
                raise B1ConfigurationError(f"invalid company alias in SAP_B1_INTERCOMPANY: {alias!r}")
        if company == counterparty:
            raise B1ConfigurationError(f"a company cannot be its own counterparty: {company!r}")
        if not _CARD_RE.fullmatch(card_code):
            raise B1ConfigurationError("invalid business partner code in SAP_B1_INTERCOMPANY")
        if (company, card_code) in seen:
            raise B1ConfigurationError(f"duplicate intercompany mapping for {company}:{card_code}")
        seen.add((company, card_code))
        partners.append(IntercompanyPartner(company=company, card_code=card_code, counterparty=counterparty))
    return partners


def validate_against_companies(partners: list[IntercompanyPartner], aliases: Iterable[str]) -> None:
    known = set(aliases)
    if not known:
        return
    unknown = sorted({alias for p in partners for alias in (p.company, p.counterparty) if alias not in known})
    if unknown:
        raise B1ConfigurationError(
            f"SAP_B1_INTERCOMPANY names companies that are not in SAP_B1_COMPANIES: {unknown}"
        )


def partner_records(partners: list[IntercompanyPartner]) -> list[dict[str, Any]]:
    return [
        {
            "CardCode": partner.card_code,
            "CounterpartyCompany": partner.counterparty,
            "MappingSource": "config",
            "_company": partner.company,
            "_source_updated_at": None,
        }
        for partner in sorted(partners, key=lambda p: (p.company, p.card_code))
    ]


def arrow_schema():
    import pyarrow as pa

    from app.services.b1_queries import COMPANY_COLUMN, METADATA_COLUMNS, SOURCE_UPDATED_COLUMN

    fields = [pa.field(column, pa.string()) for column in COLUMNS]
    fields.extend(pa.field(column, pa.string()) for column in (COMPANY_COLUMN, SOURCE_UPDATED_COLUMN, *METADATA_COLUMNS))
    return pa.schema(fields)
