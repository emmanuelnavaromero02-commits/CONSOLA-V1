from __future__ import annotations

from pydantic import Field
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
    pg_password: str = Field(..., min_length=1)

    # MinIO fallback
    minio_endpoint:   str  = "minio:9000"
    minio_access_key: str  = "minio"
    minio_secret_key: str = ""
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
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def gold_database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@postgres_gold:5433/{self.pg_db}_gold"
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
