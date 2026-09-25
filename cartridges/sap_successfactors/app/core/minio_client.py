from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib import request
from urllib.parse import urlsplit

from minio import Minio
from minio.credentials import Credentials, Provider

from app.core.config import settings

_IMDS_BASE_URL = "http://169.254.169.254"
_SAFE_STORAGE_CODES = frozenset(
    {
        "storage_credentials_missing",
        "storage_signature_invalid",
        "storage_access_denied",
        "storage_bucket_missing",
    }
)


class StoragePreflightError(RuntimeError):

    def __init__(self, code: str) -> None:
        if code not in _SAFE_STORAGE_CODES:
            code = "storage_access_denied"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class StorageRuntimeConfig:
    provider: str
    endpoint: str
    bucket: str
    secure: bool
    region: str | None
    access_key: str | None = None
    secret_key: str | None = None
    session_token: str | None = None

    def __repr__(self) -> str:
        return (
            "StorageRuntimeConfig("
            f"provider={self.provider!r}, endpoint={self.endpoint!r}, "
            f"bucket={self.bucket!r}, secure={self.secure!r}, "
            f"region={self.region!r}, credentials=********)"
        )


def _value(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _endpoint_host(endpoint: str) -> str:
    raw = endpoint.strip()
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise StoragePreflightError("storage_credentials_missing")
    host = parsed.netloc or parsed.path
    if not host:
        raise StoragePreflightError("storage_credentials_missing")
    return host.rstrip("/")


def _provider_for(endpoint: str) -> str:
    explicit = _value("lakehouse_provider").lower()
    inferred = (
        "gcs"
        if "storage.googleapis.com" in endpoint.lower()
        else "s3"
        if "amazonaws.com" in endpoint.lower()
        else "minio"
    )
    if explicit and explicit not in {"gcs", "s3", "minio"}:
        raise StoragePreflightError("storage_credentials_missing")
    if explicit and inferred in {"gcs", "s3"} and explicit != inferred:
        raise StoragePreflightError("storage_credentials_missing")
    return explicit or inferred


def _complete_pair(
    access_key: str, secret_key: str, *, required: bool
) -> tuple[str | None, str | None]:
    if bool(access_key) != bool(secret_key) or (required and not access_key):
        raise StoragePreflightError("storage_credentials_missing")
    return (access_key or None, secret_key or None)


def resolve_storage_config() -> StorageRuntimeConfig:

    explicit_provider = _value("lakehouse_provider").lower()
    endpoint_value = _value("lakehouse_endpoint")
    if not endpoint_value and explicit_provider == "gcs":
        endpoint_value = "storage.googleapis.com"
    elif not endpoint_value and explicit_provider == "s3":
        endpoint_value = "s3.amazonaws.com"
    elif not endpoint_value:
        endpoint_value = _value("minio_endpoint")
    endpoint = _endpoint_host(endpoint_value)
    provider = _provider_for(endpoint)

    if provider == "gcs":
        access_key, secret_key = _complete_pair(
            _value("gcs_access_key_id"),
            _value("gcs_secret_access_key"),
            required=True,
        )
        bucket = _value("gcs_bucket") or _value("lakehouse_bucket")
        if not bucket:
            raise StoragePreflightError("storage_bucket_missing")
        return StorageRuntimeConfig(
            provider="gcs",
            endpoint=endpoint,
            bucket=bucket,
            secure=True,
            region="auto",
            access_key=access_key,
            secret_key=secret_key,
        )

    if provider == "s3":
        access_key, secret_key = _complete_pair(
            _value("aws_access_key_id"),
            _value("aws_secret_access_key"),
            required=False,
        )
        session_token = _value("aws_session_token")
        if session_token and not access_key:
            raise StoragePreflightError("storage_credentials_missing")
        bucket = _value("s3_bucket_name") or _value("lakehouse_bucket")
        if not bucket:
            raise StoragePreflightError("storage_bucket_missing")
        return StorageRuntimeConfig(
            provider="s3",
            endpoint=endpoint,
            bucket=bucket,
            secure=True,
            region=_value("aws_region") or _value("aws_default_region") or "us-east-1",
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token or None,
        )

    access_key, secret_key = _complete_pair(
        _value("minio_access_key"),
        _value("minio_secret_key"),
        required=True,
    )
    bucket = _value("minio_bucket") or _value("lakehouse_bucket")
    if not bucket:
        raise StoragePreflightError("storage_bucket_missing")
    return StorageRuntimeConfig(
        provider="minio",
        endpoint=endpoint,
        bucket=bucket,
        secure=bool(settings.minio_secure),
        region=None,
        access_key=access_key,
        secret_key=secret_key,
    )


class Ec2ImdsV2Provider(Provider):

    def __init__(self, base_url: str = _IMDS_BASE_URL, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._credentials: Credentials | None = None

    def _read(self, path: str, *, token: str | None = None, method: str = "GET") -> str:
        parsed = urlsplit(self._base_url)
        if parsed.scheme != "http" or parsed.netloc != "169.254.169.254":
            raise ValueError(
                "EC2 IMDS provider only permits the link-local metadata endpoint"
            )
        if not path.startswith("/latest/"):
            raise ValueError("EC2 IMDS provider only permits /latest metadata paths")
        headers = {}
        if token:
            headers["X-aws-ec2-metadata-token"] = token
        if method == "PUT":
            headers["X-aws-ec2-metadata-token-ttl-seconds"] = "21600"
        req = request.Request(f"{self._base_url}{path}", headers=headers, method=method)
        with request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
            return resp.read().decode("utf-8")

    def retrieve(self) -> Credentials:
        if self._credentials and not self._credentials.is_expired():
            return self._credentials

        token = self._read("/latest/api/token", method="PUT")
        role_name = (
            self._read("/latest/meta-data/iam/security-credentials/", token=token)
            .splitlines()[0]
            .strip()
        )
        payload = json.loads(
            self._read(
                f"/latest/meta-data/iam/security-credentials/{role_name}", token=token
            )
        )
        if payload.get("Code", "Success") != "Success":
            raise ValueError(
                f"EC2 IMDS credential lookup failed: {payload.get('Message')}"
            )
        expiration = datetime.fromisoformat(
            str(payload["Expiration"]).replace("Z", "+00:00")
        )
        self._credentials = Credentials(
            payload["AccessKeyId"],
            payload["SecretAccessKey"],
            session_token=payload.get("Token"),
            expiration=expiration,
        )
        return self._credentials


def get_minio_client(*, config: StorageRuntimeConfig | None = None) -> Minio:

    resolved = config or resolve_storage_config()
    common: dict[str, object] = {
        "endpoint": resolved.endpoint,
        "secure": resolved.secure,
    }
    if resolved.region:
        common["region"] = resolved.region
    if resolved.provider == "s3" and not resolved.access_key:
        return Minio(credentials=Ec2ImdsV2Provider(), **common)
    if resolved.session_token:
        common["session_token"] = resolved.session_token
    return Minio(
        access_key=resolved.access_key,
        secret_key=resolved.secret_key,
        **common,
    )


def _preflight_code(exc: Exception) -> str:
    raw_code = str(getattr(exc, "code", "") or "").lower()
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        raw_code += " " + str((response.get("Error") or {}).get("Code") or "").lower()
    class_name = type(exc).__name__.lower()
    message = str(exc).lower()
    markers = " ".join((raw_code, class_name, message))
    if any(
        token in markers
        for token in ("signaturedoesnotmatch", "invalidaccesskeyid", "invalidsignature")
    ):
        return "storage_signature_invalid"
    if any(
        token in markers
        for token in ("nosuchbucket", "no such bucket", "notfound", "not found", "404")
    ):
        return "storage_bucket_missing"
    if any(
        token in markers
        for token in (
            "credential",
            "access key",
            "secret key",
            "metadata token",
            "metadata endpoint",
            "169.254.169.254",
        )
    ):
        return "storage_credentials_missing"
    return "storage_access_denied"


def _bucket_ready(
    client: Minio,
    config: StorageRuntimeConfig,
    *,
    create_local: bool,
) -> None:
    try:
        if config.provider != "minio":
            iterator = client.list_objects(
                config.bucket,
                prefix="_omega_storage_preflight_/",
                recursive=False,
            )
            next(iter(iterator), None)
            return
        exists = client.bucket_exists(config.bucket)
        if not exists and create_local:
            client.make_bucket(config.bucket)
            exists = client.bucket_exists(config.bucket)
        if not exists:
            raise StoragePreflightError("storage_bucket_missing")
    except StoragePreflightError:
        raise
    except Exception as exc:
        raise StoragePreflightError(_preflight_code(exc)) from None


def check_storage_access() -> dict[str, object]:

    try:
        config = resolve_storage_config()
        client = get_minio_client(config=config)
        _bucket_ready(client, config, create_local=True)
    except StoragePreflightError as exc:
        return {
            "component": "minio",
            "configured": False,
            "missing": [],
            "code": exc.code,
        }
    except Exception as exc:
        return {
            "component": "minio",
            "configured": False,
            "missing": [],
            "code": _preflight_code(exc),
        }
    return {"component": "minio", "configured": True, "missing": []}


def require_storage_access() -> StorageRuntimeConfig:

    try:
        config = resolve_storage_config()
        client = get_minio_client(config=config)
        _bucket_ready(client, config, create_local=True)
        return config
    except StoragePreflightError:
        raise
    except Exception as exc:
        raise StoragePreflightError(_preflight_code(exc)) from None


def run_storage_canary() -> dict[str, object]:

    config = require_storage_access()
    client = get_minio_client(config=config)
    payload = b"omega-successfactors-storage-canary:" + os.urandom(32)
    checksum = hashlib.sha256(payload).hexdigest()
    object_name = f"_canary/sap_successfactors/{uuid.uuid4().hex}.bin"
    cleanup_required = False
    failure: StoragePreflightError | None = None
    try:
        cleanup_required = True
        client.put_object(
            config.bucket,
            object_name,
            io.BytesIO(payload),
            len(payload),
            content_type="application/octet-stream",
            metadata={"omega-sha256": checksum},
        )
        stat = client.stat_object(config.bucket, object_name)
        metadata = {
            str(key).lower(): str(value)
            for key, value in dict(getattr(stat, "metadata", {}) or {}).items()
        }
        observed_checksum = (
            metadata.get("x-amz-meta-omega-sha256")
            or metadata.get("x-goog-meta-omega-sha256")
            or metadata.get("omega-sha256")
        )
        if int(getattr(stat, "size", -1)) != len(payload) or observed_checksum != checksum:
            failure = StoragePreflightError("storage_access_denied")
        else:
            response = client.get_object(config.bucket, object_name)
            try:
                observed_payload = response.read()
            finally:
                response.close()
                response.release_conn()
            if (
                not isinstance(observed_payload, bytes)
                or hashlib.sha256(observed_payload).hexdigest() != checksum
            ):
                failure = StoragePreflightError("storage_access_denied")
    except StoragePreflightError as exc:
        failure = exc
    except Exception as exc:
        failure = StoragePreflightError(_preflight_code(exc))
    finally:
        if cleanup_required:
            try:
                client.remove_object(config.bucket, object_name)
            except Exception as exc:
                failure = failure or StoragePreflightError(_preflight_code(exc))
    if failure is not None:
        raise failure
    return {
        "status": "ok",
        "checksum_verified": True,
        "deleted": True,
        "provider": config.provider,
    }


def active_storage_bucket() -> str:

    endpoint = _value("lakehouse_endpoint") or _value("minio_endpoint")
    explicit = _value("lakehouse_provider").lower()
    provider = explicit or (
        "gcs"
        if "storage.googleapis.com" in endpoint.lower()
        else "s3"
        if "amazonaws.com" in endpoint.lower()
        else "minio"
    )
    if provider == "gcs":
        bucket = (
            _value("gcs_bucket") or _value("lakehouse_bucket") or _value("minio_bucket")
        )
    elif provider == "s3":
        bucket = (
            _value("s3_bucket_name")
            or _value("lakehouse_bucket")
            or _value("minio_bucket")
        )
    else:
        bucket = _value("minio_bucket") or _value("lakehouse_bucket")
    if not bucket:
        raise StoragePreflightError("storage_bucket_missing")
    return bucket


def ensure_bucket_exists(
    bucket_name: str,
    *,
    client: Minio | None = None,
    config: StorageRuntimeConfig | None = None,
) -> None:
    resolved = config or resolve_storage_config()
    if bucket_name != resolved.bucket:
        raise StoragePreflightError("storage_bucket_missing")
    _bucket_ready(
        client or get_minio_client(config=resolved), resolved, create_local=True
    )


def upload_file_to_minio(local_path: str, object_name: str) -> None:
    try:
        config = resolve_storage_config()
        client = get_minio_client(config=config)
        ensure_bucket_exists(config.bucket, client=client, config=config)
        client.fput_object(
            bucket_name=config.bucket,
            object_name=object_name,
            file_path=local_path,
        )
    except StoragePreflightError:
        raise
    except Exception as exc:
        raise StoragePreflightError(_preflight_code(exc)) from None
