from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_superset_tools(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "mcp-infra"))
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "pg-password")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin-password")
    return importlib.import_module("app.tools.superset")


@pytest.mark.parametrize(
    ("func_name", "kwargs"),
    [
        ("superset_create_database", {"name": "gold", "sqlalchemy_uri": "postgresql://x/y"}),
        ("superset_create_dataset", {"database_id": 1, "table_name": "gold_sales"}),
        ("superset_create_chart", {"name": "sales", "viz_type": "bar", "datasource_id": 1}),
        ("superset_create_dashboard", {"title": "Exec Dashboard"}),
        ("superset_import_dashboard", {"files": {"dashboard_export/metadata.yaml": "version: 1.0.0"}}),
    ],
)
@pytest.mark.asyncio
async def test_superset_create_tools_disabled_in_production(monkeypatch, func_name, kwargs):
    superset = _load_superset_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match=f"{func_name} is disabled outside development"):
        await getattr(superset, func_name)(**kwargs)
