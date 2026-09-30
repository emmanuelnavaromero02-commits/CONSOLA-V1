from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import NamedTuple

SUCCESSION_COVERAGE_DATASET = "sap_successfactors_talent_succession_coverage"
POSITION_SOURCE = "silver/sap_successfactors/sap_successfactors_position_latest"
NOMINATION_SOURCE = "silver/sap_successfactors/sap_successfactors_successionnomination_latest"
_NOMINATION_READ = re.compile(
    r"read_parquet\s*\(\s*'s3://\{bucket\}/silver/sap_successfactors/"
    r"sap_successfactors_successionnomination_latest/\*\*/\*\.parquet'[^()]*\)",
    re.IGNORECASE,
)
_AVAILABLE = re.compile(r"SELECT\s+TRUE\s+AS\s+nominations_available\b", re.IGNORECASE)
_UNAVAILABLE = "SELECT FALSE AS nominations_available"
_NO_NOMINATIONS = (
    "(SELECT NULL::VARCHAR AS tenant_id, NULL::VARCHAR AS workspace_id, "
    "NULL::VARCHAR AS nomination_id, NULL::VARCHAR AS user_id, "
    "NULL::VARCHAR AS target_position, NULL::VARCHAR AS readiness, "
    "NULL::VARCHAR AS nomination_status WHERE FALSE)"
)

MissingSources = Callable[[list[str]], list[str]]
logger = logging.getLogger(__name__)


class StructuralFallback(NamedTuple):
    build: Callable[[str], str | None]
    sources: tuple[str, ...]
    kind: Callable[[MissingSources | None], str | None]


def succession_coverage_without_nominations(sql: str) -> str | None:
    """Same projection with the nomination input empty and flagged as unavailable."""
    if len(_NOMINATION_READ.findall(sql)) != 1 or len(_AVAILABLE.findall(sql)) != 1:
        return None
    return _AVAILABLE.sub(_UNAVAILABLE, _NOMINATION_READ.sub(_NO_NOMINATIONS, sql))


def succession_fallback_kind(missing_sources: MissingSources | None) -> str | None:
    """'derived' only when the nomination silver has no publication head, 'readfree' when the position silver has none."""
    if missing_sources is None:
        return None
    try:
        missing = list(missing_sources([POSITION_SOURCE, NOMINATION_SOURCE]))
    except Exception as exc:  # noqa: BLE001 - an unreadable head is not an absent one
        logger.warning("succession fallback probe failed: %s", type(exc).__name__)
        return None
    if POSITION_SOURCE in missing:
        return "readfree"
    if missing == [NOMINATION_SOURCE]:
        return "derived"
    return None


SUCCESSFACTORS_DERIVED_GOLD_FALLBACKS: dict[str, StructuralFallback] = {
    SUCCESSION_COVERAGE_DATASET: StructuralFallback(
        build=succession_coverage_without_nominations,
        sources=(POSITION_SOURCE,),
        kind=succession_fallback_kind,
    ),
}
