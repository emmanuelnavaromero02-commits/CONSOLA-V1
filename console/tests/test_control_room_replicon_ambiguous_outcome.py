from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import egress_guard
from app.services.adapters import AdapterConfigurationError, AdapterExecutionError
from app.services.adapters.circuit_breaker import CartridgeCircuitBreaker
from app.services.adapters.replicon_adapter import RepliconAdapter
from app.services.control_room.business_external_outcome import (
    adapter_error_outcome_is_ambiguous,
)

EgressGuardError = egress_guard.EgressGuardError


def _execute_with_guard_error(error: EgressGuardError) -> None:
    adapter = RepliconAdapter()
    CartridgeCircuitBreaker.reset(adapter.cartridge_id)
    try:
        with patch(
            "app.services.adapters.replicon_adapter.egress_guard.pinned_request_sync",
            side_effect=error,
        ):
            adapter.execute(
                {"idempotency_key": "cr-action:v1:replicon-outcome"},
                {
                    "base_url": "https://replicon.example.invalid",
                    "writeback_path": "/writeback",
                    "token": "secret",
                },
                dry_run=False,
            )
    finally:
        CartridgeCircuitBreaker.reset(adapter.cartridge_id)


def test_replicon_pre_dispatch_guard_error_is_definitive_configuration() -> None:
    with pytest.raises(AdapterConfigurationError) as exc:
        _execute_with_guard_error(EgressGuardError("host is blocked"))

    assert exc.value.outcome_ambiguous is False
    assert not adapter_error_outcome_is_ambiguous(
        exc.value, remote_attempt_started=True
    )


def test_replicon_post_dispatch_response_guard_error_is_ambiguous() -> None:
    with pytest.raises(AdapterExecutionError) as exc:
        _execute_with_guard_error(
            EgressGuardError(
                "response exceeds size limit",
                request_dispatched=True,
            )
        )

    assert not isinstance(exc.value, AdapterConfigurationError)
    assert exc.value.outcome_ambiguous is True
    assert adapter_error_outcome_is_ambiguous(exc.value, remote_attempt_started=True)


@pytest.mark.parametrize("status_code", (0, 100, 302, 600, 999))
def test_replicon_unconfirmed_post_status_is_ambiguous(status_code: int) -> None:
    adapter = RepliconAdapter()
    response = egress_guard.PinnedHTTPResponse(status_code, {}, b"")
    with (
        patch(
            "app.services.adapters.replicon_adapter.egress_guard.pinned_request_sync",
            return_value=response,
        ),
        pytest.raises(AdapterExecutionError) as exc,
    ):
        adapter.execute(
            {"idempotency_key": "cr-action:v1:replicon-status"},
            {
                "base_url": "https://replicon.example.invalid",
                "writeback_path": "/writeback",
                "token": "secret",
            },
            dry_run=False,
        )

    CartridgeCircuitBreaker.reset(adapter.cartridge_id)
    assert exc.value.status_code == status_code
    assert exc.value.outcome_ambiguous is True
    assert adapter_error_outcome_is_ambiguous(exc.value, remote_attempt_started=True)
