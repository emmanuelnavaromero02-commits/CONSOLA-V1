"""
Secret resolution helper shared by Replicon DAGs.

Reading credentials from Airflow Variables exposes them to anyone with admin
UI access and ships them through task logs unless every log line is audited.
This helper prefers process-level environment variables — typically injected
from a Docker secret, Kubernetes Secret, or external secrets backend — and
only falls back to Variable.get() for backwards-compatibility with existing
deployments that still load values into the Airflow metadata DB.

Usage:
    from _secrets import get_secret

    minio_secret = get_secret("minio_secret_key", env="MINIO_SECRET_KEY")

If neither source resolves and no `default` is provided, the call raises so
the DAG fails fast at parse time rather than running with an empty credential.
"""
from __future__ import annotations

import os
from typing import Optional


_MISSING = object()


def get_secret(name: str, env: Optional[str] = None, default=_MISSING) -> str:
    """Return the credential value, preferring env over Variable.get.

    Args:
        name: Airflow Variable key (legacy fallback).
        env: Optional explicit environment variable name; defaults to the
             upper-case form of `name`.
        default: Returned when neither source resolves. If not supplied, a
             RuntimeError is raised — fail fast beats silently shipping a
             blank credential to MinIO/SMTP/etc.
    """
    env_name = env or name.upper()
    value = os.environ.get(env_name)
    if value:
        return value

    try:
        # Imported lazily so unit tests that don't have the airflow package
        # installed can still import this module.
        from airflow.models import Variable

        return Variable.get(name)
    except Exception:
        if default is _MISSING:
            raise RuntimeError(
                f"Secret {name!r} not found in env ({env_name}) or Airflow Variables"
            )
        return default
