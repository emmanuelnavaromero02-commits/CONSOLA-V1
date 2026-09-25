from __future__ import annotations

import pytest

from omega_lakehouse.types import ObjectStat
from refinement.app import publication_inputs
from refinement.app.publication_contract import PublicationScope
from refinement.app.publication_snapshot import (
    PublicationSnapshot,
    PublicationSnapshotResolver,
)


DIGEST = "a" * 64
KEY = "gold/sap_successfactors/employee_360/data.parquet"
URI = f"s3://bucket/{KEY}"
VERSION = "3HL4kqtJlcpXroDTDmJ+rmSpXd3dIbrHY+MTRCxf3vjVBH40Nrjfkd"


class _Storage:

    def __init__(self, stat: ObjectStat | None = None, error: Exception | None = None):
        self._stat = stat
        self._error = error
        self.stat_calls: list[tuple[str, str | None]] = []
        self.byte_calls: list[str] = []

    def uri_for(self, key: str) -> str:
        return f"s3://bucket/{key}"

    def stat(self, key: str, *, expected_version: str | None = None) -> ObjectStat:
        self.stat_calls.append((key, expected_version))
        if self._error is not None:
            raise self._error
        assert self._stat is not None
        return self._stat

    def iter_chunks(self, key: str, **_: object):
        self.byte_calls.append(key)
        raise AssertionError("iter_chunks must not be used to verify a published object")

    def get_bytes(self, key: str, **_: object) -> bytes:
        self.byte_calls.append(key)
        raise AssertionError("get_bytes must not be used to verify a published object")


def _stat(*, checksum: str | None = DIGEST, version: str | None = VERSION) -> ObjectStat:
    return ObjectStat(
        key=KEY, uri=URI, size=1024, etag="etag-1",
        version=version, checksum_sha256=checksum,
    )


def _snapshot(**overrides: object) -> PublicationSnapshot:
    head = {
        "materialization_run_id": "run-1",
        "generation": 1,
        "status": "published",
        "object_uri": URI,
        "object_version": VERSION,
        "object_checksum": DIGEST,
    }
    head.update(overrides)
    return PublicationSnapshot(
        PublicationScope("t", "w", "employee_360", "gold"), head, None
    )


def _resolver(storage: _Storage) -> PublicationSnapshotResolver:
    resolver = PublicationSnapshotResolver.__new__(PublicationSnapshotResolver)
    resolver.storage = storage
    resolver.database_url = "postgresql://unused"
    return resolver


def test_valid_object_is_verified_without_transferring_bytes() -> None:
    storage = _Storage(_stat())

    _resolver(storage)._validate_object(_snapshot())

    assert storage.stat_calls == [(KEY, VERSION)], "expected exactly one pinned HEAD"
    assert storage.byte_calls == []


@pytest.mark.parametrize("bad", [None, "", "null"])
def test_unpinnable_version_is_refused(bad: str | None) -> None:
    storage = _Storage(_stat())

    with pytest.raises(RuntimeError, match="no pinned version"):
        _resolver(storage)._validate_object(_snapshot(object_version=bad))

    assert storage.stat_calls == [], "must fail before touching storage"


def test_missing_recorded_checksum_is_refused() -> None:
    storage = _Storage(_stat(checksum=None))

    with pytest.raises(RuntimeError, match="no recorded checksum"):
        _resolver(storage)._validate_object(_snapshot())


def test_checksum_mismatch_still_fails() -> None:
    storage = _Storage(_stat(checksum="b" * 64))

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _resolver(storage)._validate_object(_snapshot())


def test_unknown_version_fails_as_unavailable() -> None:
    storage = _Storage(error=RuntimeError("NoSuchVersion"))

    with pytest.raises(RuntimeError, match="unavailable"):
        _resolver(storage)._validate_object(_snapshot())


def test_legacy_unverified_still_short_circuits() -> None:
    storage = _Storage(_stat())

    _resolver(storage)._validate_object(_snapshot(status="legacy_unverified"))

    assert storage.stat_calls == []


class _Engine:
    def __init__(self, storage: _Storage):
        self.storage = storage

    @staticmethod
    def _s3_object_key(uri: str) -> str:
        return uri.split("s3://bucket/", 1)[-1] if uri else ""

    @staticmethod
    def _object_checksum(*_: object, **__: object) -> str:
        raise AssertionError("a published dependency must not be downloaded")


def _published_state(monkeypatch: pytest.MonkeyPatch, head: dict | None, storage: _Storage):
    class _Resolver:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def published_snapshot(self, *_: object, **__: object):
            return None if head is None else type("S", (), {"head": head})()

    monkeypatch.setattr(publication_inputs, "PublicationSnapshotResolver", _Resolver)
    return publication_inputs._published_state(
        _Engine(storage), "gold/sap_successfactors/employee_360", {}
    )


def _head(**overrides: object) -> dict:
    head = {
        "materialization_run_id": "run-1",
        "generation": 1,
        "object_uri": URI,
        "object_version": VERSION,
        "object_checksum": DIGEST,
        "status": "published",
        "gold_table": None,
    }
    head.update(overrides)
    return head


def test_dependency_is_fingerprinted_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_stat())

    state = _published_state(monkeypatch, _head(), storage)

    assert storage.stat_calls == [(KEY, VERSION)]
    assert storage.byte_calls == []
    assert state["published"]["checksum"] == DIGEST
    assert state["published"]["object_version"] == VERSION


def test_dependency_without_pinned_version_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_stat())

    with pytest.raises(RuntimeError, match="no pinned version"):
        _published_state(monkeypatch, _head(object_version=""), storage)

    assert storage.stat_calls == []


@pytest.mark.parametrize("bad", [None, "", "null"])
def test_dependency_with_unpinnable_version_is_refused(
    monkeypatch: pytest.MonkeyPatch, bad: str | None
) -> None:
    storage = _Storage(_stat())

    with pytest.raises(RuntimeError, match="no pinned version"):
        _published_state(monkeypatch, _head(object_version=bad), storage)

    assert storage.stat_calls == []


def test_dependency_without_recorded_checksum_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_stat(checksum=""))

    with pytest.raises(RuntimeError, match="no recorded checksum"):
        _published_state(monkeypatch, _head(), storage)


def test_dependency_checksum_mismatch_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_stat(checksum="c" * 64))

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _published_state(monkeypatch, _head(), storage)


def test_absent_head_is_still_reported_as_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_stat())

    state = _published_state(monkeypatch, None, storage)

    assert state == {
        "source": "gold/sap_successfactors/employee_360",
        "published": None,
    }
    assert storage.stat_calls == []
