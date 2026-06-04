from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_v1_router_paths_are_covered_by_runtime_app_without_duplicates():
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("INTERNAL_API_KEY", "v1routercoverageinternal1234567890abcdef")
    if str(REPO / "console") not in sys.path:
        sys.path.insert(0, str(REPO / "console"))

    import app.main as main
    from app.routers import v1

    expected_pairs: set[tuple[str, str]] = set()
    for router in v1.ROUTERS:
        for route in router.routes:
            for method in getattr(route, "methods", set()) or set():
                expected_pairs.add((method, route.path))

    runtime_pairs = [
        (method, route.path)
        for route in main.app.routes
        for method in (getattr(route, "methods", set()) or set())
    ]
    runtime_counter = Counter(runtime_pairs)
    missing = sorted(expected_pairs - set(runtime_pairs))
    duplicate_v1_pairs = {
        pair: count
        for pair, count in runtime_counter.items()
        if pair in expected_pairs and count > 1
    }

    assert not missing
    assert not duplicate_v1_pairs


def test_v1_router_aggregate_lists_runtime_router_modules():
    src = (REPO / "console/app/routers/v1/__init__.py").read_text(encoding="utf-8")
    for module in (
        "auth",
        "system",
        "jobs",
        "data",
        "pipeline_studio",
        "marketplace_apps",
        "misc",
        "rag",
        "agents",
        "vault",
        "monitoring",
        "admin_decisions",
    ):
        assert f"{module}.router" in src
