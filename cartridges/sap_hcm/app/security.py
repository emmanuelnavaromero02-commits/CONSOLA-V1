"""
Internal API key resolver.

The key is read on every call so that:
  * test suites can set the env var before importing the app;
  * a missing or insecure-default key still hard-fails any request,
    because ``verify_api_key`` will compare the supplied header against
    the result of this function.
"""
from __future__ import annotations

import os
import secrets

INSECURE_DEFAULTS = frozenset({"dev-secret-key", "changeme", "secret", ""})


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY", "")
    # Reject the well-known insecure defaults but never raise at import time —
    # callers raise an HTTP 401 themselves so the process can still serve /health.
    if not key or any(secrets.compare_digest(key, bad) for bad in INSECURE_DEFAULTS):
        return "__internal_api_key_not_configured__"
    return key
