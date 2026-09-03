from __future__ import annotations

from minio import Minio
from minio.credentials.providers import IamAwsProvider

from app.core.config import settings


def get_minio_client() -> Minio:
    m = settings.resolved_minio
    common = {
        "endpoint": m["endpoint"],
        "secure": bool(m.get("secure", False)),
        "region": m.get("region"),
    }
    if m["provider"] == "s3" and not m["access_key"]:
        return Minio(credentials=IamAwsProvider(), **common)
    if m.get("session_token"):
        common["session_token"] = m["session_token"]
    return Minio(
        access_key=m["access_key"],
        secret_key=m["secret_key"],
        **common,
    )


def ensure_bucket_exists(bucket_name: str) -> None:
    if settings.resolved_minio.get("provider") != "minio":
        return
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
