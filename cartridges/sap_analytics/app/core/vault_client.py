"""
Credential helpers for the SAP Analytics cartridge (non-Airflow services).
Reads from environment variables / Settings — no external Vault service needed.
"""
from __future__ import annotations

from app.core.config import settings


def get_sap_analytics_credentials() -> tuple[str, str]:
    """Return (base_url, token) from environment configuration."""
    base_url = settings.sap_analytics_base_url
    token    = settings.sap_analytics_api_token or ""
    if not token:
        raise ValueError(
            "SAP Analytics API token not configured.\n"
            "Set SAP_ANALYTICS_API_TOKEN environment variable for the cartridge service."
        )
    return base_url, token
