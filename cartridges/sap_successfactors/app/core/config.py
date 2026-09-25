from __future__ import annotations

import os
from typing import Optional

try:
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:  # pragma: no cover - exercised by source-level tests.
    BaseSettings = None  # type: ignore[assignment]
    Field = None  # type: ignore[assignment]
    SettingsConfigDict = None  # type: ignore[assignment]


if BaseSettings is not None:

    class Settings(BaseSettings):
        app_name: str = "sap_successfactors"

        sf_base_url: str = ""
        sf_company_id: str = ""
        sf_client_id: str = ""
        sf_client_secret: str = ""
        sf_token_url: str = ""
        sf_idp_url: str = ""
        sf_auth_method: str = "oauth2_client_credentials"
        sf_private_key_path: str = "/run/secrets/sf_epiuse_iaappliance_connector.pem"
        sf_admin_user: str = ""

        database_url: str = Field(default_factory=lambda: os.environ["DATABASE_URL"])

        lakehouse_provider: str = ""
        lakehouse_endpoint: str = ""
        lakehouse_bucket: str = ""
        gcs_bucket: str = ""
        gcs_access_key_id: str = ""
        gcs_secret_access_key: str = ""
        s3_bucket_name: str = ""
        aws_access_key_id: str = ""
        aws_secret_access_key: str = ""
        aws_session_token: str = ""
        aws_region: str = ""
        aws_default_region: str = ""

        minio_endpoint: str = ""
        minio_access_key: str = ""
        minio_secret_key: str = ""
        minio_bucket: str = "lakehouse"
        minio_secure: bool = False

        internal_api_key: str = ""

        refinement_url: Optional[str] = None

        airflow_url: Optional[str] = None
        airflow_user: Optional[str] = None
        airflow_password: Optional[str] = None

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            case_sensitive=False,
        )

else:

    def _env(name: str, default: str = "") -> str:
        return os.environ.get(name.upper(), os.environ.get(name.lower(), default))

    def _env_bool(name: str, default: bool = False) -> bool:
        value = _env(name, str(default)).strip().lower()
        return value in {"1", "true", "yes", "on"}

    class Settings:
        app_name: str = _env("APP_NAME", "sap_successfactors")

        sf_base_url: str = _env("SF_BASE_URL")
        sf_company_id: str = _env("SF_COMPANY_ID")
        sf_client_id: str = _env("SF_CLIENT_ID")
        sf_client_secret: str = _env("SF_CLIENT_SECRET")
        sf_token_url: str = _env("SF_TOKEN_URL")
        sf_idp_url: str = _env("SF_IDP_URL")
        sf_auth_method: str = _env("SF_AUTH_METHOD", "oauth2_client_credentials")
        sf_private_key_path: str = _env(
            "SF_PRIVATE_KEY_PATH",
            "/run/secrets/sf_epiuse_iaappliance_connector.pem",
        )
        sf_admin_user: str = _env("SF_ADMIN_USER")

        database_url: str = _env("DATABASE_URL")

        lakehouse_provider: str = _env("LAKEHOUSE_PROVIDER")
        lakehouse_endpoint: str = _env("LAKEHOUSE_ENDPOINT")
        lakehouse_bucket: str = _env("LAKEHOUSE_BUCKET")
        gcs_bucket: str = _env("GCS_BUCKET")
        gcs_access_key_id: str = _env("GCS_ACCESS_KEY_ID")
        gcs_secret_access_key: str = _env("GCS_SECRET_ACCESS_KEY")
        s3_bucket_name: str = _env("S3_BUCKET_NAME")
        aws_access_key_id: str = _env("AWS_ACCESS_KEY_ID")
        aws_secret_access_key: str = _env("AWS_SECRET_ACCESS_KEY")
        aws_session_token: str = _env("AWS_SESSION_TOKEN")
        aws_region: str = _env("AWS_REGION")
        aws_default_region: str = _env("AWS_DEFAULT_REGION")

        minio_endpoint: str = _env("MINIO_ENDPOINT")
        minio_access_key: str = _env("MINIO_ACCESS_KEY")
        minio_secret_key: str = _env("MINIO_SECRET_KEY")
        minio_bucket: str = _env("MINIO_BUCKET", "lakehouse")
        minio_secure: bool = _env_bool("MINIO_SECURE", False)

        internal_api_key: str = _env("INTERNAL_API_KEY")

        refinement_url: Optional[str] = _env("REFINEMENT_URL") or None
        airflow_url: Optional[str] = _env("AIRFLOW_URL") or None
        airflow_user: Optional[str] = _env("AIRFLOW_USER") or None
        airflow_password: Optional[str] = _env("AIRFLOW_PASSWORD") or None


settings = Settings()
