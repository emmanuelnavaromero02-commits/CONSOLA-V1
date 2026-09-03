from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import request
from urllib.parse import urlsplit

from omega_lakehouse import storage_from_env
from minio import Minio
from minio.credentials import Credentials, Provider

_AWS_S3_HOST_MARKER = "amazonaws.com"
_IMDS_BASE_URL = "http://169.254.169.254"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True, repr=False)
class ResolvedStorageConfig:
    provider: str
    endpoint: str
    endpoint_url: str | None
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

    def validate(self) -> None:
        if not self.bucket:
            raise RuntimeError("storage_bucket_missing")
        if self.provider in {"gcs", "minio"} and not (
            self.access_key and self.secret_key
        ):
            raise RuntimeError("storage_credentials_missing")
        if bool(self.access_key) != bool(self.secret_key):
            raise RuntimeError("storage_credentials_missing")


def _endpoint_host(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return parsed.netloc or parsed.path


def _endpoint_url(raw: str, *, secure: bool) -> str:
    value = str(raw or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return value
    return f"{'https' if secure else 'http'}://{value}"


def resolve_storage_config(*, bucket: str | None = None) -> ResolvedStorageConfig:
    """Resolve storage while keeping GCS, AWS and local MinIO keys isolated."""

    endpoint_hint = _env("LAKEHOUSE_ENDPOINT")
    provider = _env("LAKEHOUSE_PROVIDER").lower()
    if provider not in {"gcs", "s3", "minio"}:
        if _env("GCS_BUCKET") or "storage.googleapis.com" in endpoint_hint.lower():
            provider = "gcs"
        elif _env("S3_BUCKET_NAME") or _is_aws_endpoint(endpoint_hint):
            provider = "s3"
        else:
            provider = "minio"

    if provider == "gcs":
        endpoint = _endpoint_host(
            _env("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
        )
        return ResolvedStorageConfig(
            provider="gcs",
            endpoint=endpoint,
            endpoint_url=_endpoint_url(endpoint, secure=True),
            # Exact GCS interoperability pair; never AWS/SES or MINIO aliases.
            access_key=_env("GCS_ACCESS_KEY_ID"),
            secret_key=_env("GCS_SECRET_ACCESS_KEY"),
            bucket=(bucket or _env("GCS_BUCKET") or _env("LAKEHOUSE_BUCKET")).strip(),
            secure=True,
            region="auto",
        )

    if provider == "s3":
        aws_access_key = _env("AWS_ACCESS_KEY_ID")
        aws_secret_key = _env("AWS_SECRET_ACCESS_KEY")
        if bool(aws_access_key) != bool(aws_secret_key):
            raise RuntimeError("storage_credentials_missing")
        if not aws_access_key:
            aws_access_key = ""
            aws_secret_key = ""
        endpoint_raw = (
            _env("S3_ENDPOINT_URL")
            or _env("AWS_S3_ENDPOINT_URL")
            or _env("LAKEHOUSE_ENDPOINT")
            or "s3.amazonaws.com"
        )
        return ResolvedStorageConfig(
            provider="s3",
            endpoint=_endpoint_host(endpoint_raw),
            endpoint_url=_endpoint_url(endpoint_raw, secure=True),
            access_key=aws_access_key,
            secret_key=aws_secret_key,
            bucket=(
                bucket
                or _env("S3_BUCKET_NAME")
                or _env("LAKEHOUSE_BUCKET")
            ).strip(),
            secure=True,
            region=_env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1",
        )

    endpoint_raw = _env("MINIO_ENDPOINT", "minio:9000")
    secure = _secure_from_env()
    return ResolvedStorageConfig(
        provider="minio",
        endpoint=_endpoint_host(endpoint_raw),
        endpoint_url=_endpoint_url(endpoint_raw, secure=secure),
        access_key=_env("MINIO_ACCESS_KEY"),
        secret_key=_env("MINIO_SECRET_KEY"),
        bucket=(bucket or _env("MINIO_BUCKET", "lakehouse")).strip(),
        secure=secure,
        region="us-east-1",
    )


def _is_aws_endpoint(endpoint: str | None) -> bool:
    return _AWS_S3_HOST_MARKER in (endpoint or "").lower()


def _secure_from_env() -> bool:
    return _env("MINIO_SECURE", "false").lower() == "true"


class Ec2ImdsV2Provider(Provider):
    """MinIO credentials provider for EC2 instance profiles using IMDSv2."""

    def __init__(self, base_url: str = _IMDS_BASE_URL, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._credentials: Credentials | None = None

    def _read(self, path: str, *, token: str | None = None, method: str = "GET") -> str:
        parsed = urlsplit(self._base_url)
        if parsed.scheme != "http" or parsed.netloc != "169.254.169.254":
            raise ValueError("EC2 IMDS provider only permits the link-local metadata endpoint")
        if not path.startswith("/latest/"):
            raise ValueError("EC2 IMDS provider only permits /latest metadata paths")
        headers = {}
        if token:
            headers["X-aws-ec2-metadata-token"] = token
        if method == "PUT":
            headers["X-aws-ec2-metadata-token-ttl-seconds"] = "21600"
        req = request.Request(f"{self._base_url}{path}", headers=headers, method=method)
        # Bandit B310 false positive: URL is restricted above to EC2 IMDSv2
        # link-local 169.254.169.254 and fixed /latest/* paths.
        with request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
            return resp.read().decode("utf-8")

    def retrieve(self) -> Credentials:
        if self._credentials and not self._credentials.is_expired():
            return self._credentials

        token = self._read("/latest/api/token", method="PUT")
        role_name = self._read("/latest/meta-data/iam/security-credentials/", token=token).splitlines()[0].strip()
        payload = json.loads(
            self._read(f"/latest/meta-data/iam/security-credentials/{role_name}", token=token)
        )
        if payload.get("Code", "Success") != "Success":
            raise ValueError(f"EC2 IMDS credential lookup failed: {payload.get('Message')}")
        expiration = datetime.fromisoformat(str(payload["Expiration"]).replace("Z", "+00:00"))
        self._credentials = Credentials(
            payload["AccessKeyId"],
            payload["SecretAccessKey"],
            session_token=payload.get("Token"),
            expiration=expiration,
        )
        return self._credentials


def get_minio_client() -> Minio:
    storage = resolve_storage_config()
    storage.validate()
    if storage.provider == "s3" and not (
        storage.access_key and storage.secret_key
    ):
        return Minio(
            endpoint=storage.endpoint,
            credentials=Ec2ImdsV2Provider(),
            secure=True,
            region=storage.region,
        )
    return Minio(
        storage.endpoint,
        access_key=storage.access_key,
        secret_key=storage.secret_key,
        secure=storage.secure,
        region=storage.region,
    )


def get_boto3_s3_client():
    import boto3
    from botocore.config import Config

    storage = resolve_storage_config()
    storage.validate()
    kwargs: dict = {
        "region_name": storage.region,
        "config": Config(
            connect_timeout=2,
            read_timeout=10,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    }
    if storage.endpoint_url:
        kwargs["endpoint_url"] = storage.endpoint_url
    if storage.access_key and storage.secret_key:
        kwargs["aws_access_key_id"] = storage.access_key
        kwargs["aws_secret_access_key"] = storage.secret_key
    return boto3.client("s3", **kwargs)


class LakehouseExplorerClient:
    """Boto3-shaped adapter used by the console object explorer."""

    def _storage(self, bucket: str):
        storage = resolve_storage_config(bucket=bucket)
        storage.validate()
        return storage_from_env(bucket=storage.bucket)

    def list_objects_v2(self, **kwargs) -> dict:
        bucket = kwargs["Bucket"]
        prefix = kwargs.get("Prefix") or ""
        max_keys = int(kwargs.get("MaxKeys") or 1000)
        cursor = kwargs.get("ContinuationToken")
        delimiter = kwargs.get("Delimiter")
        page = self._storage(bucket).list_page(
            prefix,
            page_size=max_keys,
            cursor=cursor,
            delimiter=delimiter,
        )
        now = datetime.now(timezone.utc)
        return {
            "Contents": [
                {
                    "Key": obj.key,
                    "Size": obj.size,
                    "LastModified": obj.updated_at or now,
                    "ETag": obj.etag or "",
                }
                for obj in page.objects
            ],
            "CommonPrefixes": [{"Prefix": prefix} for prefix in page.prefixes],
            "NextContinuationToken": page.next_cursor,
            "IsTruncated": page.is_truncated,
        }

    def generate_presigned_url(self, client_method: str, *, Params: dict, ExpiresIn: int) -> str:
        if client_method != "get_object":
            raise ValueError("only get_object presigned URLs are supported")
        storage = self._storage(Params["Bucket"])
        presign = getattr(storage, "presigned_get_url", None)
        if not presign:
            raise ValueError("provider does not support presigned downloads")
        return presign(Params["Key"], expires_in=ExpiresIn)

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self._storage(Bucket).delete_object(Key)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}


def get_lakehouse_explorer_client() -> LakehouseExplorerClient:
    return LakehouseExplorerClient()
