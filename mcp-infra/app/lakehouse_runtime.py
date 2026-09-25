from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib import request
from urllib.parse import urlsplit

from app.config import ResolvedStorageConfig, settings
from minio.credentials import Credentials, Provider


class Ec2ImdsV2Provider(Provider):

    def __init__(
        self,
        base_url: str = "http://169.254.169.254",
        timeout: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._credentials: Credentials | None = None

    def _read(
        self,
        path: str,
        *,
        token: str | None = None,
        method: str = "GET",
    ) -> str:
        parsed = urlsplit(self._base_url)
        if parsed.scheme != "http" or parsed.netloc != "169.254.169.254":
            raise ValueError("EC2 IMDS provider only permits link-local metadata")
        if not path.startswith("/latest/"):
            raise ValueError("EC2 IMDS provider only permits /latest paths")
        headers: dict[str, str] = {}
        if token:
            headers["X-aws-ec2-metadata-token"] = token
        if method == "PUT":
            headers["X-aws-ec2-metadata-token-ttl-seconds"] = "21600"
        req = request.Request(
            f"{self._base_url}{path}",
            headers=headers,
            method=method,
        )
        with request.urlopen(req, timeout=self._timeout) as response:  # nosec B310
            return response.read().decode("utf-8")

    def retrieve(self) -> Credentials:
        if self._credentials and not self._credentials.is_expired():
            return self._credentials
        token = self._read("/latest/api/token", method="PUT")
        role_name = self._read(
            "/latest/meta-data/iam/security-credentials/",
            token=token,
        ).splitlines()[0].strip()
        payload = json.loads(
            self._read(
                f"/latest/meta-data/iam/security-credentials/{role_name}",
                token=token,
            )
        )
        if payload.get("Code", "Success") != "Success":
            raise RuntimeError("storage_credentials_missing")
        self._credentials = Credentials(
            payload["AccessKeyId"],
            payload["SecretAccessKey"],
            session_token=payload.get("Token"),
            expiration=datetime.fromisoformat(
                str(payload["Expiration"]).replace("Z", "+00:00")
            ),
        )
        return self._credentials


def configure_duckdb_s3(connection: Any) -> ResolvedStorageConfig:

    storage = settings.resolved_storage
    storage.require_interoperability_pair()
    try:
        _configure_duckdb_s3(connection, storage)
    except Exception as exc:  # noqa: BLE001 - public error must be stable.
        code = str(exc)
        if code not in {
            "storage_credentials_missing",
            "storage_signature_invalid",
            "storage_access_denied",
            "storage_bucket_missing",
        }:
            code = "storage_access_denied"
        raise RuntimeError(code) from None
    return storage


def _configure_duckdb_s3(
    connection: Any,
    storage: ResolvedStorageConfig,
) -> None:

    if storage.access_key and storage.secret_key:
        connection.execute("SET s3_access_key_id = ?", [storage.access_key])
        connection.execute("SET s3_secret_access_key = ?", [storage.secret_key])
    else:
        connection.execute("LOAD aws;")
        try:
            connection.execute("CALL load_aws_credentials();")
        except Exception:  # noqa: BLE001
            try:
                connection.execute(
                    "CREATE OR REPLACE SECRET omega_s3 ("
                    "TYPE S3, PROVIDER CREDENTIAL_CHAIN);"
                )
            except Exception:  # noqa: BLE001
                raise RuntimeError("storage_credentials_missing") from None

    connection.execute("SET s3_endpoint = ?", [storage.endpoint])
    connection.execute(
        "SET s3_url_style='"
        + ("vhost" if storage.provider == "s3" else "path")
        + "';"
    )
    connection.execute(
        "SET s3_use_ssl=" + ("true" if storage.secure else "false") + ";"
    )
    connection.execute("SET s3_region = ?", [storage.region])


def minio_compatible_client():

    from minio import Minio

    storage = settings.resolved_storage
    storage.require_interoperability_pair()
    if storage.provider == "s3" and not storage.access_key:
        return Minio(
            storage.endpoint,
            credentials=Ec2ImdsV2Provider(),
            secure=True,
            region=storage.region,
        )
    return Minio(
        storage.endpoint,
        access_key=storage.access_key or None,
        secret_key=storage.secret_key or None,
        secure=storage.secure,
        region=storage.region or None,
    )


def ensure_local_bucket(client: Any) -> str:

    storage = settings.resolved_storage
    storage.require_interoperability_pair()
    if storage.provider == "minio" and not client.bucket_exists(storage.bucket):
        client.make_bucket(storage.bucket)
    return storage.bucket
