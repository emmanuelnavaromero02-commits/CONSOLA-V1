from __future__ import annotations

import os
import secrets


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY")
    insecure_fragments = (
        "change_me",
        "changeme",
        "local_dev",
        "replace",
        "example",
        "dummy",
        "secret_key",
        "do_not_use",
        "dev-secret-key",
    )
    normalized = (key or "").strip().lower()
    if not normalized or len(normalized) < 32 or any(fragment in normalized for fragment in insecure_fragments):
        raise RuntimeError("INTERNAL_API_KEY missing or using an insecure default. System halted for security.")
    return key
