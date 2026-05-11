from app.core.config import settings

def get_sap_hcm_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_hcm_base_url
    user = settings.sap_hcm_user
    password = settings.sap_hcm_pass

    if not user or not password:
        raise ValueError(
            "SAP HCM credentials not configured.\n"
            "Set SAP_HCM_USER and SAP_HCM_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
