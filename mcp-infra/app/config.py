from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Airflow ────────────────────────────────────────────────────────────────
    airflow_url:      str = "http://airflow:8080"
    airflow_user:     str = Field(..., min_length=1)
    airflow_password: str = Field(..., min_length=1)
    airflow_dags_path: str = "/opt/airflow/dags"

    # ── MinIO ──────────────────────────────────────────────────────────────────
    minio_endpoint:   str  = "minio:9000"
    minio_access_key: str  = "minio"
    minio_secret_key: str = ""
    minio_bucket:     str  = "lakehouse"
    minio_secure:     bool = False

    # ── PostgreSQL main (modecissions) ─────────────────────────────────────────
    pg_host:     str = "postgres"
    pg_port:     int = 5432
    pg_db:       str = "modecissions"
    pg_user:     str = "postgres"
    pg_password: str = Field(..., min_length=1)

    # ── PostgreSQL gold ────────────────────────────────────────────────────────
    pg_gold_host: str = "postgres_gold"
    pg_gold_port: int = 5433
    pg_gold_db:   str = "modecissions_gold"

    # ── Vault ──────────────────────────────────────────────────────────────────
    vault_url: str = "http://vault:8300"

    # ── Superset ───────────────────────────────────────────────────────────────
    superset_url:      str = "http://superset:8088"
    superset_user:     str = Field(..., min_length=1)
    superset_password: str = Field(..., min_length=1)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
