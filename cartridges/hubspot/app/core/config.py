from __future__ import annotations

import os

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
    app_name: str = "hubspot"

    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")
    gold_database_url_override: str | None = Field(default=None, alias="GOLD_DATABASE_URL")

    hubspot_base_url:   str = "https://api.hubapi.com"
    hubspot_api_token:  str | None = (
        os.environ.get("HUBSPOT_API_TOKEN")
        or os.environ.get("HUBSPOT_TOKEN")
        or os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
    )
    hubspot_page_size:  int = 100
    hubspot_timeout:    int = 120

    pg_host:     str = "postgres"
    pg_port:     int = 5432
    pg_db:       str = "modecissions"

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

    airflow_url:      str | None = None
    airflow_user:     str | None = None
    airflow_password: str | None = None

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
    def pg_user(self) -> str:
        return os.environ["PG_USER"]

    @property
    def pg_password(self) -> str:
        return os.environ["PG_PASSWORD"]

    @property
    def database_url(self) -> str:
        if self.database_url_override and self.database_url_override.strip():
            return self.database_url_override
        pg_user = os.environ["PG_USER"]
        pg_password = os.environ["PG_PASSWORD"]
        return (
            f"postgresql+psycopg2://{pg_user}:{pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def gold_database_url(self) -> str:
        if self.gold_database_url_override and self.gold_database_url_override.strip():
            return self.gold_database_url_override
        pg_user = os.environ["PG_USER"]
        pg_password = os.environ["PG_PASSWORD"]
        return (
            f"postgresql+psycopg2://{pg_user}:{pg_password}"
            f"@postgres_gold:5433/{self.pg_db}_gold"
        )

    @property
    def asyncpg_dsn(self) -> str:
        return self.database_url.replace("postgresql+psycopg2://", "postgresql://")

    @property
    def resolved_minio(self) -> dict:
        provider = _storage_provider()
        access_key = (self.minio_access_key or "").strip()
        secret_key = (self.minio_secret_key or "").strip()
        session_token = (self.aws_session_token or "").strip()
        if bool(access_key) != bool(secret_key):
            raise RuntimeError("storage_credentials_missing")
        if provider in {"gcs", "minio"} and not access_key:
            raise RuntimeError("storage_credentials_missing")
        if session_token and not access_key:
            raise RuntimeError("storage_credentials_missing")
        if not self.minio_endpoint or not self.minio_bucket:
            raise RuntimeError("storage_bucket_missing")
        return {
            "endpoint":   self.minio_endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "session_token": session_token,
            "bucket":     self.minio_bucket,
            "secure":     self.minio_secure,
            "provider":   provider,
            "region": (
                "auto"
                if provider == "gcs"
                else (
                    os.environ.get("AWS_REGION")
                    or os.environ.get("AWS_DEFAULT_REGION")
                    or "us-east-1"
                )
                if provider == "s3"
                else None
            ),
        }


settings = Settings()
