from __future__ import annotations

from app.domains.agentops.domain_monitor_support import DomainMonitorSpec
from app.domains.agentops.finance_monitor import FINANCE_MONITOR_SPEC
from app.domains.agentops.operations_monitor import OPERATIONS_MONITOR_SPEC
from app.domains.agentops.risk_monitor import RISK_MONITOR_SPEC
from app.domains.agentops.sap_b1_monitors import SAP_B1_MONITOR_SPECS

DOMAIN_MONITOR_SPECS: tuple[DomainMonitorSpec, ...] = (
    FINANCE_MONITOR_SPEC,
    OPERATIONS_MONITOR_SPEC,
    RISK_MONITOR_SPEC,
)
ALL_DOMAIN_MONITOR_SPECS: tuple[DomainMonitorSpec, ...] = (
    *DOMAIN_MONITOR_SPECS,
    *SAP_B1_MONITOR_SPECS,
)

SPECS_BY_WISDOM_BIT: dict[str, DomainMonitorSpec] = {
    spec.wisdom_bit_id: spec for spec in ALL_DOMAIN_MONITOR_SPECS
}
SPECS_BY_SLUG: dict[str, DomainMonitorSpec] = {
    spec.slug: spec for spec in ALL_DOMAIN_MONITOR_SPECS
}
SPECS_BY_KEY: dict[str, DomainMonitorSpec] = {
    spec.key: spec for spec in ALL_DOMAIN_MONITOR_SPECS
}


AGENTOPS_MONITOR_CARTRIDGES: frozenset[str] = frozenset(
    {"sap_successfactors", *(spec.cartridge_id for spec in ALL_DOMAIN_MONITOR_SPECS)}
)


def spec_for_wisdom_bit(wisdom_bit_id: str) -> DomainMonitorSpec | None:
    return SPECS_BY_WISDOM_BIT.get(str(wisdom_bit_id or "").strip().upper())


def spec_for_slug(slug: str) -> DomainMonitorSpec | None:
    return SPECS_BY_SLUG.get(str(slug or "").strip())


__all__ = (
    "AGENTOPS_MONITOR_CARTRIDGES",
    "ALL_DOMAIN_MONITOR_SPECS",
    "DOMAIN_MONITOR_SPECS",
    "SPECS_BY_KEY",
    "SPECS_BY_SLUG",
    "SPECS_BY_WISDOM_BIT",
    "spec_for_slug",
    "spec_for_wisdom_bit",
)
