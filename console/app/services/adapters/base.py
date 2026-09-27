from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExecutionResult:
    success: bool
    message: str
    status_code: int | None = None
    remote_id: str | None = None
    response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "status_code": self.status_code,
            "remote_id": self.remote_id,
            "response": self.response,
        }


class AdapterExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: Any = None,
        outcome_ambiguous: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response
        self.outcome_ambiguous = outcome_ambiguous


class AdapterConfigurationError(AdapterExecutionError):
    pass


class AdapterCircuitOpenError(AdapterExecutionError):
    pass


async def run_adapter(execute: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Await an adapter call; blocking (sync) adapters run in a worker thread, never on the event loop."""
    if inspect.iscoroutinefunction(execute):
        return await execute(*args, **kwargs)
    result = await asyncio.to_thread(execute, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


class BaseAdapter(ABC):
    cartridge_id: str

    @abstractmethod
    async def execute(
        self, action_data: dict[str, Any], credentials: dict[str, Any]
    ) -> ExecutionResult:
        pass
