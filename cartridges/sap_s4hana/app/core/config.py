from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "sap_s4hana"

    # SAP S/4HANA OData APIs — Basic Auth
    # Canonical env vars (preferred):
    #   SAP_S4_BASE_URL, SAP_S4_USER, SAP_S4_PASS, SAP_S4_CLIENT_MANDANT, SAP_S4_API_KEY
    # The legacy short names (S4_BASE_URL, S4_USER, S4_PASS, S4_CLIENT_MANDANT) are
    # still honoured for back-compat — see the __init__ override below.
    sap_s4_base_url: str = ""
    sap_s4_user: str = ""
    sap_s4_pass: str = ""
    sap_s4_client_mandant: str = "100"
    sap_s4_api_key: str = ""

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
        # Back-compat: if a caller still sets the old S4_* names, treat them as
        # fallback for the canonical SAP_S4_* names.
        for legacy, canonical in (
            ("S4_BASE_URL", "SAP_S4_BASE_URL"),
            ("S4_USER", "SAP_S4_USER"),
            ("S4_PASS", "SAP_S4_PASS"),
            ("S4_CLIENT_MANDANT", "SAP_S4_CLIENT_MANDANT"),
            ("S4_API_KEY", "SAP_S4_API_KEY"),
        ):
            if os.environ.get(canonical) is None and os.environ.get(legacy) is not None:
                os.environ[canonical] = os.environ[legacy]
        super().__init__(**values)


settings = Settings()
