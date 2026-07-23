from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.services.control_room.business_action_attempt import (
    mark_remote_attempt_ambiguous,
)

if TYPE_CHECKING:
    from app.services.adapters.base import AdapterExecutionError


AMBIGUOUS_OUTCOME_ERROR_CODE = "external_writeback_outcome_ambiguous"
PENDING_RECONCILIATION_CODE = "external_action_pending_reconciliation"


def adapter_error_outcome_is_ambiguous(
    error: AdapterExecutionError,
    *,
    remote_attempt_started: bool,
) -> bool:
    from app.services.adapters import (
        AdapterCircuitOpenError,
        AdapterConfigurationError,
    )

    if not remote_attempt_started or isinstance(
        error,
        (AdapterCircuitOpenError, AdapterConfigurationError),
    ):
        return False
    explicit_outcome = getattr(error, "outcome_ambiguous", None)
    if explicit_outcome is not None:
        return bool(explicit_outcome)
    status_code = error.status_code
    return status_code is None or status_code == 429 or 500 <= status_code <= 599


async def ambiguous_adapter_error_response(
    conn: Any,
    *,
    error: AdapterExecutionError,
    remote_attempt_started: bool,
    workspace_id: str,
    reservation_id: int,
    effective_key: str,
    result: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not adapter_error_outcome_is_ambiguous(
        error,
        remote_attempt_started=remote_attempt_started,
    ):
        return None
    await mark_remote_attempt_ambiguous(
        conn,
        workspace_id=workspace_id,
        reservation_id=reservation_id,
        effective_key=effective_key,
        error_code=AMBIGUOUS_OUTCOME_ERROR_CODE,
    )
    detail = {
        **dict(result),
        "code": PENDING_RECONCILIATION_CODE,
        "reservation_id": reservation_id,
        "idempotency_key": effective_key,
        "remote_outcome": "ambiguous",
        "reconciliation_required": True,
        "message": (
            "External write-back outcome is ambiguous; retry or reconciliation "
            "must use the same idempotency key."
        ),
    }
    return {"_http_error_status": 409, "_http_error_detail": detail}


__all__ = (
    "AMBIGUOUS_OUTCOME_ERROR_CODE",
    "PENDING_RECONCILIATION_CODE",
    "adapter_error_outcome_is_ambiguous",
    "ambiguous_adapter_error_response",
)
