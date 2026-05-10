"""
Credential helpers for the SAP FI/CO Finanzas cartridge (non-Airflow services).
Reads from environment variables / Settings — no external Vault service needed.
"""
from __future__ import annotations

from app.core.config import settings


def get_sap_fi_co_credentials() -> tuple[str, str]:
    """Return (base_url, token) from environment configuration."""
    base_url = settings.sap_fi_co_base_url
    token    = settings.sap_fi_co_api_token or ""
    if not token:
        raise ValueError(
            "SAP FI/CO Finanzas API token not configured.\n"
            "Set SAP_FICO_API_TOKEN environment variable for the cartridge service."
        )
    return base_url, token
