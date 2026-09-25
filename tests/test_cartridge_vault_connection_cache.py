from __future__ import annotations

import ast
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
