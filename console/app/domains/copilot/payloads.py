from __future__ import annotations

from typing import Annotated, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)

PAGE_CONTEXT_MAX_KEYS = 24
PAGE_CONTEXT_MAX_KEY_LEN = 64
PAGE_CONTEXT_MAX_VALUE_LEN = 800

PageContextValue = Union[StrictStr, StrictInt, StrictFloat, StrictBool]


class TurnRequest(BaseModel):
    """Body of a copilot turn: the user message plus optional ephemeral page context."""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=16_000)]
    page_context: dict[str, PageContextValue] | None = None

    @field_validator("page_context")
    @classmethod
    def _bounded_page_context(
        cls, value: dict[str, PageContextValue] | None
    ) -> dict[str, PageContextValue] | None:
        if value is None:
            return None
        if len(value) > PAGE_CONTEXT_MAX_KEYS:
            raise ValueError(
                f"page_context accepts at most {PAGE_CONTEXT_MAX_KEYS} keys"
            )
        for key, item in value.items():
            if len(key) > PAGE_CONTEXT_MAX_KEY_LEN:
                raise ValueError(
                    f"page_context keys are limited to {PAGE_CONTEXT_MAX_KEY_LEN} characters"
                )
            if isinstance(item, str) and len(item) > PAGE_CONTEXT_MAX_VALUE_LEN:
                raise ValueError(
                    f"page_context values are limited to {PAGE_CONTEXT_MAX_VALUE_LEN} characters"
                )
        return value
