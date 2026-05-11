from __future__ import annotations

import os
import secrets


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY")
    insecure_default = "dev" + "-secret-key"
    if not key or secrets.compare_digest(key, insecure_default):
        raise RuntimeError("INTERNAL_API_KEY missing or using an insecure default. System halted for security.")
    return key
