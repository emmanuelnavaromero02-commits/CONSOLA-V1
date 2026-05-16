from __future__ import annotations

import os
import secrets


def _runtime_env() -> str:
    """Return the current runtime environment name (lowercased).

    v1.43.2 (Codex P1-2): default flipped from ``development`` to
    ``production``. A misconfigured deploy that forgets to set APP_ENV
    used to silently enable dangerous tools (airflow_create_dag,
    vault mutating ops, etc.). Now it falls back to the safer mode and
    operators have to opt into dev behaviour explicitly via
    APP_ENV=development in their docker-compose env."""
    return (
        os.environ.get("APP_ENV")
        or os.environ.get("ENV")
        or os.environ.get("MODE")
        or "production"
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
