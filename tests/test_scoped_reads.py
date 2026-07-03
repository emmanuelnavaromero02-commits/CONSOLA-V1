from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.domains.data_platform import scoped_reads


USER = {
    "id": "user-1",
    "role": "tenant_admin",
    "tenant_id": "tenant-1",
    "workspace_id": "workspace-1",
    "active_tenant_id": "tenant-1",
    "active_workspace_id": "workspace-1",
    "allowed_cartridges": ["sap_successfactors"],
}


@pytest.fixture(autouse=True)
def _clear_cache():
    scoped_reads.SCOPED_READ_CACHE.clear()
    scoped_reads.SCOPED_READ_CACHE_LOCKS.clear()
    yield
    scoped_reads.SCOPED_READ_CACHE.clear()
    scoped_reads.SCOPED_READ_CACHE_LOCKS.clear()


def test_infers_bronze_sources_from_packaged_s3_reader():
    sql = (
        "select * from read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )

    assert scoped_reads.infer_bronze_sources_from_sql(sql) == [
        "raw/sap_successfactors/Candidate"
    ]


def test_merge_declared_and_inferred_sources_preserves_order():
    sql = (
        "select * from read_parquet("
        "'s3://lakehouse/raw/sap_successfactors/User/**/*.parquet')"
    )

    assert scoped_reads.merge_declared_and_inferred_bronze_sources(
        ["raw/sap_successfactors/Candidate", "raw/sap_successfactors/User"],
        sql,
    ) == [
        "raw/sap_successfactors/Candidate",
        "raw/sap_successfactors/User",
    ]


def test_scoped_bronze_s3_path_uses_tenant_workspace_partitions(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lakehouse-test")

    assert scoped_reads.scoped_bronze_s3_path(
        "raw/sap_successfactors/User",
        USER,
    ) == (
        "s3://lakehouse-test/raw/sap_successfactors/User/"
        "tenant_id=tenant-1/workspace_id=workspace-1/**/*.parquet"
    )


def test_scoped_bronze_s3_path_rejects_prepartitioned_paths():
    with pytest.raises(HTTPException) as exc:
        scoped_reads.scoped_bronze_s3_path(
            "raw/sap_successfactors/User/tenant_id=other/workspace_id=other",
            USER,
        )

    assert exc.value.status_code == 400


def test_rewrite_bronze_logical_paths_adds_scoped_read_options(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lakehouse-test")
    sql = "select * from read_parquet('raw/sap_successfactors/User')"

    rewritten = scoped_reads.rewrite_bronze_logical_paths(sql, USER)

    assert "read_parquet('raw/sap_successfactors/User')" not in rewritten
    assert (
        "read_parquet('s3://lakehouse-test/raw/sap_successfactors/User/"
        "tenant_id=tenant-1/workspace_id=workspace-1/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    ) in rewritten


def test_workspace_scope_requires_tenant_and_workspace():
    with pytest.raises(HTTPException) as exc:
        scoped_reads.workspace_scope_from_user({"role": "tenant_admin"})

    assert exc.value.status_code == 400


def test_scoped_read_cache_isolation_and_invalidation(monkeypatch):
    monkeypatch.setenv("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "60")
    other_user = {
        **USER,
        "workspace_id": "workspace-2",
        "active_workspace_id": "workspace-2",
    }

    scoped_reads.scoped_read_cache_set("catalog", USER, {"rows": [1]}, "gold")
    scoped_reads.scoped_read_cache_set("catalog", other_user, {"rows": [2]}, "gold")

    assert scoped_reads.scoped_read_cache_get("catalog", USER, "gold") == {
        "rows": [1]
    }
    assert scoped_reads.scoped_read_cache_get("catalog", other_user, "gold") == {
        "rows": [2]
    }

    scoped_reads.scoped_read_cache_invalidate("catalog", USER)

    assert scoped_reads.scoped_read_cache_get("catalog", USER, "gold") is None
    assert scoped_reads.scoped_read_cache_get("catalog", other_user, "gold") == {
        "rows": [2]
    }


@pytest.mark.asyncio
async def test_scoped_read_cache_get_or_set_singleflights(monkeypatch):
    monkeypatch.setenv("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "60")
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"rows": [calls]}

    results = await asyncio.gather(
        *(
            scoped_reads.scoped_read_cache_get_or_set(
                "schema",
                USER,
                ("raw/sap_successfactors/User",),
                loader,
            )
            for _ in range(8)
        )
    )

    assert results == [{"rows": [1]}] * 8
    assert calls == 1
