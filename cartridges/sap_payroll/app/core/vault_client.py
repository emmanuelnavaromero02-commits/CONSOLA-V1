from app.core.config import settings

def get_sap_payroll_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment configuration."""
    base_url = settings.sap_payroll_base_url
    user = settings.sap_payroll_user
    password = settings.sap_payroll_pass

    if not user or not password:
        raise ValueError(
            "SAP Payroll credentials not configured.\n"
            "Set SAP_PAYROLL_USER and SAP_PAYROLL_PASS environment variables."
        )
    return base_url, user, password

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
