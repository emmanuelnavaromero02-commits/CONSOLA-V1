from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_hash(*parts: str) -> str:
    return sha256_bytes("|".join(parts).encode("utf-8"))
