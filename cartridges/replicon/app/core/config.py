from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "replicon"

    # Replicon API
    replicon_base_url:      str   = "https://na5.replicon.com/analytics"
    replicon_api_token:     str | None = None
    replicon_poll_interval: float = 2.0
    replicon_poll_timeout:  int   = 300

    # PostgreSQL fallback (used when vault is unreachable)
    pg_host:     str = "postgres"
    pg_port:     int = 5432
    pg_db:       str = "modecissions"
    pg_user:     str = "postgres"
    pg_password: str = "postgres"

    # MinIO fallback
    minio_endpoint:   str  = "minio:9000"
    minio_access_key: str  = "minio"
    minio_secret_key: str  = "minio123"
    minio_bucket:     str  = "lakehouse"
    minio_secure:     bool = False

    # Airflow — si está configurado, extract() delega al DAG en lugar de correr inline
    airflow_url:      str | None = None   # e.g. http://airflow:8080
    airflow_user:     str        = "admin"
    airflow_password: str        = "admin"

    # Demo
    use_demo_data: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def database_url(self) -> str:
        # Sprint v1.40.2: respect the DATABASE_URL env var first. The
        # OMEGA compose passes
        # ``postgresql+psycopg2://omega_cartridge_replicon:<pwd>@postgres:5432/modecissions``
        # there, and pre-v1.40.2 this getter ignored it and rebuilt
        # the URL from ``pg_user=postgres`` / ``pg_password=postgres``
        # defaults, which made the cartridge try to log in as the
        # postgres superuser and fail asyncpg auth in a restart loop.
        # The override stays opt-in: dev runs without DATABASE_URL
        # still hit the legacy field-based path so the original ZIP
        # contract is intact.
        env_url = os.environ.get("DATABASE_URL", "").strip()
        if env_url:
            return env_url
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def gold_database_url(self) -> str:
        env_url = os.environ.get("GOLD_DATABASE_URL", "").strip()
        if env_url:
            return env_url
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@postgres_gold:5433/{self.pg_db}_gold"
        )

    @property
    def asyncpg_dsn(self) -> str:
        """asyncpg-compatible DSN — drops the SQLAlchemy ``+psycopg2``
        driver hint that asyncpg refuses to parse."""
        return self.database_url.replace(
            "postgresql+psycopg2://", "postgresql://"
        )

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
