"""Stable facade for Bayesian calibration services.

Implementation is split by validation, persistence, observation, and recompute
responsibilities so each production module remains reviewable and bounded.
"""

from app.services import auth
from app.services.intelligence import calibration
from app.services.intelligence.calibration_observation_service import observe
from app.services.intelligence.calibration_recompute_service import (
    _observation_from_row,
    get_state,
    get_state_map_for_live_calibration,
    list_observations,
    recompute,
)
from app.services.intelligence.calibration_state_repository import (
    _derived_prior_for_group,
    _fetch_state,
    _json_obj,
    _observation_id,
    _parent_group_candidates,
    _parent_prior_source,
    _row_state,
    _source_exists,
    _state_id,
    _upsert_state,
)
from app.services.intelligence.calibration_validation_service import (
    DEFAULT_MODEL_VERSION,
    FORBIDDEN_SCOPE_KEYS,
    SOURCE_TYPES,
    _actor_id,
    _calibration_group,
    _forbidden_path,
    _observed_at,
    _short_text,
    _synthetic_allowed,
    _validate_evidence_refs,
    _validate_payload,
)


__all__ = (
    "DEFAULT_MODEL_VERSION",
    "FORBIDDEN_SCOPE_KEYS",
    "SOURCE_TYPES",
    "_actor_id",
    "_calibration_group",
    "_derived_prior_for_group",
    "_fetch_state",
    "_forbidden_path",
    "_json_obj",
    "_observation_from_row",
    "_observation_id",
    "_observed_at",
    "_parent_group_candidates",
    "_parent_prior_source",
    "_row_state",
    "_short_text",
    "_source_exists",
    "_state_id",
    "_synthetic_allowed",
    "_upsert_state",
    "_validate_evidence_refs",
    "_validate_payload",
    "auth",
    "calibration",
    "get_state",
    "get_state_map_for_live_calibration",
    "list_observations",
    "observe",
    "recompute",
)
