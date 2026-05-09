from app.core.config import settings

def get_sap_time_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_time_base_url
    user = settings.sap_time_user
    password = settings.sap_time_pass

    if not user or not password:
        raise ValueError(
            "SAP Time Management credentials not configured.\n"
            "Set SAP_TIME_USER and SAP_TIME_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
