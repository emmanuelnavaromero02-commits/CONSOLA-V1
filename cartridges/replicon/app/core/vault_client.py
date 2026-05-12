"""
Credential helpers for the Replicon cartridge (non-Airflow services).
Reads from system_settings (via console proxy) with env-var fallback.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.settings_proxy import get_setting


def get_replicon_credentials() -> tuple[str, str]:
    """Return (base_url, token) from system_settings or env fallback."""
    base_url = get_setting(
        "replicon_base_url",
        default=settings.replicon_base_url,
        env_fallback="REPLICON_BASE_URL",
    )
    token = get_setting(
        "replicon_token",
        default=settings.replicon_api_token or "",
        env_fallback="REPLICON_TOKEN",
    )
    if not token:
        raise ValueError(
            "Replicon API token not configured.\n"
            "Set REPLICON_API_TOKEN environment variable for the cartridge service."
        )
    return base_url, token
