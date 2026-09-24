from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _storage_provider() -> str:
    endpoint = (
        os.environ.get("LAKEHOUSE_ENDPOINT")
        or os.environ.get("MINIO_ENDPOINT")
        or ""
    ).lower()
    inferred = (
        "gcs"
        if "storage.googleapis.com" in endpoint
        else "s3"
        if "amazonaws.com" in endpoint
        else "minio"
    )
    explicit = os.environ.get("LAKEHOUSE_PROVIDER", "").strip().lower()
    if explicit and explicit not in {"gcs", "s3", "minio"}:
        raise RuntimeError("storage_credentials_missing")
    if explicit and inferred in {"gcs", "s3"} and explicit != inferred:
        raise RuntimeError("storage_credentials_missing")
    return explicit or inferred


def _gcs_runtime() -> bool:
    return _storage_provider() == "gcs"


def _storage_value(gcs_name: str, minio_name: str) -> str:
    provider = _storage_provider()
    if provider == "gcs":
        return os.environ.get(gcs_name, "")
    if provider == "s3":
        aws_name = {
            "GCS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
            "GCS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
        }[gcs_name]
        return os.environ.get(aws_name, "")
    return os.environ.get(minio_name, "")


class Settings(BaseSettings):
    app_name: str = "sap_b1"

    # SAP Business One company databases, read over SQL.
    #   SAP_B1_DIALECT    hana (production) | postgres (the B1-shaped test bed)
    #   SAP_B1_HOST       database host reachable from this container (VPN/tunnel)
    #   SAP_B1_PORT       tenant SQL port; empty = dialect default (30015 / 5432)
    #   SAP_B1_USER / SAP_B1_PASSWORD  read-only database user
    #   SAP_B1_DATABASE   HANA tenant database when connecting through SYSTEMDB,
    #                     or the Postgres database name of the test bed
    #   SAP_B1_COMPANIES  "alias=SCHEMA,alias=SCHEMA": one company schema per
    #                     alias. Aliases are what the lakehouse sees; the schema
    #                     names never leave the configuration.
    # Client-specific values live outside the repository (Vault or the host
    # .env); the repository only knows the variable names.
    sap_b1_dialect: str = "hana"
    sap_b1_host: str = ""
    sap_b1_port: str = ""
    sap_b1_user: str = ""
    sap_b1_password: str = ""
    sap_b1_database: str = ""
    sap_b1_companies: str = ""
    sap_b1_encrypt: bool = True
    sap_b1_ssl_validate_certificate: bool = True
    sap_b1_connect_timeout_seconds: int = 15

    # Database
    database_url: str = Field(default_factory=lambda: os.environ["DATABASE_URL"])

    # MinIO
    minio_endpoint: str = Field(
        default_factory=lambda: os.environ.get("LAKEHOUSE_ENDPOINT") or "minio:9000"
    )
    minio_access_key: str = Field(
        default_factory=lambda: _storage_value("GCS_ACCESS_KEY_ID", "MINIO_ACCESS_KEY")
    )
    minio_secret_key: str = Field(
        default_factory=lambda: _storage_value("GCS_SECRET_ACCESS_KEY", "MINIO_SECRET_KEY")
    )
    minio_bucket: str = Field(
        default_factory=lambda: (
            os.environ.get("GCS_BUCKET") or os.environ.get("LAKEHOUSE_BUCKET") or "lakehouse"
            if _gcs_runtime()
            else os.environ.get("MINIO_BUCKET", "lakehouse")
        )
    )
    minio_secure: bool = Field(default_factory=_gcs_runtime)
    aws_session_token: str = Field(
        default_factory=lambda: os.environ.get("AWS_SESSION_TOKEN", "")
    )

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
        env_ignore_empty=True,
    )

    def model_post_init(self, __context) -> None:
        provider = _storage_provider()
        if provider == "gcs":
            self.minio_endpoint = os.environ.get("LAKEHOUSE_ENDPOINT") or "storage.googleapis.com"
            self.minio_access_key = os.environ.get("GCS_ACCESS_KEY_ID", "")
            self.minio_secret_key = os.environ.get("GCS_SECRET_ACCESS_KEY", "")
            self.minio_bucket = (
                os.environ.get("GCS_BUCKET")
                or os.environ.get("LAKEHOUSE_BUCKET")
                or ""
            )
            self.minio_secure = True
            self.aws_session_token = ""
        elif provider == "s3":
            region = (
                os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
                or "us-east-1"
            )
            self.minio_endpoint = (
                os.environ.get("LAKEHOUSE_ENDPOINT")
                or os.environ.get("MINIO_ENDPOINT")
                or f"s3.{region}.amazonaws.com"
            )
            self.minio_access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
            self.minio_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
            self.minio_bucket = (
                os.environ.get("S3_BUCKET_NAME")
                or os.environ.get("LAKEHOUSE_BUCKET")
                or ""
            )
            self.minio_secure = True
            self.aws_session_token = os.environ.get("AWS_SESSION_TOKEN", "")

    @property
    def storage_provider(self) -> str:
        return _storage_provider()

    @property
    def storage_region(self) -> str | None:
        if self.storage_provider == "gcs":
            return "auto"
        if self.storage_provider == "s3":
            return (
                os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
                or "us-east-1"
            )
        return None

    @property
    def resolved_minio(self) -> dict:
        access_key = (self.minio_access_key or "").strip()
        secret_key = (self.minio_secret_key or "").strip()
        session_token = (self.aws_session_token or "").strip()
        if bool(access_key) != bool(secret_key):
            raise RuntimeError("storage_credentials_missing")
        if self.storage_provider in {"gcs", "minio"} and not access_key:
            raise RuntimeError("storage_credentials_missing")
        if session_token and not access_key:
            raise RuntimeError("storage_credentials_missing")
        if not self.minio_endpoint or not self.minio_bucket:
            raise RuntimeError("storage_bucket_missing")
        return {
            "endpoint": self.minio_endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "session_token": session_token,
            "bucket": self.minio_bucket,
            "secure": self.minio_secure,
            "provider": self.storage_provider,
            "region": self.storage_region,
        }


settings = Settings()
