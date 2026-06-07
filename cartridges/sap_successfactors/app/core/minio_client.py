from __future__ import annotations

import json
from datetime import datetime
from urllib import request

from minio import Minio
from minio.credentials import Credentials, Provider

from app.core.config import settings

_IMDS_BASE_URL = "http://169.254.169.254"


class Ec2ImdsV2Provider(Provider):
    """MinIO credentials provider for EC2 instance profiles using IMDSv2."""

    def __init__(self, base_url: str = _IMDS_BASE_URL, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._credentials: Credentials | None = None

    def _read(self, path: str, *, token: str | None = None, method: str = "GET") -> str:
        headers = {}
        if token:
            headers["X-aws-ec2-metadata-token"] = token
        if method == "PUT":
            headers["X-aws-ec2-metadata-token-ttl-seconds"] = "21600"
        req = request.Request(f"{self._base_url}{path}", headers=headers, method=method)
        with request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - IMDS link-local only.
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
    endpoint = str(settings.minio_endpoint or "").strip()
    access_key = str(settings.minio_access_key or "").strip()
    secret_key = str(settings.minio_secret_key or "").strip()
    if "amazonaws.com" in endpoint.lower() and not (access_key or secret_key):
        return Minio(
            endpoint=endpoint,
            credentials=Ec2ImdsV2Provider(),
            secure=bool(settings.minio_secure),
        )
    return Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=bool(settings.minio_secure),
    )


def ensure_bucket_exists(bucket_name: str) -> None:
    client = get_minio_client()
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)


def upload_file_to_minio(local_path: str, object_name: str) -> None:
    client = get_minio_client()
    ensure_bucket_exists(settings.minio_bucket)
    client.fput_object(
        bucket_name=settings.minio_bucket,
        object_name=object_name,
        file_path=local_path,
    )
