from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "sap_successfactors"

    # SAP SuccessFactors OAuth2 / OData
    sf_base_url: str = ""
    sf_company_id: str = ""
    sf_client_id: str = ""
    sf_client_secret: str = ""
    sf_token_url: str = ""
    sf_auth_method: str = "oauth2_client_credentials"
    sf_private_key_path: str = "/run/secrets/sf_epiuse_iaappliance_connector.pem"
    sf_admin_user: str = ""

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
