"""Registry of the domain AgentOps monitors.

Talent has no registry: it is discovered by two hardcoded predicates on
``(cartridge_id, slug)``. Three more domains would mean three more pairs of
predicates at every call site, so the domain monitors are enumerated once here
and every consumer — the wisdom-bit dispatch, the seed generator, the tests —
reads this list instead of repeating literals.

Talent is deliberately NOT in this registry. Its contract, its runtime repair and
its wisdom bit predate the shared shape, and folding it in would mean rewriting
``successfactors_talent_monitor`` — which Mission 4 rules out.
"""

from __future__ import annotations

from app.domains.agentops.domain_monitor_support import DomainMonitorSpec
from app.domains.agentops.finance_monitor import FINANCE_MONITOR_SPEC
from app.domains.agentops.operations_monitor import OPERATIONS_MONITOR_SPEC
from app.domains.agentops.risk_monitor import RISK_MONITOR_SPEC

DOMAIN_MONITOR_SPECS: tuple[DomainMonitorSpec, ...] = (
    FINANCE_MONITOR_SPEC,
    OPERATIONS_MONITOR_SPEC,
    RISK_MONITOR_SPEC,
)

# Keyed by wisdom bit id, which is how the internal wisdom-bits route dispatches.
SPECS_BY_WISDOM_BIT: dict[str, DomainMonitorSpec] = {
    spec.wisdom_bit_id: spec for spec in DOMAIN_MONITOR_SPECS
}
SPECS_BY_SLUG: dict[str, DomainMonitorSpec] = {
    spec.slug: spec for spec in DOMAIN_MONITOR_SPECS
}
SPECS_BY_KEY: dict[str, DomainMonitorSpec] = {
    spec.key: spec for spec in DOMAIN_MONITOR_SPECS
}


# Cartridges that have at least one scheduled AgentOps monitor. Talent's is not in
# DOMAIN_MONITOR_SPECS (see the module docstring) so its cartridge is added here
# explicitly. Used by the sync-now path, which used to gate on the literal
# "sap_successfactors" and therefore rendered the AgentOps step as "skipped" for
# every other cartridge — including the three domains that now have monitors.
AGENTOPS_MONITOR_CARTRIDGES: frozenset[str] = frozenset(
    {"sap_successfactors", *(spec.cartridge_id for spec in DOMAIN_MONITOR_SPECS)}
)


def spec_for_wisdom_bit(wisdom_bit_id: str) -> DomainMonitorSpec | None:
    """Look up a domain monitor by wisdom bit id, case-insensitively.

    The internal route upper-cases the incoming id before dispatch, matching how
    the Talent route already normalises ``WB-TALENTO``.
    """
    return SPECS_BY_WISDOM_BIT.get(str(wisdom_bit_id or "").strip().upper())


def spec_for_slug(slug: str) -> DomainMonitorSpec | None:
    return SPECS_BY_SLUG.get(str(slug or "").strip())


__all__ = (
    "AGENTOPS_MONITOR_CARTRIDGES",
    "DOMAIN_MONITOR_SPECS",
    "SPECS_BY_KEY",
    "SPECS_BY_SLUG",
    "SPECS_BY_WISDOM_BIT",
    "spec_for_slug",
    "spec_for_wisdom_bit",
)
