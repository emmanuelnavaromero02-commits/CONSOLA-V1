from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

V1_ROUTE_INVENTORY = {
    "auth": "legacy_mirror",
    "system": "legacy_mirror",
    "jobs": "legacy_mirror",
    "data": "legacy_mirror",
    "pipeline_studio": "legacy_mirror",
    "marketplace_apps": "legacy_mirror",
    "misc": "legacy_mirror",
    "rag": "legacy_mirror",
    "agents": "legacy_mirror",
    "vault": "legacy_mirror",
    "monitoring": "legacy_mirror",
    "admin_decisions": "legacy_mirror",
}


def test_v1_router_paths_are_covered_by_runtime_app_without_duplicates():
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault(
        "INTERNAL_API_KEY", "v1routercoverageinternal1234567890abcdef"
    )
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
    for module in V1_ROUTE_INVENTORY:
        assert f"{module}.router" in src


def test_v1_route_inventory_marks_all_modules_as_legacy_mirrors():
    """v1 routers are mounted mirrors, not the only production source.

    Product fixes for visible endpoints must land in the live main/router
    implementation and only then be mirrored here while the v1 aggregate
    exists. This keeps future fixes from being applied to a dead-looking file
    that production does not actually serve.
    """
    assert set(V1_ROUTE_INVENTORY.values()) == {"legacy_mirror"}


def test_visible_endpoint_fixes_are_not_v1_only():
    main_src = (REPO / "console/app/main.py").read_text(encoding="utf-8")
    run_logs_src = (REPO / "console/app/domains/pipeline/run_logs.py").read_text(
        encoding="utf-8"
    )
    v1_jobs_src = (REPO / "console/app/routers/v1/jobs.py").read_text(encoding="utf-8")
    v1_data_src = (REPO / "console/app/routers/v1/data.py").read_text(encoding="utf-8")

    assert '"/api/jobs/{job_id}/logs"' in main_src
    assert "_build_job_logs_payload_impl" in main_src
    assert "job_service=job_service" in main_src
    assert "job_service.get_scoped" in run_logs_src
    assert 'job_args.get("cartridge_id")' in run_logs_src
    assert "WHERE run_id=$1 AND cartridge=$2" in run_logs_src
    assert '"/api/jobs/{job_id}/logs"' in v1_jobs_src
    assert "job_service.get_scoped" in v1_jobs_src
    assert 'job_args.get("cartridge_id")' in v1_jobs_src
    assert "WHERE run_id=$1 AND cartridge=$2" in v1_jobs_src

    assert '"/api/datasets/{name}/detail"' in main_src
    assert "return _normalize_dataset_detail(" in main_src
    assert '"/api/datasets/{name}/detail"' in v1_data_src
    assert "return _normalize_dataset_detail(" in v1_data_src
