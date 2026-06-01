from __future__ import annotations

import pytest

from app.services import mcp_registry


def _ctx(*, role: str = "admin", allowed_prefixes: list[str] | None = None) -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": role,
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "allowed_cartridges": ["replicon"],
        "allowed_prefixes": allowed_prefixes or ["raw/replicon/"],
        "permissions": ["datasets.read", "cartridges.read", "cartridges.execute"],
    }


def _sql(path: str) -> str:
    return f"SELECT * FROM read_parquet('{path}')"


def test_scoped_admin_query_kb_rejects_foreign_physical_path():
    with pytest.raises(PermissionError, match="path not allowed"):
        mcp_registry._enforce_outbound_scope(
            "replicon",
            "cartridge",
            "query_kb",
            {
                "sql": _sql(
                    "s3://lakehouse/raw/replicon/TimeEntry/"
                    "tenant_id=tenant-9/workspace_id=ws-9/data.parquet"
                )
            },
            _ctx(),
        )


def test_scoped_admin_query_kb_rejects_broad_path_even_with_allowed_prefix():
    with pytest.raises(PermissionError, match="path not allowed"):
        mcp_registry._enforce_outbound_scope(
            "replicon",
            "cartridge",
            "query_kb",
            {"sql": _sql("s3://lakehouse/raw/replicon/TimeEntry/**/*.parquet")},
            _ctx(allowed_prefixes=["raw/replicon/"]),
        )


def test_scoped_admin_query_kb_allows_own_physical_path():
    mcp_registry._enforce_outbound_scope(
        "replicon",
        "cartridge",
        "query_kb",
        {
            "sql": _sql(
                "s3://lakehouse/raw/replicon/TimeEntry/"
                "tenant_id=tenant-1/workspace_id=ws-1/data.parquet"
            )
        },
        _ctx(),
    )
