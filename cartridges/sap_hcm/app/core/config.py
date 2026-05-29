from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "sap_hcm"

    # SAP HCM (NetWeaver Gateway OData) — Basic Auth
    # Canonical env vars:
    #   SAP_HCM_BASE_URL, SAP_HCM_USER, SAP_HCM_PASS, SAP_HCM_CLIENT_MANDANT
    # Legacy SAP_HCM_CLIENT is honoured as fallback (see __init__ below).
    sap_hcm_base_url: str = ""
    sap_hcm_user: str = ""
    sap_hcm_pass: str = ""
    sap_hcm_client_mandant: str = "100"

    # Database
    database_url: str = Field(default_factory=lambda: os.environ["DATABASE_URL"])

    # MinIO
    minio_endpoint: str = ""
    minio_access_key: str = Field(default_factory=lambda: os.environ["MINIO_ACCESS_KEY"])
    minio_secret_key: str = Field(default_factory=lambda: os.environ["MINIO_SECRET_KEY"])
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

    def __init__(self, **values):
        # Back-compat: accept the legacy short name for the mandant.
        if os.environ.get("SAP_HCM_CLIENT_MANDANT") is None \
                and os.environ.get("SAP_HCM_CLIENT") is not None:
            os.environ["SAP_HCM_CLIENT_MANDANT"] = os.environ["SAP_HCM_CLIENT"]
        super().__init__(**values)


settings = Settings()
