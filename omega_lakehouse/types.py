from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class ObjectStat:
    key: str
    uri: str
    size: int
    updated_at: datetime | None = None
    etag: str | None = None
    version: str | None = None
    checksum_sha256: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PutResult:
    key: str
    uri: str
    size: int
    checksum_sha256: str
    version: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PublishResult:
    staging_key: str
    final_key: str
    final_uri: str
    checksum_sha256: str | None
    version: str | None = None
    staging_deleted: bool = False
    staging_delete_failed: bool = False


@dataclass(frozen=True)
class ListPage:
    objects: tuple[ObjectStat, ...]
    next_cursor: str | None = None
    is_truncated: bool = False
    prefixes: tuple[str, ...] = ()
