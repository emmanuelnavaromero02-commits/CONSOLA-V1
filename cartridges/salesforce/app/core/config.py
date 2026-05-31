from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "salesforce"

    # Salesforce OAuth2 / REST (SOQL). `sf_base_url` is the org instance URL
    # (e.g. https://mycompany.my.salesforce.com). Auth supports the OAuth2
    # username-password flow, client_credentials, or a static bearer token.
    sf_base_url: str = ""
    sf_company_id: str = ""          # unused by Salesforce; kept for config parity
    sf_client_id: str = ""           # Connected App consumer key
    sf_client_secret: str = ""       # Connected App consumer secret
    sf_token_url: str = ""           # e.g. https://login.salesforce.com/services/oauth2/token
    sf_username: str = ""            # username-password flow
    sf_password: str = ""            # username-password flow
    sf_security_token: str = ""      # appended to password when org requires it
    sf_api_version: str = "v60.0"    # Salesforce REST/SOQL API version

    # Database
    database_url: str = Field(default_factory=lambda: os.environ["DATABASE_URL"])

    # MinIO
    minio_endpoint: str = ""
    minio_access_key: str = Field(default_factory=lambda: os.environ["MINIO_ACCESS_KEY"])
    minio_secret_key: str = Field(default_factory=lambda: os.environ["MINIO_SECRET_KEY"])
    minio_bucket: str = "lakehouse"
    minio_secure: bool = False

    # Internal API key (validated by app.security on startup)
    internal_api_key: str = ""

    # Refinement service (silver layer trigger)
    refinement_url: Optional[str] = None

    # Airflow (optional)
    airflow_url: Optional[str] = None
    airflow_user: Optional[str] = None
    airflow_password: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
