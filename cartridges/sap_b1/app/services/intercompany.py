"""Intercompany partners: which business-partner codes are group companies.

Business One has no standard flag for "this customer is one of our own
distributors". Until the customer confirms how those partners are marked
(a partner group, a property, or nothing at all), the mapping is
configuration: ``SAP_B1_INTERCOMPANY`` or the Vault field ``intercompany``,
in the form ``company:CARDCODE=counterparty,...`` where ``company`` and
``counterparty`` are aliases from ``SAP_B1_COMPANIES``. The cartridge
writes the mapping to Bronze as the pseudo-entity ``IntercompanyPartners``
so silver and gold can join it like any other table, and the
consolidation can be proven on both sides of the group.

Client-specific codes live in configuration, never in this repository.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.b1_source import CARTRIDGE_ID, B1ConfigurationError, resolve_config
from app.core.vault_client import get_secret_for_worker
from app.services.parquet_service import write_parquet_and_upload
from app.services.runlog_service import create_run, fail_run, finish_run

logger = logging.getLogger(__name__)

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
    """``mx_mfg:C-IC-DIST-A=mx_dist_a,mx_dist_a:V-IC-MFG=mx_mfg`` → partners.

    Each entry says: in company ``mx_mfg``, the business partner
    ``C-IC-DIST-A`` is the group company ``mx_dist_a``. Whether the code is
    a customer or a supplier comes from OCRD.CardType at join time.
    """
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


def resolve_intercompany(security_context: str | None = None) -> list[IntercompanyPartner]:
    """The configured mapping, validated against the configured companies."""
    ctx = (security_context or "").strip() or None
    spec = get_secret_for_worker(CARTRIDGE_ID, "SAP_B1_INTERCOMPANY", security_context=ctx)
    partners = parse_intercompany(spec)
    config = resolve_config(ctx)
    aliases = {company.alias for company in config.companies}
    unknown = sorted({p.company for p in partners} | {p.counterparty for p in partners}) if not aliases else sorted(
        alias for p in partners for alias in (p.company, p.counterparty) if alias not in aliases
    )
    if aliases and unknown:
        raise B1ConfigurationError(
            f"SAP_B1_INTERCOMPANY names companies that are not in SAP_B1_COMPANIES: {sorted(set(unknown))}"
        )
    return partners


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


def refresh_intercompany_partners(security_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the configured mapping to Bronze as a full snapshot.

    An empty mapping is a valid answer ("this group has no intercompany
    partners configured") and still leaves a zero-row artifact, so silver
    can join it without special cases.
    """
    import json

    serialized = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
    run_id = create_run(
        cartridge_id=CARTRIDGE_ID,
        entity_name=ENTITY,
        run_type="full",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    try:
        partners = resolve_intercompany(serialized)
        rows = partner_records(partners)
        storage_uri = write_parquet_and_upload(
            entity=ENTITY,
            rows=rows,
            run_id=run_id,
            load_type="full",
            watermark_field=None,
            expected_columns=[*COLUMNS, "_company", "_source_updated_at"],
            security_context=security_context,
            arrow_schema=arrow_schema(),
        )
        finish_run(
            run_id=run_id,
            status="success",
            records_extracted=len(rows),
            storage_uri=storage_uri,
            finished_at=datetime.now(timezone.utc),
        )
        return {
            "run_id": run_id,
            "entity": ENTITY,
            "mode": "full",
            "record_count": len(rows),
            "storage_uri": storage_uri,
            "companies": sorted({p.company for p in partners}),
            "status": "success",
        }
    except Exception as exc:
        fail_run(run_id=run_id, error_message=str(exc)[:4000], finished_at=datetime.now(timezone.utc))
        raise
