from __future__ import annotations

from minio import Minio
from app.core.config import settings


def get_minio_client() -> Minio:
    m = settings.resolved_minio
    return Minio(
        endpoint=m["endpoint"],
        access_key=m["access_key"],
        secret_key=m["secret_key"],
        secure=bool(m.get("secure", False)),
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
