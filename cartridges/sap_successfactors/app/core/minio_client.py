from __future__ import annotations

from minio import Minio
from minio.credentials import IamAwsProvider

from app.core.config import settings


def get_minio_client() -> Minio:
    endpoint = str(settings.minio_endpoint or "").strip()
    access_key = str(settings.minio_access_key or "").strip()
    secret_key = str(settings.minio_secret_key or "").strip()
    if "amazonaws.com" in endpoint.lower() and not (access_key or secret_key):
        return Minio(
            endpoint=endpoint,
            credentials=IamAwsProvider(),
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
