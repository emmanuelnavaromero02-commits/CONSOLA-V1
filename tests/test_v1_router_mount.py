from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_console_mounts_v1_router_aggregate():
    src = (REPO / "console/app/main.py").read_text(encoding="utf-8")
    assert "from app.routers import v1 as v1_routers" in src
    assert "for router in v1_routers.ROUTERS:" in src
    assert "app.include_router(router)" in src


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
