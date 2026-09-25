from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.sap_client import SapS4Client

_PG_REQUIRED = ("database_url",)
_MINIO_REQUIRED = ("minio_endpoint", "minio_access_key", "minio_secret_key", "minio_bucket")


def _missing(*names: str) -> list[str]:
    return [n.upper() for n in names if not getattr(settings, n, "")]


def check_sap(security_context: str | None = None) -> dict[str, Any]:
    return SapS4Client(security_context=security_context).configuration_status()


def check_postgres() -> dict[str, Any]:
    missing = _missing(*_PG_REQUIRED)
    return {"component": "postgres", "configured": not missing, "missing": missing}


def check_minio() -> dict[str, Any]:
    missing = _missing(*_MINIO_REQUIRED)
    return {"component": "minio", "configured": not missing, "missing": missing}


def preflight_for_extract(security_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    serialized = (
        json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(security_context, dict)
        else None
    )
    components = [check_sap(serialized), check_postgres(), check_minio()]
    failing = [c for c in components if not c.get("configured", True)]
    if not failing:
        return None
    return {
        "status": "degraded",
        "configured": False,
        "missing": [m for c in failing for m in (c.get("missing") or [])],
        "components": components,
    }
