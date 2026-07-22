from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any


class RemoteSideEffectCommitted(RuntimeError):
    def __init__(
        self,
        *,
        execution_result: Mapping[str, Any],
        side_effect: Mapping[str, Any],
        cause: BaseException,
    ) -> None:
        super().__init__("remote side effect committed; local projection failed")
        self.execution_result = dict(execution_result)
        self.side_effect = dict(side_effect)
        self.cause_type = type(cause).__name__


@contextmanager
def remote_effect_boundary(
    execution_result: Mapping[str, Any],
    target: str,
    adapter: str,
    after: Mapping[str, Any],
):
    try:
        yield
    except BaseException as error:
        raise RemoteSideEffectCommitted(
            execution_result=execution_result,
            side_effect={"target": target, "adapter": adapter, "after": dict(after)},
            cause=error,
        ) from error


__all__ = ("RemoteSideEffectCommitted", "remote_effect_boundary")
