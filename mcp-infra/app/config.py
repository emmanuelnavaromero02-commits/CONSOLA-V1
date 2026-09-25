from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _endpoint_host(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return parsed.netloc or parsed.path


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True, repr=False)
class ResolvedStorageConfig:
    provider: str
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    secure: bool
    region: str

    def __repr__(self) -> str:
        return (
            "ResolvedStorageConfig("
            f"provider={self.provider!r}, endpoint={self.endpoint!r}, "
            f"bucket={self.bucket!r}, secure={self.secure!r}, "
            f"region={self.region!r}, access_key=********, secret_key=********)"
        )

    def require_interoperability_pair(self) -> None:

        if self.provider in {"gcs", "minio"} and not (
            self.access_key and self.secret_key
        ):
            raise RuntimeError("storage_credentials_missing")
        if bool(self.access_key) != bool(self.secret_key):
            raise RuntimeError("storage_credentials_missing")
        if not self.bucket:
            raise RuntimeError("storage_bucket_missing")


def resolve_storage_config(
    *,
    minio_endpoint: str = "minio:9000",
    minio_access_key: str = "minio",
    minio_secret_key: str = "",
    minio_bucket: str = "lakehouse",
    minio_secure: bool = False,
) -> ResolvedStorageConfig:

    endpoint_hint = _env("LAKEHOUSE_ENDPOINT")
    provider = _env("LAKEHOUSE_PROVIDER").lower()
    if provider not in {"gcs", "s3", "minio"}:
        if _env("GCS_BUCKET") or "storage.googleapis.com" in endpoint_hint.lower():
            provider = "gcs"
        elif _env("S3_BUCKET_NAME") or "amazonaws.com" in endpoint_hint.lower():
            provider = "s3"
        else:
            provider = "minio"

    if provider == "gcs":
        return ResolvedStorageConfig(
            provider="gcs",
            endpoint=_endpoint_host(
                _env("LAKEHOUSE_ENDPOINT") or "storage.googleapis.com"
            ),
            access_key=_env("GCS_ACCESS_KEY_ID"),
            secret_key=_env("GCS_SECRET_ACCESS_KEY"),
            bucket=_env("GCS_BUCKET") or _env("LAKEHOUSE_BUCKET"),
            secure=True,
            region="auto",
        )

    if provider == "s3":
        return ResolvedStorageConfig(
            provider="s3",
            endpoint=_endpoint_host(
                _env("S3_ENDPOINT_URL")
                or _env("AWS_S3_ENDPOINT_URL")
                or _env("LAKEHOUSE_ENDPOINT")
                or "s3.amazonaws.com"
            ),
            access_key=_env("AWS_ACCESS_KEY_ID"),
            secret_key=_env("AWS_SECRET_ACCESS_KEY"),
            bucket=_env("S3_BUCKET_NAME") or _env("LAKEHOUSE_BUCKET"),
            secure=True,
            region=_env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1",
        )

    return ResolvedStorageConfig(
        provider="minio",
        endpoint=_endpoint_host(_env("MINIO_ENDPOINT") or minio_endpoint),
        access_key=_env("MINIO_ACCESS_KEY") or minio_access_key,
        secret_key=_env("MINIO_SECRET_KEY") or minio_secret_key,
        bucket=_env("MINIO_BUCKET") or minio_bucket,
        secure=_env_bool("MINIO_SECURE", minio_secure),
        region="us-east-1",
    )


class Settings(BaseSettings):
    airflow_url:      str = "http://airflow:8080"
    airflow_user:     str = Field(..., min_length=1)
    airflow_password: str = Field(..., min_length=1)
    airflow_dags_path: str = "/opt/airflow/dags"

    minio_endpoint:   str  = "minio:9000"
    minio_access_key: str  = "minio"
    minio_secret_key: str = ""
    minio_bucket:     str  = "lakehouse"
    minio_secure:     bool = False

    storage_provider: str = "minio"
    storage_region: str = "us-east-1"

    pg_host:     str = "postgres"
    pg_port:     int = 5432
    pg_db:       str = "modecissions"
    pg_user:     str = "omega_mcp_infra"
    pg_password: str = Field(..., min_length=1)

    pg_gold_host: str = "postgres_gold"
    pg_gold_port: int = 5433
    pg_gold_db:   str = "modecissions_gold"
    pg_gold_user: str = ""
    pg_gold_password: str = ""

    vault_url: str = "http://vault:8300"

    superset_url:      str = "http://superset:8088"
    superset_user:     str = Field(..., min_length=1)
    superset_password: str = Field(..., min_length=1)

    def __init__(self, **values):
        super().__init__(**values)
        resolved = resolve_storage_config(
            minio_endpoint=self.minio_endpoint,
            minio_access_key=self.minio_access_key,
            minio_secret_key=self.minio_secret_key,
            minio_bucket=self.minio_bucket,
            minio_secure=self.minio_secure,
        )
        self.minio_endpoint = resolved.endpoint
        self.minio_access_key = resolved.access_key
        self.minio_secret_key = resolved.secret_key
        self.minio_bucket = resolved.bucket
        self.minio_secure = resolved.secure
        self.storage_provider = resolved.provider
        self.storage_region = resolved.region

    @property
    def resolved_storage(self) -> ResolvedStorageConfig:
        return ResolvedStorageConfig(
            provider=self.storage_provider,
            endpoint=self.minio_endpoint,
            access_key=self.minio_access_key,
            secret_key=self.minio_secret_key,
            bucket=self.minio_bucket,
            secure=self.minio_secure,
            region=self.storage_region,
        )

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
