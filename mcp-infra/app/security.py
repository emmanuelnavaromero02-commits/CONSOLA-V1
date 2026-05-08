from __future__ import annotations

import os


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY")
    if not key or key == "dev-secret-key":
        raise RuntimeError("INTERNAL_API_KEY missing or using default dev-secret-key. System halted for security.")
    return key
