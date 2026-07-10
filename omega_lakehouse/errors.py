from __future__ import annotations


def _safe(value: str | None) -> str:
    text = str(value or "")
    text = "".join(ch if ch >= " " and ch != "\x7f" else "?" for ch in text)
    if len(text) > 240:
        return text[:220] + "...[truncated]"
    return text


class StorageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        bucket: str | None = None,
        key: str | None = None,
    ) -> None:
        parts = [_safe(message)]
        details = []
        if provider:
            details.append(f"provider={_safe(provider)}")
        if bucket:
            details.append(f"bucket={_safe(bucket)}")
        if key:
            details.append(f"key={_safe(key)}")
        if details:
            parts.append("(" + ", ".join(details) + ")")
        super().__init__(" ".join(parts))
        self.provider = provider
        self.bucket = bucket
        self.key = key


class ObjectAlreadyExists(StorageError):
    pass


class ObjectNotFound(StorageError):
    pass


class InvalidObjectKey(StorageError):
    pass


class UnsafePrefixDelete(StorageError):
    pass


class ChecksumMismatch(StorageError):
    pass


class UnsupportedPrecondition(StorageError):
    pass
