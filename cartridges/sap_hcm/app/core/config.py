from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "sap_hcm"

    # SAP HCM (NetWeaver Gateway OData) — Basic Auth
    sap_hcm_base_url: str = ""
    sap_hcm_user: str = ""
    sap_hcm_pass: str = ""
    sap_hcm_client: str = "100"

    # Database
    database_url: str = ""

    # MinIO
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "lakehouse"
    minio_secure: bool = False

    # Internal API key
    internal_api_key: str = ""

    # Refinement service
    refinement_url: Optional[str] = None

    # Airflow
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
