from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from omega_lakehouse import (
    ChecksumMismatch,
    InvalidObjectKey,
    LakehouseStorageConfig,
    ObjectAlreadyExists,
    SecretValue,
    StorageError,
    UnsupportedPrecondition,
    UnsafePrefixDelete,
)
from omega_lakehouse.s3_storage import S3Storage


class NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.fail_copy = False

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        obj = self.objects[Key]
        return {
            "ContentLength": len(obj["body"]),
            "LastModified": obj["updated"],
            "ETag": '"etag"',
            "VersionId": obj.get("version"),
            "Metadata": obj.get("metadata", {}),
        }

    def put_object(self, Bucket, Key, Body, Metadata=None, ContentType=None):
        body = Body.read() if hasattr(Body, "read") else Body
        self.objects[Key] = {
            "body": body,
            "metadata": Metadata or {},
            "updated": datetime.now(timezone.utc),
            "version": f"v{len(self.objects) + 1}",
        }

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        return {"Body": io.BytesIO(self.objects[Key]["body"])}

    def copy_object(self, Bucket, Key, CopySource, **kwargs):
        if self.fail_copy:
            raise RuntimeError("copy interrupted")
        source_key = CopySource["Key"]
        if source_key not in self.objects:
            raise NotFound()
        source = self.objects[source_key]
        self.objects[Key] = {
            "body": source["body"],
            "metadata": kwargs.get("Metadata") or source.get("metadata", {}),
            "updated": datetime.now(timezone.utc),
            "version": f"v{len(self.objects) + 1}",
        }

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix") or ""
        max_keys = int(kwargs.get("MaxKeys") or 1000)
        start = int(kwargs.get("ContinuationToken") or 0)
        delimiter = kwargs.get("Delimiter")
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        contents = []
        prefixes = []
        seen = set()
        for key in keys:
            tail = key[len(prefix):]
            if delimiter and delimiter in tail:
                folder = prefix + tail.split(delimiter, 1)[0] + delimiter
                if folder not in seen:
                    seen.add(folder)
                    prefixes.append({"Prefix": folder})
                continue
            contents.append({"Key": key, "Size": len(self.objects[key]["body"]), "LastModified": self.objects[key]["updated"]})
        page = contents[start:start + max_keys]
        next_token = str(start + max_keys) if start + max_keys < len(contents) else None
        return {"Contents": page, "CommonPrefixes": prefixes, "NextContinuationToken": next_token, "IsTruncated": bool(next_token)}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        return f"https://signed.local/{Params['Bucket']}/{Params['Key']}?ttl={ExpiresIn}"


def _storage(fake: FakeS3 | None = None) -> S3Storage:
    config = LakehouseStorageConfig(provider="minio", bucket="lakehouse", endpoint="http://minio:9000", secure=False)
    return S3Storage(config, client=fake or FakeS3())


def test_read_methods_and_native_uri_roundtrip(tmp_path):
    storage = _storage()
    storage.put_bytes("raw/a/file.txt", b"hello")
    assert storage.uri_for("raw/a/file.txt") == "s3://lakehouse/raw/a/file.txt"
    assert storage.get_bytes("raw/a/file.txt") == b"hello"
    assert storage.open_reader("raw/a/file.txt").read() == b"hello"
    assert b"".join(storage.iter_chunks("raw/a/file.txt", chunk_size=2)) == b"hello"


def test_list_page_and_iter_list_are_paged():
    fake = FakeS3()
    storage = _storage(fake)
    for idx in range(3):
        storage.put_bytes(f"raw/a/{idx}.txt", str(idx).encode())
    page = storage.list_page("raw/a/", page_size=2)
    assert [obj.key for obj in page.objects] == ["raw/a/0.txt", "raw/a/1.txt"]
    assert page.next_cursor == "2"
    assert [obj.key for obj in storage.iter_list("raw/a/", page_size=2)] == [
        "raw/a/0.txt",
        "raw/a/1.txt",
        "raw/a/2.txt",
    ]


def test_publish_copies_validates_then_deletes_staging():
    fake = FakeS3()
    storage = _storage(fake)
    staging = storage.put_bytes("tmp/batch.parquet", b"data")
    result = storage.publish("tmp/batch.parquet", "raw/final.parquet", expected_sha256=staging.checksum_sha256)
    assert result.staging_deleted is True
    assert fake.deleted == ["tmp/batch.parquet"]
    assert storage.get_bytes("raw/final.parquet") == b"data"


def test_interrupted_publish_keeps_staging():
    fake = FakeS3()
    storage = _storage(fake)
    storage.put_bytes("tmp/batch.parquet", b"data")
    fake.fail_copy = True
    with pytest.raises(StorageError):
        storage.publish("tmp/batch.parquet", "raw/final.parquet")
    assert "tmp/batch.parquet" in fake.objects
    assert fake.deleted == []


def test_checksum_overwrite_and_precondition_contracts():
    storage = _storage()
    storage.put_bytes("raw/a.txt", b"one")
    with pytest.raises(ObjectAlreadyExists):
        storage.put_bytes("raw/a.txt", b"one")
    with pytest.raises(UnsupportedPrecondition):
        storage.put_bytes("raw/a.txt", b"two", overwrite=True, expected_version="v1")
    with pytest.raises(ChecksumMismatch):
        storage.put_bytes("raw/b.txt", b"bad", checksum_sha256="0" * 64)


def test_key_delete_limits_and_secret_redaction(monkeypatch):
    storage = _storage()
    for bad in ("s3://bucket/key", "/abs/key", "a//b", "a/../b", "a/\x00/b"):
        with pytest.raises(InvalidObjectKey):
            storage.put_bytes(bad, b"x")
    with pytest.raises(UnsafePrefixDelete):
        storage.delete_prefix("")
    storage.put_bytes("raw/a/1.txt", b"1")
    storage.put_bytes("raw/a/2.txt", b"2")
    with pytest.raises(UnsafePrefixDelete):
        storage.delete_prefix("raw/a/", max_objects=1)

    secret = SecretValue("super-secret")
    config = LakehouseStorageConfig(provider="s3", bucket="b", access_key=secret, secret_key=secret)
    err = StorageError("failed", provider="s3", bucket="b", key="raw/a")
    assert "super-secret" not in repr(config)
    assert "super-secret" not in str(secret)
    assert "super-secret" not in str(err)


def test_gcs_uri_is_native_without_importing_sdk():
    from omega_lakehouse.gcs_storage import GCSStorage

    config = LakehouseStorageConfig(provider="gcs", bucket="lakehouse")
    assert GCSStorage(config, client=object()).uri_for("raw/a.txt") == "gs://lakehouse/raw/a.txt"
