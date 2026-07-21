from __future__ import annotations

from typing import Any


IDEMPOTENT_TEMPLATE_TYPES = frozenset(
    {
        "prepare_hcm_access_review",
        "sap_hcm_it0008",
        "prepare_billing_review",
        "prepare_replicon_adjustment",
    }
)


def template_supports_idempotency(template_type: str) -> bool:
    return str(template_type or "") in IDEMPOTENT_TEMPLATE_TYPES


def adapter_guarantees_idempotency(_template_type: str, adapter: Any) -> bool:
    return bool(getattr(adapter, "supports_idempotency", False))


__all__ = (
    "IDEMPOTENT_TEMPLATE_TYPES",
    "adapter_guarantees_idempotency",
    "template_supports_idempotency",
)
