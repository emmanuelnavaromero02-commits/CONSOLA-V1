from app.core.config import settings

def get_sap_successfactors_credentials() -> tuple[str, str, str, str, str]:
    """Return SF OAuth credentials from environment configuration."""
    base_url = settings.sf_base_url
    company_id = settings.sf_company_id
    client_id = settings.sf_client_id
    client_secret = settings.sf_client_secret
    token_url = settings.sf_token_url

    if not all([company_id, client_id, client_secret, token_url]):
        raise ValueError(
            "SAP SuccessFactors credentials not configured.\n"
            "Set SF_COMPANY_ID, SF_CLIENT_ID, SF_CLIENT_SECRET, and SF_TOKEN_URL environment variables."
        )
    return base_url, company_id, client_id, client_secret, token_url

def get_secret(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)
