from __future__ import annotations

from .config import LakehouseStorageConfig, config_from_env
from .interface import LakehouseStorage


def storage_from_env(*, bucket: str | None = None) -> LakehouseStorage:
    return storage_from_config(config_from_env(bucket=bucket))


def storage_from_config(config: LakehouseStorageConfig) -> LakehouseStorage:
    if config.provider == "gcs":
        from .gcs_storage import GCSStorage

        return GCSStorage(config)
    from .s3_storage import S3Storage

    return S3Storage(config)
