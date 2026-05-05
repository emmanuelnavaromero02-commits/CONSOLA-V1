from __future__ import annotations
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Airflow ────────────────────────────────────────────────────────────────
    airflow_url:      str = "http://airflow:8080"
    airflow_user:     str = "admin"
    airflow_password: str = "admin"
    airflow_dags_path: str = "/opt/airflow/dags"

    # ── MinIO ──────────────────────────────────────────────────────────────────
    minio_endpoint:   str  = "minio:9000"
    minio_access_key: str  = "minio"
    minio_secret_key: str  = "minio123"
    minio_bucket:     str  = "lakehouse"
    minio_secure:     bool = False

    # ── PostgreSQL main (modecissions) ─────────────────────────────────────────
    pg_host:     str = "postgres"
    pg_port:     int = 5432
    pg_db:       str = "modecissions"
    pg_user:     str = "postgres"
    pg_password: str = "postgres"

    # ── PostgreSQL gold ────────────────────────────────────────────────────────
    pg_gold_host: str = "postgres_gold"
    pg_gold_port: int = 5433
    pg_gold_db:   str = "modecissions_gold"

    # ── Vault ──────────────────────────────────────────────────────────────────
    vault_url: str = "http://vault:8300"

    # ── Superset ───────────────────────────────────────────────────────────────
    superset_url:      str = "http://superset:8088"
    superset_user:     str = "admin"
    superset_password: str = "admin"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
