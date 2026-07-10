from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SecretValue:
    _value: str

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "SecretValue('********')"

    def __str__(self) -> str:
        return "********"


@dataclass(frozen=True)
class LakehouseStorageConfig:
    provider: Literal["minio", "s3", "gcs"]
    bucket: str
    endpoint: str | None = None
    region: str | None = None
    secure: bool = True
    access_key: SecretValue | None = None
    secret_key: SecretValue | None = None

    def __repr__(self) -> str:
        return (
            "LakehouseStorageConfig("
            f"provider={self.provider!r}, bucket={self.bucket!r}, "
            f"endpoint={self.endpoint!r}, region={self.region!r}, secure={self.secure!r}, "
            "access_key=********, secret_key=********)"
        )


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _secret(value: str) -> SecretValue | None:
    return SecretValue(value) if value else None


def _bool(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _with_scheme(endpoint: str | None, *, secure: bool) -> str | None:
    if not endpoint:
        return None
    parsed = urlsplit(endpoint)
    if parsed.scheme and parsed.netloc:
        return endpoint
    return f"{'https' if secure else 'http'}://{endpoint}"


def _infer_provider(endpoint: str | None) -> Literal["minio", "s3", "gcs"]:
    explicit = _env("LAKEHOUSE_PROVIDER").lower()
    if explicit in {"minio", "s3", "gcs"}:
        return explicit  # type: ignore[return-value]
    if _env("GCS_BUCKET") or "storage.googleapis.com" in (endpoint or "").lower():
        return "gcs"
    if "amazonaws.com" in (endpoint or "").lower() or _env("S3_BUCKET_NAME"):
        return "s3"
    return "minio"


def config_from_env(*, bucket: str | None = None) -> LakehouseStorageConfig:
    endpoint = (
        _env("LAKEHOUSE_ENDPOINT")
        or _env("S3_ENDPOINT_URL")
        or _env("AWS_S3_ENDPOINT_URL")
        or _env("MINIO_ENDPOINT")
        or None
    )
    provider = _infer_provider(endpoint)
    secure_default = provider != "minio" or "amazonaws.com" in (endpoint or "").lower()
    secure = _bool(_env("LAKEHOUSE_SECURE") or _env("MINIO_SECURE"), secure_default)
    access = (
        _env("LAKEHOUSE_ACCESS_KEY")
        or _env("AWS_ACCESS_KEY_ID")
        or _env("MINIO_ACCESS_KEY")
    )
    secret = (
        _env("LAKEHOUSE_SECRET_KEY")
        or _env("AWS_SECRET_ACCESS_KEY")
        or _env("MINIO_SECRET_KEY")
    )
    selected_bucket = (
        bucket
        or _env("LAKEHOUSE_BUCKET")
        or _env("MINIO_BUCKET")
        or _env("S3_BUCKET_NAME")
        or _env("GCS_BUCKET")
        or "lakehouse"
    )
    return LakehouseStorageConfig(
        provider=provider,
        bucket=selected_bucket,
        endpoint=_with_scheme(endpoint, secure=secure) if provider in {"minio", "s3"} else endpoint,
        region=_env("LAKEHOUSE_REGION") or _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or None,
        secure=secure,
        access_key=_secret(access),
        secret_key=_secret(secret),
    )
