from app.core.config import settings

def get_sap_fi_co_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_fi_co_base_url
    user = settings.sap_fi_co_user
    password = settings.sap_fi_co_pass

    if not user or not password:
        raise ValueError(
            "SAP FI/CO Finanzas credentials not configured.\n"
            "Set SAP_FICO_USER and SAP_FICO_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
