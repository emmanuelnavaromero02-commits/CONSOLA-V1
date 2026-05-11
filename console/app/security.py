from __future__ import annotations

import os
import secrets


def _runtime_env() -> str:
    """Return the current runtime environment name (lowercased)."""
    return (
        os.environ.get("APP_ENV")
        or os.environ.get("ENV")
        or os.environ.get("MODE")
        or "development"
    ).lower()


def required_secret(name: str, dev_default: str | None = None) -> str:
    """Return env var `name`, failing loudly in production if absent.

    - In production/prod: raises RuntimeError if the var is unset.
    - In other envs: returns dev_default if provided, else raises RuntimeError.
    """
    value = os.environ.get(name)
    if value:
        return value
    env = _runtime_env()
    if env in {"production", "prod"}:
        raise RuntimeError(f"{name} is required in production but is not set")
    if dev_default is not None:
        return dev_default
    raise RuntimeError(f"{name} is not configured")


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
