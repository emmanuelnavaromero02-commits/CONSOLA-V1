from __future__ import annotations

import pytest

from omega_lakehouse.types import ObjectStat
from refinement.app.publication_inputs import _raw_prefix, _raw_state


PREFIX = "raw/sap_successfactors/EmployeeTime/tenant_id=T/workspace_id=W/"
WILDCARD = f"s3://bucket/{PREFIX}**/*.parquet"


def _key(load_date: str, batch: str = "b1") -> str:
    return f"{PREFIX}load_date={load_date}/batch_id={batch}/EmployeeTime.parquet"


class _Storage:

    def __init__(self, objects: list[ObjectStat]):
        self._objects = objects
        self.listed_prefixes: list[str] = []
        self.stat_calls: list[str] = []
        self.byte_calls: list[str] = []

    def iter_list(self, prefix: str, **_: object):
        self.listed_prefixes.append(prefix)
        for obj in self._objects:
            if obj.key.startswith(prefix):
                yield obj

    def stat(self, key: str, **_: object) -> ObjectStat:
        self.stat_calls.append(key)
        raise AssertionError("the raw fingerprint must not HEAD each object")

    def iter_chunks(self, key: str, **_: object):
        self.byte_calls.append(key)
        raise AssertionError("the raw fingerprint must not download objects")

    def get_bytes(self, key: str, **_: object) -> bytes:
        self.byte_calls.append(key)
        raise AssertionError("the raw fingerprint must not download objects")


class _Engine:
    def __init__(self, storage: _Storage):
        self.storage = storage

    @staticmethod
    def _bronze_path(*_: object, **__: object) -> str:
        return WILDCARD

    @staticmethod
    def _s3_object_key(uri: str) -> str:
        return uri.split("s3://bucket/", 1)[-1] if uri else ""

    @staticmethod
    def _object_checksum(*_: object, **__: object) -> str:
        raise AssertionError("the raw fingerprint must not hash object bytes")


def _stat(key: str, *, size: int = 100, etag: str = "e") -> ObjectStat:
    return ObjectStat(key=key, uri=f"s3://bucket/{key}", size=size, etag=etag)


def _history() -> list[ObjectStat]:
    return [
        _stat(_key("2026-06-07"), size=10, etag="e1"),
        _stat(_key("2026-06-08"), size=20, etag="e2"),
        _stat(_key("2026-07-15"), size=30, etag="e3"),
    ]


def _state(objects: list[ObjectStat]) -> tuple[dict, _Storage]:
    storage = _Storage(objects)
    return _raw_state(_Engine(storage), "raw/sap_successfactors/EmployeeTime", {}), storage


def test_prefix_sits_above_the_load_date_partition() -> None:
    prefix = _raw_prefix(_Engine(_Storage([])), "raw/sap_successfactors/EmployeeTime", {})

    assert prefix == PREFIX
    assert "load_date" not in prefix, (
        "the prefix must not descend into a load_date partition -- the SQL reads "
        "the full history and picks the latest row per key"
    )


def test_every_partition_is_fingerprinted_not_just_the_newest() -> None:
    state, storage = _state(_history())

    assert [obj["key"] for obj in state["objects"]] == [
        _key("2026-06-07"), _key("2026-06-08"), _key("2026-07-15"),
    ]
    assert storage.listed_prefixes == [PREFIX]


def test_dropping_an_old_partition_changes_the_fingerprint() -> None:
    full, _ = _state(_history())
    narrowed, _ = _state(_history()[-1:])

    assert full != narrowed


def test_non_parquet_objects_are_still_excluded() -> None:
    objects = _history() + [_stat(f"{PREFIX}load_date=2026-07-15/_SUCCESS")]

    state, _ = _state(objects)

    assert all(obj["key"].endswith(".parquet") for obj in state["objects"])
    assert len(state["objects"]) == 3


def test_objects_are_sorted_so_listing_order_cannot_move_the_digest() -> None:
    forward, _ = _state(_history())
    reversed_order, _ = _state(list(reversed(_history())))

    assert forward == reversed_order


def test_fingerprint_costs_one_listing_and_nothing_else() -> None:
    state, storage = _state(_history())

    assert storage.listed_prefixes == [PREFIX]
    assert storage.stat_calls == [], "a HEAD per object is 2,321 round trips"
    assert storage.byte_calls == [], "a GET per object is 14.87 GB, twice"
    assert all(set(obj) == {"key", "size", "etag"} for obj in state["objects"])


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_object_without_an_etag_is_refused(bad: str | None) -> None:
    objects = _history() + [_stat(_key("2026-07-16"), etag=bad)]

    with pytest.raises(RuntimeError, match="no identity"):
        _state(objects)


def test_rewritten_object_changes_the_fingerprint() -> None:
    before, _ = _state(_history())
    rewritten = _history()
    rewritten[1] = _stat(_key("2026-06-08"), size=20, etag="e2-rewritten")

    after, _ = _state(rewritten)

    assert before != after


def test_resized_object_changes_the_fingerprint() -> None:
    before, _ = _state(_history())
    resized = _history()
    resized[0] = _stat(_key("2026-06-07"), size=999, etag="e1")

    after, _ = _state(resized)

    assert before != after


def test_added_and_removed_objects_change_the_fingerprint() -> None:
    before, _ = _state(_history())
    added, _ = _state(_history() + [_stat(_key("2026-07-16"), etag="e4")])
    removed, _ = _state(_history()[:-1])

    assert before != added
    assert before != removed
    assert added != removed
