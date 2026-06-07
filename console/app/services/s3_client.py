from __future__ import annotations

import json
import os
from datetime import datetime
from urllib import request
from urllib.parse import urlsplit

from minio import Minio
from minio.credentials import Credentials, Provider

_AWS_S3_HOST_MARKER = "amazonaws.com"
_IMDS_BASE_URL = "http://169.254.169.254"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _is_aws_endpoint(endpoint: str | None) -> bool:
    return _AWS_S3_HOST_MARKER in (endpoint or "").lower()


def _secure_from_env() -> bool:
    return _env("MINIO_SECURE", "false").lower() == "true"


def _minio_endpoint_from_env() -> str:
    raw = _env("MINIO_ENDPOINT", "minio:9000")
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        return parsed.netloc
    return raw


def _boto_endpoint_from_env() -> str | None:
    endpoint_url = _env("S3_ENDPOINT_URL") or _env("AWS_S3_ENDPOINT_URL")
    if endpoint_url:
        return endpoint_url
    minio_endpoint = _env("MINIO_ENDPOINT")
    if not minio_endpoint:
        return None
    parsed = urlsplit(minio_endpoint)
    if parsed.scheme and parsed.netloc:
        return minio_endpoint
    scheme = "https" if _secure_from_env() else "http"
    return f"{scheme}://{minio_endpoint}"


def _static_access_key() -> str:
    return _env("AWS_ACCESS_KEY_ID") or _env("MINIO_ACCESS_KEY")


def _static_secret_key() -> str:
    return _env("AWS_SECRET_ACCESS_KEY") or _env("MINIO_SECRET_KEY")


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
    endpoint = _minio_endpoint_from_env()
    access_key = _static_access_key()
    secret_key = _static_secret_key()
    if _is_aws_endpoint(endpoint) and not (access_key and secret_key):
        return Minio(
            endpoint=endpoint,
            credentials=Ec2ImdsV2Provider(),
            secure=True,
        )
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=_secure_from_env(),
    )


def get_boto3_s3_client():
    import boto3
    from botocore.config import Config

    endpoint_url = _boto_endpoint_from_env()
    kwargs: dict = {
        "region_name": _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1",
        "config": Config(
            connect_timeout=2,
            read_timeout=10,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    }
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    access_key = _static_access_key()
    secret_key = _static_secret_key()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)
