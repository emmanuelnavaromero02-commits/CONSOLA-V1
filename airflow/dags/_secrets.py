from __future__ import annotations

import os
from typing import Optional


_MISSING = object()


def get_secret(name: str, env: Optional[str] = None, default=_MISSING) -> str:
    env_name = env or name.upper()
    value = os.environ.get(env_name)
    if value:
        return value

    try:
        from airflow.models import Variable

        return Variable.get(name)
    except Exception:
        if default is _MISSING:
            raise RuntimeError(
                f"Secret {name!r} not found in env ({env_name}) or Airflow Variables"
            )
        return default
