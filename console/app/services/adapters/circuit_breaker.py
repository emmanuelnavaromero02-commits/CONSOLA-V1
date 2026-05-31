from __future__ import annotations

import time
from dataclasses import dataclass

from .base import AdapterCircuitOpenError


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class CartridgeCircuitBreaker:
    _states: dict[str, _CircuitState] = {}
    threshold = 3
    cooldown_seconds = 300

    @classmethod
    def before_call(cls, cartridge_id: str) -> None:
        state = cls._states.setdefault(cartridge_id, _CircuitState())
        if state.failures < cls.threshold:
            return
        if state.opened_at and time.monotonic() - state.opened_at >= cls.cooldown_seconds:
            cls.reset(cartridge_id)
            return
        raise AdapterCircuitOpenError(
            f"{cartridge_id} circuit breaker is UNHEALTHY after {state.failures} consecutive failures",
            status_code=503,
        )

    @classmethod
    def record_success(cls, cartridge_id: str) -> None:
        cls._states[cartridge_id] = _CircuitState()

    @classmethod
    def record_failure(cls, cartridge_id: str) -> None:
        state = cls._states.setdefault(cartridge_id, _CircuitState())
        state.failures += 1
        if state.failures >= cls.threshold:
            state.opened_at = state.opened_at or time.monotonic()

    @classmethod
    def state(cls, cartridge_id: str) -> dict[str, object]:
        state = cls._states.setdefault(cartridge_id, _CircuitState())
        return {
            "state": "UNHEALTHY" if state.failures >= cls.threshold else "HEALTHY",
            "failures": state.failures,
            "threshold": cls.threshold,
        }

    @classmethod
    def reset(cls, cartridge_id: str) -> None:
        cls._states[cartridge_id] = _CircuitState()
