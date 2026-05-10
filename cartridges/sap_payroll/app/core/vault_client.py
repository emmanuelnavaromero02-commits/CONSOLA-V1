"""
Credential helpers for the SAP Payroll cartridge (non-Airflow services).
Reads from environment variables / Settings — no external Vault service needed.
"""
from __future__ import annotations

from app.core.config import settings


def get_sap_payroll_credentials() -> tuple[str, str]:
    """Return (base_url, token) from environment configuration."""
    base_url = settings.sap_payroll_base_url
    token    = settings.sap_payroll_api_token or ""
    if not token:
        raise ValueError(
            "SAP Payroll API token not configured.\n"
            "Set SAP_PAYROLL_API_TOKEN environment variable for the cartridge service."
        )
    return base_url, token
