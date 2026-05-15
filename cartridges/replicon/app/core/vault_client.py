"""
Credential helpers for the Replicon cartridge (non-Airflow services).
Reads from environment variables / Settings — no external Vault service needed.
"""
from __future__ import annotations

from app.core.config import settings


def get_replicon_credentials() -> tuple[str, str]:
    """Return (base_url, token) from environment configuration."""
    base_url = settings.replicon_base_url
    token    = settings.replicon_api_token or ""
    if not token:
        raise ValueError(
            "Replicon API token not configured.\n"
            "Set REPLICON_API_TOKEN environment variable for the cartridge service."
        )
    return base_url, token
