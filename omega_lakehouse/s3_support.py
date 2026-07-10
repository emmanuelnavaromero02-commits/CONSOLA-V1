from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from .types import ObjectStat

HASH_METADATA = "omega-sha256"


def metadata_with_checksum(metadata: Mapping[str, str] | None, checksum: str) -> dict[str, str]:
    clean = {
        str(k).lower(): str(v)
        for k, v in (metadata or {}).items()
        if k and not str(k).lower().startswith(("aws-", "x-amz-"))
    }
    clean[HASH_METADATA] = checksum
    return clean


def is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"404", "NoSuchKey", "NotFound"}


def stat_from_head(uri_for: Callable[[str], str], key: str, resp: Mapping[str, Any]) -> ObjectStat:
    metadata = {str(k).lower(): str(v) for k, v in (resp.get("Metadata") or {}).items()}
    return ObjectStat(
        key=key,
        uri=uri_for(key),
        size=int(resp.get("ContentLength") or 0),
        updated_at=resp.get("LastModified"),
        etag=str(resp.get("ETag") or "").strip('"') or None,
        version=resp.get("VersionId"),
        checksum_sha256=metadata.get(HASH_METADATA),
        metadata=metadata,
    )


def stat_from_list_item(uri_for: Callable[[str], str], item: Mapping[str, Any]) -> ObjectStat:
    key = str(item.get("Key") or "")
    return ObjectStat(
        key=key,
        uri=uri_for(key),
        size=int(item.get("Size") or 0),
        updated_at=item.get("LastModified"),
        etag=str(item.get("ETag") or "").strip('"') or None,
    )
