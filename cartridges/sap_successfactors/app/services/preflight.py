"""
Pre-flight configuration checks for the sap_successfactors cartridge.

Validates the three environments the cartridge depends on (SAP, Postgres,
MinIO) and returns a structured ``degraded`` report when any of them is
incomplete. The checks are cheap (env / settings only — no network
calls) so they run before every extract / preview.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.sap_client import SapSfClient

_PG_REQUIRED = ("database_url",)
_MINIO_REQUIRED = ("minio_endpoint", "minio_access_key", "minio_secret_key", "minio_bucket")


def _missing(*names: str) -> list[str]:
    return [n.upper() for n in names if not getattr(settings, n, "")]


def check_sap() -> dict[str, Any]:
    return SapSfClient().configuration_status()


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
    a missing SAP credential).
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
