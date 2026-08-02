from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Iterator, Mapping, Protocol

from .types import ListPage, ObjectStat, PublishResult, PutResult


class LakehouseStorage(Protocol):
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        overwrite: bool = False,
        expected_version: str | None = None,
        metadata: Mapping[str, str] | None = None,
        checksum_sha256: str | None = None,
    ) -> PutResult: ...

    def put_file(
        self,
        key: str,
        path: Path,
        *,
        overwrite: bool = False,
        expected_version: str | None = None,
        metadata: Mapping[str, str] | None = None,
        checksum_sha256: str | None = None,
    ) -> PutResult: ...

    def get_bytes(self, key: str, *, expected_version: str | None = None) -> bytes: ...
    def open_reader(
        self, key: str, *, expected_version: str | None = None
    ) -> BinaryIO: ...
    def iter_chunks(
        self,
        key: str,
        *,
        chunk_size: int = 1024 * 1024,
        expected_version: str | None = None,
    ) -> Iterator[bytes]: ...
    def stat(self, key: str, *, expected_version: str | None = None) -> ObjectStat: ...
    def exists(self, key: str) -> bool: ...
    def copy(self, source_key: str, target_key: str, **kwargs) -> PutResult: ...
    def publish(self, staging_key: str, final_key: str, **kwargs) -> PublishResult: ...
    def delete_object(
        self, key: str, *, expected_version: str | None = None
    ) -> bool: ...
    def delete_prefix(self, prefix: str, **kwargs) -> int: ...
    def iter_list(
        self, prefix: str, *, page_size: int = 1000
    ) -> Iterator[ObjectStat]: ...
    def list_page(
        self, prefix: str, *, page_size: int = 1000, cursor: str | None = None
    ) -> ListPage: ...
    def uri_for(self, key: str) -> str: ...
