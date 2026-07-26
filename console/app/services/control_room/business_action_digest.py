from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def canonical_action_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): canonical_action_value(nested)
            for key, nested in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonical_action_value(nested) for nested in value]
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("action contract contains an unsupported value")


def canonical_action_bytes(value: Mapping[str, Any]) -> bytes:
    normalized = canonical_action_value(value)
    return json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")


def action_contract_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_action_bytes(value)).hexdigest()


__all__ = ("action_contract_digest", "canonical_action_bytes", "canonical_action_value")
