from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "hubspot"

    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")
    gold_database_url_override: str | None = Field(default=None, alias="GOLD_DATABASE_URL")

    # HubSpot CRM API (v3) — REST, cursor-paginated, Private App bearer token.
    hubspot_base_url:   str = "https://api.hubapi.com"
    hubspot_api_token:  str | None = (
        os.environ.get("HUBSPOT_API_TOKEN")
        or os.environ.get("HUBSPOT_TOKEN")
        or os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
    )
    hubspot_page_size:  int = 100
    hubspot_timeout:    int = 120

    # PostgreSQL fallback (used when vault is unreachable)
    pg_host:     str = "postgres"
    pg_port:     int = 5432
    pg_db:       str = "modecissions"

    # MinIO fallback
    minio_endpoint:   str  = "minio:9000"
    minio_access_key: str  = Field(default_factory=lambda: os.environ["MINIO_ACCESS_KEY"])
    minio_secret_key: str  = Field(default_factory=lambda: os.environ["MINIO_SECRET_KEY"])
    minio_bucket:     str  = "lakehouse"
    minio_secure:     bool = False

    # Airflow — si está configurado, extract() delega al DAG en lugar de correr inline
    airflow_url:      str | None = None   # e.g. http://airflow:8080
    airflow_user:     str | None = None
    airflow_password: str | None = None

    # Demo
    use_demo_data: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

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
        return {
            "endpoint":   self.minio_endpoint,
            "access_key": self.minio_access_key,
            "secret_key": self.minio_secret_key,
            "bucket":     self.minio_bucket,
            "secure":     self.minio_secure,
        }


settings = Settings()
