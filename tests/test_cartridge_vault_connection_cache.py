from __future__ import annotations

import ast
import threading
import types
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = ("hubspot", "replicon", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors")


def _class_source(cartridge: str) -> tuple[ast.ClassDef, str]:
    path = REPO / "cartridges" / cartridge / "app" / "core" / "vault_client.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ConnectionCache")
    assert "_CONNECTION_CACHE = _ConnectionCache()" in source
    return node, ast.get_source_segment(source, node) or ""


def _load(cartridge: str, clock: list[float]):
    node, _ = _class_source(cartridge)
    namespace: dict[str, Any] = {
        "Any": Any,
        "OrderedDict": OrderedDict,
        "time": types.SimpleNamespace(monotonic=lambda: clock[0]),
        "threading": threading,
        "_MISSING": object(),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), cartridge, "exec"), namespace)
    return namespace["_ConnectionCache"]


def test_every_cartridge_ships_the_same_bounded_cache():
    sources = {_class_source(cartridge)[1] for cartridge in CARTRIDGES}
    assert len(sources) == 1


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_cached_credentials_expire_and_the_oldest_are_evicted(cartridge):
    clock = [1000.0]
    cache = _load(cartridge, clock)(ttl_seconds=300, max_entries=2)
    cache["a"] = {"token": "1"}
    cache["b"] = {"token": "2"}
    assert cache.get("a") == {"token": "1"}
    cache["c"] = {"token": "3"}
    assert cache.get("b") is None and len(cache) == 2
    clock[0] += 301
    assert cache.get("a") is None and cache.get("c") is None
    cache["d"] = {"token": "4"}
    cache.clear()
    assert len(cache) == 0


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_expired_credentials_stay_available_as_stale_for_the_same_context(cartridge):
    clock = [1000.0]
    cache = _load(cartridge, clock)(ttl_seconds=300, max_entries=4, max_stale_seconds=3600)
    cache["job-context"] = {"password": "pw"}
    clock[0] += 400
    assert cache.get("job-context") is None
    assert cache.stale("job-context") == {"password": "pw"}
    clock[0] += 3300
    assert cache.stale("job-context") is None
    assert len(cache) == 0


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_a_failed_refetch_falls_back_to_the_stale_entry_of_that_context(cartridge):
    source = (REPO / "cartridges" / cartridge / "app" / "core" / "vault_client.py").read_text(encoding="utf-8")
    body = source[source.index("def _fetch_connection("):]
    body = body[: body.index("\ndef ", 1)]
    assert body.rstrip().endswith("return _CONNECTION_CACHE.stale(cache_key, {})")


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_concurrent_lookups_never_trip_over_pruning(cartridge):
    import sys
    import time as real_time

    node, _ = _class_source(cartridge)
    namespace: dict[str, Any] = {
        "Any": Any, "OrderedDict": OrderedDict, "time": real_time, "threading": threading, "_MISSING": object()
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), cartridge, "exec"), namespace)
    cache = namespace["_ConnectionCache"](ttl_seconds=300, max_entries=64)
    for index in range(8):
        cache[f"ctx-{index}"] = {"password": str(index)}
    errors: list[BaseException] = []
    stop = real_time.monotonic() + 0.4

    def hammer(offset: int) -> None:
        try:
            while real_time.monotonic() < stop:
                for index in range(8):
                    key = f"ctx-{(index + offset) % 8}"
                    cache.get(key)
                    cache.stale(key)
                    cache[key] = {"password": key}
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=hammer, args=(offset,)) for offset in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(previous)
    assert errors == []
