from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_evidence_identity import (
    administrative_references,
    canonical_sources,
    source_and_id_pair,
)
from app.services.control_room.business_evidence_reference import (
    has_substantive_reference,
)
from app.services.control_room.business_semantic_slots import semantic_maps


EVIDENCE_FIELDS = (
    "analysis_evidence",
    "evidence",
    "evidence_pack",
    "evidence_refs",
)
EVIDENCE_ID_FIELDS = ("evidence_pack_id", "evidence_id")


def has_evidence(item: Mapping[str, Any]) -> bool:
    surfaces = semantic_maps(item)
    sources = canonical_sources(surfaces)
    excluded = administrative_references(surfaces)
    for values in surfaces:
        if any(key in values for key in EVIDENCE_ID_FIELDS) and source_and_id_pair(
            values,
            allow_generic_id=False,
            canonical_sources_by_role=sources,
            excluded_references=excluded,
        ):
            return True
        for key in EVIDENCE_FIELDS:
            if key in values and has_substantive_reference(
                values.get(key),
                scalar_is_locator=True,
                allow_generic_id=True,
                canonical_sources_by_role=sources,
                excluded_references=excluded,
            ):
                return True
    return False


__all__ = ("EVIDENCE_FIELDS", "EVIDENCE_ID_FIELDS", "has_evidence")
