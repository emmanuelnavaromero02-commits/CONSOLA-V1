"""Public Talent population backend selection.

Phase 2 deliberately exposes only PostgreSQL Gold.  The protocol makes the
future serving cut-over explicit without allowing the shadow implementation to
be selected by an environment typo during the pilot.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Protocol


class TalentPopulationBackendName(StrEnum):
    POSTGRES_GOLD = "postgres_gold"


class TalentPopulationBackend(Protocol):
    name: TalentPopulationBackendName


def public_talent_population_backend() -> TalentPopulationBackendName:
    configured = str(
        os.environ.get("TALENT_POPULATION_BACKEND") or "postgres_gold"
    ).strip().lower()
    if configured != TalentPopulationBackendName.POSTGRES_GOLD:
        raise RuntimeError(
            "BigQuery cannot be selected as a public Talent backend during the shadow pilot"
        )
    return TalentPopulationBackendName.POSTGRES_GOLD


__all__ = (
    "TalentPopulationBackend",
    "TalentPopulationBackendName",
    "public_talent_population_backend",
)
