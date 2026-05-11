from app.core.config import settings

def get_sap_checkin_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_checkin_base_url
    user = settings.sap_checkin_user
    password = settings.sap_checkin_pass

    if not user or not password:
        raise ValueError(
            "SAP Check-In Empleados credentials not configured.\n"
            "Set SAP_CHECKIN_USER and SAP_CHECKIN_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
