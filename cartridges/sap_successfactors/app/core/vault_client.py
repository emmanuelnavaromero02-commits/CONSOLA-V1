from app.core.config import settings
from app.core.settings_proxy import get_setting


def get_sap_successfactors_credentials() -> tuple[str, str, str, str, str]:
    """Return SF OAuth credentials from system_settings or env fallback.

    base_url, client_id, client_secret live in system_settings (seeded in
    migration 21). company_id and token_url still come from env until they're
    seeded in a future migration.
    """
    base_url = get_setting(
        "sap_successfactors_base_url",
        default=settings.sf_base_url,
        env_fallback="SF_BASE_URL",
    )
    client_id = get_setting(
        "sap_successfactors_client_id",
        default=settings.sf_client_id,
        env_fallback="SF_CLIENT_ID",
    )
    client_secret = get_setting(
        "sap_successfactors_client_secret",
        default=settings.sf_client_secret,
        env_fallback="SF_CLIENT_SECRET",
    )
    company_id = settings.sf_company_id
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
