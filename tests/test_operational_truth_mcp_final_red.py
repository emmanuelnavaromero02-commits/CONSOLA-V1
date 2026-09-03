from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _publication_heads(monkeypatch: pytest.MonkeyPatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.syspath_prepend(str(ROOT / "mcp-infra"))
    for name in (
        "AIRFLOW_USER",
        "AIRFLOW_PASSWORD",
        "PG_PASSWORD",
        "PG_GOLD_USER",
        "PG_GOLD_PASSWORD",
        "SUPERSET_USER",
        "SUPERSET_PASSWORD",
    ):
        monkeypatch.setenv(name, "test")
    return importlib.import_module("app.publication_heads")


def _scope() -> dict[str, object]:
    return {
        "trusted": True,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "allowed_cartridges": ["replicon"],
        "allowed_buckets": ["lakehouse"],
    }


@pytest.mark.parametrize(
    "key",
    [
        "raw/replicon/users/tenant_id=tenant-b/workspace_id=workspace-b/data.parquet",
        "raw/other/users/tenant_id=tenant-a/workspace_id=workspace-a/data.parquet",
        "raw/replicon/users/tenant_id=tenant-a/workspace_id=workspace-a%2f..%2fworkspace-b/data.parquet",
        "raw//replicon/users/tenant_id=tenant-a/workspace_id=workspace-a/data.parquet",
        "raw/replicon/users/tenant_id=tenant-a/workspace_id=workspace-\N{FULLWIDTH LATIN SMALL LETTER A}/data.parquet",
    ],
)
def test_raw_object_requires_exact_canonical_scope_and_cartridge(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    heads = _publication_heads(monkeypatch)

    assert heads.published_object(key, _scope(), "lakehouse") is False


def test_raw_scoped_listing_accepts_one_trailing_prefix_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heads = _publication_heads(monkeypatch)
    prefix = "raw/replicon/users/tenant_id=tenant-a/" "workspace_id=workspace-a/"

    assert heads.published_prefix(prefix, _scope(), "lakehouse") is True


def test_mcp_duckdb_runtime_is_prebuilt_and_never_installs_extensions() -> None:
    runtime = ROOT / "mcp-infra" / "app" / "duckdb_runtime.py"
    assert runtime.is_file()
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            runtime,
            ROOT / "mcp-infra" / "app" / "main.py",
            ROOT / "mcp-infra" / "app" / "tools" / "cartridges.py",
        )
    )
    assert "INSTALL httpfs" not in sources
    assert "autoinstall_known_extensions" in sources
    assert "autoload_known_extensions" in sources
    assert "LOAD httpfs" in sources
    dockerfile = (ROOT / "mcp-infra" / "Dockerfile").read_text(encoding="utf-8")
    assert "install_duckdb_extensions.py" in dockerfile
    assert "USER appuser" in dockerfile


def test_cartridge_mcp_duckdb_runtime_is_pinned_and_load_only() -> None:
    cartridges = (
        "hubspot",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
    )
    for cartridge in cartridges:
        root = ROOT / "cartridges" / cartridge
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        service = (root / "app" / "services" / "duckdb_service.py").read_text(
            encoding="utf-8"
        )
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

        assert "duckdb==1.2.2" in requirements
        assert "INSTALL " not in service
        assert service.index("autoinstall_known_extensions") < service.index(
            "LOAD httpfs"
        )
        assert service.index("autoload_known_extensions") < service.index("LOAD httpfs")
        assert "ENV HOME=/app" in dockerfile
        assert "INSTALL httpfs" in dockerfile
        assert "INSTALL aws" in dockerfile
        assert "USER appuser" in dockerfile
