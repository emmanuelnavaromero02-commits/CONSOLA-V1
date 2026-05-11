from app.core.config import settings

def get_sap_s4hana_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_s4hana_base_url
    user = settings.sap_s4hana_user
    password = settings.sap_s4hana_pass

    if not user or not password:
        raise ValueError(
            "SAP S/4HANA credentials not configured.\n"
            "Set SAP_S4_USER and SAP_S4_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
