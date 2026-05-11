from app.core.config import settings

def get_sap_analytics_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_analytics_base_url
    user = settings.sap_analytics_user
    password = settings.sap_analytics_pass

    if not user or not password:
        raise ValueError(
            "SAP Analytics credentials not configured.\n"
            "Set SAP_ANALYTICS_USER and SAP_ANALYTICS_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
