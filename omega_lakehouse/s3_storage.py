from __future__ import annotations

# fmt: off
import io
from collections.abc import Iterator, Mapping
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
from .s3_support import is_not_found, metadata_with_checksum, stat_from_head, stat_from_list_item
from .types import ListPage, ObjectStat, PublishResult, PutResult
class S3Storage:
    def __init__(self, config: LakehouseStorageConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client
    def uri_for(self, key: str) -> str:
        return f"s3://{self.config.bucket}/{validate_key(key)}"
    def _client_or_create(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            kwargs: dict[str, Any] = {
                "region_name": self.config.region or "us-east-1",
                "config": Config(
                    connect_timeout=2,
                    read_timeout=30,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            }
            if self.config.endpoint:
                kwargs["endpoint_url"] = self.config.endpoint
            if self.config.access_key and self.config.secret_key:
                kwargs["aws_access_key_id"] = self.config.access_key.reveal()
                kwargs["aws_secret_access_key"] = self.config.secret_key.reveal()
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _raise(self, exc: Exception, message: str, key: str | None = None) -> None:
        if is_not_found(exc):
            raise ObjectNotFound("object not found", provider=self.config.provider, bucket=self.config.bucket, key=key) from exc
        raise StorageError(message, provider=self.config.provider, bucket=self.config.bucket, key=key) from exc

    def _call(self, message: str, key: str | None, func, **kwargs):
        try: return func(**kwargs)
        except Exception as exc:
            self._raise(exc, message, key)

    def exists(self, key: str) -> bool:
        try:
            self.stat(key)
            return True
        except ObjectNotFound:
            return False

    def stat(self, key: str, *, expected_version: str | None = None) -> ObjectStat:
        key = validate_key(key)
        args = {"Bucket": self.config.bucket, "Key": key}
        if expected_version: args["VersionId"] = expected_version
        try:
            resp = self._client_or_create().head_object(**args)
        except Exception as exc:
            self._raise(exc, "object stat failed", key)
        return stat_from_head(self.uri_for, key, resp)

    def _check_write_allowed(self, key: str, overwrite: bool, expected_version: str | None) -> None:
        if expected_version:
            raise UnsupportedPrecondition(
                "expected_version is not guaranteed for S3/MinIO writes",
                provider=self.config.provider,
                bucket=self.config.bucket,
                key=key,
            )
        if not overwrite and self.exists(key):
            raise ObjectAlreadyExists("object already exists", provider=self.config.provider, bucket=self.config.bucket, key=key)

    def _put_object(self, key: str, body: Any, metadata: Mapping[str, str], *, overwrite: bool) -> dict[str, Any]:
        kwargs = {"Bucket": self.config.bucket, "Key": key, "Body": body,
                  "Metadata": metadata, "ContentType": "application/octet-stream"}
        if not overwrite:
            kwargs["IfNoneMatch"] = "*"
        try:
            return self._client_or_create().put_object(**kwargs)
        except Exception as exc:
            code = str((getattr(exc, "response", {}) or {}).get("Error", {}).get("Code", ""))
            if code in {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}:
                raise ObjectAlreadyExists("object already exists", provider=self.config.provider,
                                          bucket=self.config.bucket, key=key) from exc
            self._raise(exc, "object write failed", key)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        overwrite: bool = False,
        expected_version: str | None = None,
        metadata: Mapping[str, str] | None = None,
        checksum_sha256: str | None = None,
    ) -> PutResult:
        key = validate_key(key)
        checksum = checksum_sha256 or sha256_bytes(data)
        if checksum != sha256_bytes(data):
            raise ChecksumMismatch("provided checksum does not match payload", provider=self.config.provider, bucket=self.config.bucket, key=key)
        self._check_write_allowed(key, overwrite, expected_version)
        response = self._put_object(key, data, metadata_with_checksum(metadata, checksum), overwrite=overwrite)
        return self._put_result(key, len(data), checksum, metadata, response.get("VersionId"))

    def put_file(
        self,
        key: str,
        path: Path,
        *,
        overwrite: bool = False,
        expected_version: str | None = None,
        metadata: Mapping[str, str] | None = None,
        checksum_sha256: str | None = None,
    ) -> PutResult:
        key = validate_key(key)
        path = Path(path)
        checksum = checksum_sha256 or sha256_file(path)
        if checksum != sha256_file(path):
            raise ChecksumMismatch("provided checksum does not match file", provider=self.config.provider, bucket=self.config.bucket, key=key)
        self._check_write_allowed(key, overwrite, expected_version)
        with path.open("rb") as fh:
            response = self._put_object(key, fh, metadata_with_checksum(metadata, checksum), overwrite=overwrite)
        return self._put_result(key, path.stat().st_size, checksum, metadata, response.get("VersionId"))

    def _put_result(self, key: str, size: int, checksum: str, metadata: Mapping[str, str] | None, version: str | None = None) -> PutResult:
        if version is None:
            try: version = self.stat(key).version
            except StorageError: pass
        return PutResult(key=key, uri=self.uri_for(key), size=size, checksum_sha256=checksum, version=version, metadata=metadata or {})

    def get_bytes(self, key: str, *, expected_version: str | None = None) -> bytes:
        key = validate_key(key)
        args = {"Bucket": self.config.bucket, "Key": key}
        if expected_version: args["VersionId"] = expected_version
        try:
            body = self._client_or_create().get_object(**args)["Body"]
            try:
                return body.read()
            finally:
                close = getattr(body, "close", None)
                if close:
                    close()
        except Exception as exc:
            self._raise(exc, "object read failed", key)

    def open_reader(self, key: str, *, expected_version: str | None = None) -> BinaryIO:
        key = validate_key(key)
        args = {"Bucket": self.config.bucket, "Key": key}
        if expected_version: args["VersionId"] = expected_version
        try:
            return self._client_or_create().get_object(**args)["Body"]
        except Exception as exc:
            self._raise(exc, "object open failed", key)

    def iter_chunks(self, key: str, *, chunk_size: int = 1024 * 1024, expected_version: str | None = None) -> Iterator[bytes]:
        body = self.open_reader(key, expected_version=expected_version)
        try:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    def copy(
        self,
        source_key: str,
        target_key: str,
        *,
        overwrite: bool = False,
        expected_version: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> PutResult:
        source_key = validate_key(source_key)
        target_key = validate_key(target_key)
        self._check_write_allowed(target_key, overwrite, expected_version)
        source = self.stat(source_key)
        args: dict[str, Any] = {
            "Bucket": self.config.bucket,
            "Key": target_key,
            "CopySource": {"Bucket": self.config.bucket, "Key": source_key},
        }
        if metadata:
            args["Metadata"] = metadata_with_checksum(metadata, source.checksum_sha256 or "")
            args["MetadataDirective"] = "REPLACE"
        try:
            response = self._client_or_create().copy_object(**args)
        except Exception as exc:
            self._raise(exc, "object copy failed", target_key)
        return self._put_result(target_key, source.size, source.checksum_sha256 or "", metadata or source.metadata, response.get("VersionId"))

    def publish(self, staging_key: str, final_key: str, **kwargs) -> PublishResult:
        staging_key = validate_key(staging_key)
        final_key = validate_key(final_key)
        expected_sha = kwargs.get("expected_sha256")
        delete_staging = bool(kwargs.get("delete_staging", True))
        staging = self.stat(staging_key)
        staging_sha = staging.checksum_sha256
        if expected_sha and not staging_sha:
            staging_sha = sha256_bytes(self.get_bytes(staging_key))
        if expected_sha and staging_sha != expected_sha:
            raise ChecksumMismatch("staging checksum mismatch", provider=self.config.provider, bucket=self.config.bucket, key=staging_key)
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
            raise ChecksumMismatch("published checksum mismatch", provider=self.config.provider, bucket=self.config.bucket, key=final_key)
        deleted = failed = False
        if delete_staging:
            try:
                deleted = self.delete_object(staging_key)
            except Exception:
                failed = True
        return PublishResult(staging_key, final_key, result.uri, final_sha, final.version, deleted, failed)

    def delete_object(self, key: str, *, expected_version: str | None = None) -> bool:
        key = validate_key(key)
        if expected_version:
            raise UnsupportedPrecondition("expected_version is not guaranteed for S3/MinIO deletes", provider=self.config.provider, bucket=self.config.bucket, key=key)
        self._call("object delete failed", key, self._client_or_create().delete_object, Bucket=self.config.bucket, Key=key)
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
        kwargs: dict[str, Any] = {
            "Bucket": self.config.bucket,
            "Prefix": prefix,
            "MaxKeys": min(max(int(page_size or 1000), 1), 1000),
        }
        if cursor:
            kwargs["ContinuationToken"] = cursor
        if delimiter:
            kwargs["Delimiter"] = delimiter
        resp = self._call("object list failed", prefix, self._client_or_create().list_objects_v2, **kwargs)
        objects = tuple(stat_from_list_item(self.uri_for, item) for item in resp.get("Contents", []))
        prefixes = tuple(str(p.get("Prefix")) for p in resp.get("CommonPrefixes", []) if p.get("Prefix"))
        return ListPage(objects, resp.get("NextContinuationToken"), bool(resp.get("IsTruncated")), prefixes)

    def delete_prefix(
        self,
        prefix: str,
        *,
        require_trailing_slash: bool = True,
        max_objects: int | None = None,
    ) -> int:
        prefix = validate_prefix(prefix, for_delete=True, trailing_slash=require_trailing_slash)
        keys = [obj.key for obj in self.iter_list(prefix)]
        if max_objects is not None and len(keys) > max_objects:
            raise UnsafePrefixDelete("delete_prefix exceeded max_objects", provider=self.config.provider, bucket=self.config.bucket, key=prefix)
        for key in keys:
            self.delete_object(key)
        return len(keys)

    def presigned_get_url(self, key: str, *, expires_in: int = 300) -> str:
        key = validate_key(key)
        return self._client_or_create().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.config.bucket, "Key": key},
            ExpiresIn=expires_in,
        )
