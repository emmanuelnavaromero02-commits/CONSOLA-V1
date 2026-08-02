from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Any

from .checksums import sha256_bytes, sha256_file
from .config import LakehouseStorageConfig
from .errors import (
    ChecksumMismatch,
    ObjectAlreadyExists,
    ObjectNotFound,
    StorageError,
    UnsupportedPrecondition,
    UnsafePrefixDelete,
)
from .keys import validate_key, validate_prefix
from .types import ListPage, ObjectStat, PublishResult, PutResult

_HASH_METADATA = "omega-sha256"


class GCSStorage:
    def __init__(
        self, config: LakehouseStorageConfig, *, client: Any | None = None
    ) -> None:
        self.config = config
        self._client = client

    def _client_or_create(self):
        if self._client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise StorageError(
                    "google-cloud-storage is required for GCS provider", provider="gcs"
                ) from exc
            self._client = storage.Client()
        return self._client

    def _bucket(self):
        return self._client_or_create().bucket(self.config.bucket)

    def _blob(self, key: str, expected_version: str | None = None):
        generation = int(expected_version) if expected_version else None
        return self._bucket().blob(validate_key(key), generation=generation)

    def uri_for(self, key: str) -> str:
        return f"gs://{self.config.bucket}/{validate_key(key)}"

    def _metadata(
        self, metadata: Mapping[str, str] | None, checksum: str
    ) -> dict[str, str]:
        clean = {str(k).lower(): str(v) for k, v in (metadata or {}).items() if k}
        clean[_HASH_METADATA] = checksum
        return clean

    def _not_found(self, exc: Exception) -> bool:
        return exc.__class__.__name__ == "NotFound"

    def _generation(
        self, expected_version: str | None, *, overwrite: bool
    ) -> int | None:
        if expected_version:
            try:
                return int(expected_version)
            except ValueError as exc:
                raise UnsupportedPrecondition(
                    "GCS expected_version must be a numeric generation", provider="gcs"
                ) from exc
        return None if overwrite else 0

    def stat(self, key: str, *, expected_version: str | None = None) -> ObjectStat:
        key = validate_key(key)
        blob = self._blob(key, expected_version)
        try:
            blob.reload()
        except Exception as exc:
            if self._not_found(exc):
                raise ObjectNotFound(
                    "object not found",
                    provider="gcs",
                    bucket=self.config.bucket,
                    key=key,
                ) from exc
            raise StorageError(
                "object stat failed", provider="gcs", bucket=self.config.bucket, key=key
            ) from exc
        metadata = {str(k).lower(): str(v) for k, v in (blob.metadata or {}).items()}
        return ObjectStat(
            key=key,
            uri=self.uri_for(key),
            size=int(blob.size or 0),
            updated_at=blob.updated,
            etag=blob.etag,
            version=str(blob.generation) if blob.generation is not None else None,
            checksum_sha256=metadata.get(_HASH_METADATA),
            metadata=metadata,
        )

    def exists(self, key: str) -> bool:
        return bool(self._blob(key).exists())

    def put_bytes(self, key: str, data: bytes, **kwargs) -> PutResult:
        key = validate_key(key)
        checksum = kwargs.get("checksum_sha256") or sha256_bytes(data)
        if checksum != sha256_bytes(data):
            raise ChecksumMismatch(
                "provided checksum does not match payload",
                provider="gcs",
                bucket=self.config.bucket,
                key=key,
            )
        overwrite = bool(kwargs.get("overwrite", False))
        generation = self._generation(
            kwargs.get("expected_version"), overwrite=overwrite
        )
        if not overwrite and self.exists(key):
            raise ObjectAlreadyExists(
                "object already exists",
                provider="gcs",
                bucket=self.config.bucket,
                key=key,
            )
        blob = self._blob(key)
        blob.metadata = self._metadata(kwargs.get("metadata"), checksum)
        blob.upload_from_string(
            data,
            content_type="application/octet-stream",
            if_generation_match=generation,
        )
        return self._result(
            key, len(data), checksum, kwargs.get("metadata"), str(blob.generation)
        )

    def put_file(self, key: str, path: Path, **kwargs) -> PutResult:
        path = Path(path)
        return self.put_bytes(
            key,
            path.read_bytes(),
            **{
                **kwargs,
                "checksum_sha256": kwargs.get("checksum_sha256") or sha256_file(path),
            },
        )

    def _result(
        self,
        key: str,
        size: int,
        checksum: str,
        metadata: Mapping[str, str] | None,
        version: str | None = None,
    ) -> PutResult:
        if version is None:
            try:
                version = self.stat(key).version
            except StorageError:
                pass
        return PutResult(
            key, self.uri_for(key), size, checksum, version, metadata or {}
        )

    def get_bytes(self, key: str, *, expected_version: str | None = None) -> bytes:
        key = validate_key(key)
        try:
            return self._blob(key, expected_version).download_as_bytes()
        except Exception as exc:
            if self._not_found(exc):
                raise ObjectNotFound(
                    "object not found",
                    provider="gcs",
                    bucket=self.config.bucket,
                    key=key,
                ) from exc
            raise StorageError(
                "object read failed", provider="gcs", bucket=self.config.bucket, key=key
            ) from exc

    def open_reader(self, key: str, *, expected_version: str | None = None) -> BinaryIO:
        return self._blob(key, expected_version).open("rb")

    def iter_chunks(
        self,
        key: str,
        *,
        chunk_size: int = 1024 * 1024,
        expected_version: str | None = None,
    ) -> Iterator[bytes]:
        with self.open_reader(key, expected_version=expected_version) as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def copy(self, source_key: str, target_key: str, **kwargs) -> PutResult:
        source_key = validate_key(source_key)
        target_key = validate_key(target_key)
        overwrite = bool(kwargs.get("overwrite", False))
        generation = self._generation(
            kwargs.get("expected_version"), overwrite=overwrite
        )
        if not overwrite and self.exists(target_key):
            raise ObjectAlreadyExists(
                "object already exists",
                provider="gcs",
                bucket=self.config.bucket,
                key=target_key,
            )
        source = self.stat(source_key)
        copied = self._bucket().copy_blob(
            self._blob(source_key),
            self._bucket(),
            target_key,
            if_generation_match=generation,
        )
        return self._result(
            target_key,
            source.size,
            source.checksum_sha256 or "",
            kwargs.get("metadata") or source.metadata,
            str(copied.generation),
        )

    def publish(self, staging_key: str, final_key: str, **kwargs) -> PublishResult:
        staging = self.stat(staging_key)
        expected_sha = kwargs.get("expected_sha256")
        staging_sha = staging.checksum_sha256
        if expected_sha and not staging_sha:
            staging_sha = sha256_bytes(self.get_bytes(staging_key))
        if expected_sha and staging_sha != expected_sha:
            raise ChecksumMismatch(
                "staging checksum mismatch",
                provider="gcs",
                bucket=self.config.bucket,
                key=staging_key,
            )
        result = self.copy(
            staging_key,
            final_key,
            overwrite=bool(kwargs.get("overwrite", False)),
            expected_version=kwargs.get("expected_version"),
        )
        final = self.stat(final_key)
        final_sha = final.checksum_sha256
        if expected_sha and not final_sha:
            final_sha = sha256_bytes(self.get_bytes(final_key))
        if expected_sha and final_sha != expected_sha:
            raise ChecksumMismatch(
                "published checksum mismatch",
                provider="gcs",
                bucket=self.config.bucket,
                key=final_key,
            )
        deleted = failed = False
        if bool(kwargs.get("delete_staging", True)):
            try:
                deleted = self.delete_object(staging_key)
            except Exception:
                failed = True
        return PublishResult(
            staging_key,
            final_key,
            result.uri,
            final_sha,
            final.version,
            deleted,
            failed,
        )

    def delete_object(self, key: str, *, expected_version: str | None = None) -> bool:
        generation = int(expected_version) if expected_version else None
        self._blob(key).delete(if_generation_match=generation)
        return True

    def iter_list(self, prefix: str, *, page_size: int = 1000) -> Iterator[ObjectStat]:
        cursor = None
        while True:
            page = self.list_page(prefix, page_size=page_size, cursor=cursor)
            yield from page.objects
            if not page.next_cursor:
                break
            cursor = page.next_cursor

    def list_page(
        self,
        prefix: str,
        *,
        page_size: int = 1000,
        cursor: str | None = None,
        delimiter: str | None = None,
    ) -> ListPage:
        prefix = validate_prefix(prefix)
        iterator = self._client_or_create().list_blobs(
            self.config.bucket,
            prefix=prefix,
            max_results=min(max(int(page_size or 1000), 1), 1000),
            page_token=cursor,
            delimiter=delimiter,
        )
        page = next(iterator.pages, None)
        if page is None:
            return ListPage(())
        objects = tuple(self._stat_from_blob(blob) for blob in page)
        prefixes = tuple(str(p) for p in getattr(page, "prefixes", ()) or ())
        return ListPage(
            objects, iterator.next_page_token, bool(iterator.next_page_token), prefixes
        )

    def _stat_from_blob(self, blob) -> ObjectStat:
        key = str(blob.name)
        metadata = {str(k).lower(): str(v) for k, v in (blob.metadata or {}).items()}
        return ObjectStat(
            key,
            self.uri_for(key),
            int(blob.size or 0),
            blob.updated,
            blob.etag,
            str(blob.generation),
            metadata.get(_HASH_METADATA),
            metadata,
        )

    def delete_prefix(
        self,
        prefix: str,
        *,
        require_trailing_slash: bool = True,
        max_objects: int | None = None,
    ) -> int:
        prefix = validate_prefix(
            prefix, for_delete=True, trailing_slash=require_trailing_slash
        )
        keys = [obj.key for obj in self.iter_list(prefix)]
        if max_objects is not None and len(keys) > max_objects:
            raise UnsafePrefixDelete(
                "delete_prefix exceeded max_objects",
                provider="gcs",
                bucket=self.config.bucket,
                key=prefix,
            )
        for key in keys:
            self.delete_object(key)
        return len(keys)

    def presigned_get_url(self, key: str, *, expires_in: int = 300) -> str:
        return self._blob(key).generate_signed_url(
            expiration=timedelta(seconds=expires_in), method="GET"
        )
