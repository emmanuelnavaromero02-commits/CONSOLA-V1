"""
Pre-flight configuration checks for the sap_b1 cartridge.

Validates the three environments the cartridge depends on (the Business One
database, Postgres, object storage) and returns a structured ``degraded``
report when any of them is incomplete. The checks are cheap (env / settings
only, no network calls) so they run before every extract / preview.
"""
from __future__ import annotations

from typing import Any

from app.core.b1_source import B1Client
from app.core.config import settings

_PG_REQUIRED = ("database_url",)
_MINIO_REQUIRED = ("minio_endpoint", "minio_access_key", "minio_secret_key", "minio_bucket")


def _missing(*names: str) -> list[str]:
    return [n.upper() for n in names if not getattr(settings, n, "")]


def check_sap() -> dict[str, Any]:
    status = B1Client().configuration_status()
    return {"component": "sap_b1", **status}


def check_postgres() -> dict[str, Any]:
    missing = _missing(*_PG_REQUIRED)
    return {"component": "postgres", "configured": not missing, "missing": missing}


def check_minio() -> dict[str, Any]:
    missing = _missing(*_MINIO_REQUIRED)
    return {"component": "minio", "configured": not missing, "missing": missing}


def preflight_for_extract() -> dict[str, Any] | None:
    """Return ``None`` when ready, otherwise a degraded report.

    Reports each missing component separately so the caller can render a
    precise error to the user (avoids "Postgres connection failed" hiding
    a missing database credential).
    """
    components = [check_sap(), check_postgres(), check_minio()]
    failing = [c for c in components if not c.get("configured", True)]
    if not failing:
        return None
    return {
        "status": "degraded",
        "configured": False,
        "missing": [m for c in failing for m in (c.get("missing") or [])],
        "components": components,
    }
