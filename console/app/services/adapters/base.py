from __future__ import annotations

from abc import ABC, abstractmethod
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
    def __init__(self, message: str, *, status_code: int | None = None, response: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AdapterConfigurationError(AdapterExecutionError):
    pass


class AdapterCircuitOpenError(AdapterExecutionError):
    pass


class BaseAdapter(ABC):
    cartridge_id: str

    @abstractmethod
    async def execute(self, action_data: dict[str, Any], credentials: dict[str, Any]) -> ExecutionResult:
        """Execute a real remote write-back action."""
