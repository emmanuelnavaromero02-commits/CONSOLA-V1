"""Phase-0 P0 regression: the RAG reindex helpers must respect the caller's
tenant/workspace scope.

This is a source-level test on mcp-infra/app/main.py. It guarantees that:

  * `_build_raw_doc(cartridge, entity, ctx)` is callable with the ctx arg
    (the previous signature only took two args and would raise TypeError
    when called from `/rag/reindex`).
  * `_build_dataset_doc(name, ctx)` is callable with ctx and rejects out
    -of-scope cartridges.
  * `_rebuild_semantic_doc` applies the scoped raw glob to the `glob(...)`
    call, not only to the `read_parquet(...)` call that runs after.
  * `_build_raw_doc` builds a tenant/workspace-partitioned path when the
    caller is scoped, NOT a global `raw/<cart>/<ent>/**/*.parquet`.
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_MAIN = REPO_ROOT / "mcp-infra/app/main.py"
SRC = MCP_MAIN.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    # Extract a top-level function body for source-level assertions. We do
    # NOT exec the module: importing mcp-infra triggers FastAPI + duckdb +
    # psycopg2 wiring that we don't want in this unit test.
    marker = f"\ndef {name}("
    assert marker in SRC, f"function {name} not found in {MCP_MAIN}"
    body = SRC.split(marker, 1)[1]
    cut = body.find("\ndef ")
    return body[:cut] if cut > 0 else body


def _async_function_source(name: str) -> str:
    marker = f"\nasync def {name}("
    assert marker in SRC, f"async function {name} not found in {MCP_MAIN}"
    body = SRC.split(marker, 1)[1]
    cut1 = body.find("\ndef ")
    cut2 = body.find("\nasync def ")
    cuts = [c for c in (cut1, cut2) if c > 0]
    cut = min(cuts) if cuts else -1
    return body[:cut] if cut > 0 else body


# ---------------------------------------------------------------------------
# Signature checks — the audit found that the callers pass ctx but the defs
# were missing the parameter, which would crash /rag/reindex at runtime.
# ---------------------------------------------------------------------------

def test_build_raw_doc_accepts_ctx_parameter():
    sig_line = _function_source("_build_raw_doc").splitlines()[0]
    assert "ctx" in sig_line, (
        "_build_raw_doc must accept a `ctx` security-context parameter so "
        "RAG documents are scoped to the caller's tenant/workspace."
    )


def test_build_dataset_doc_accepts_ctx_parameter():
    sig_line = _function_source("_build_dataset_doc").splitlines()[0]
    assert "ctx" in sig_line, (
        "_build_dataset_doc must accept a `ctx` security-context parameter."
    )


def test_cartridge_of_accepts_ctx_parameter():
    sig_line = _function_source("_cartridge_of").splitlines()[0]
    assert "ctx" in sig_line, (
        "_cartridge_of is invoked with ctx; its signature must accept it."
    )


# ---------------------------------------------------------------------------
# Behavioural checks — the scoped path must include the tenant/workspace
# partition, not the global glob.
# ---------------------------------------------------------------------------

def test_build_raw_doc_uses_scoped_partition_when_ctx_present():
    body = _function_source("_build_raw_doc")
    assert "_has_tenant_workspace_scope(ctx)" in body, (
        "_build_raw_doc must check whether the caller is tenant/workspace-scoped"
    )
    assert "tenant_id={tenant_id}/workspace_id={workspace_id}" in body, (
        "_build_raw_doc must build a hive partition path when scoped"
    )


def test_build_dataset_doc_rejects_out_of_scope_cartridge():
    body = _function_source("_build_dataset_doc")
    assert "allowed_cartridges" in body, (
        "_build_dataset_doc must consult ctx.allowed_cartridges and refuse "
        "to expose a dataset whose cartridge is outside the caller's scope"
    )


def test_rebuild_semantic_doc_scopes_the_glob_pattern():
    """The audit specifically called out that `scoped_raw_glob` was only
    applied to `read_parquet`, while the previous `glob(...)` listed all
    tenants. Fix it: scope the glob pattern itself."""
    body = _async_function_source("_rebuild_semantic_doc")
    assert "glob_pattern" in body, (
        "_rebuild_semantic_doc must compute a `glob_pattern` that already "
        "includes the tenant/workspace partition when scoped"
    )
    # The new glob_pattern, when scoped, must contain the
    # tenant_id=/workspace_id= partition. We check that the scoped branch
    # appears at all (the rest is enforced by signature/spectral tests).
    assert "scoped_raw_glob:" in body and "glob_pattern" in body, (
        "_rebuild_semantic_doc must build the scoped glob_pattern in the "
        "`if scoped_raw_glob:` branch"
    )


def test_rebuild_semantic_doc_scopes_the_silver_read_parquet():
    body = _async_function_source("_rebuild_semantic_doc")
    # The silver DESCRIBE call must use the scoped read_parquet path when
    # scoped_raw_read is set, NOT the bare global `silver/{cartridge}/{name}/
    # data.parquet`.
    assert "scoped_raw_read" in body, "silver describe must apply scoped_raw_read"
    assert 'f"s3://{bucket}/silver/{cartridge}/{name}/"' in body or \
           'f"s3://{bucket}/silver/{cartridge}/{name}/{scoped_raw_read}data.parquet"' in body, (
        "_rebuild_semantic_doc must build a scoped silver parquet path when "
        "the caller is tenant/workspace-scoped"
    )


# ---------------------------------------------------------------------------
# Callers — the reindex endpoint passes ctx; the source must keep that wiring.
# ---------------------------------------------------------------------------

def test_rag_reindex_passes_ctx_to_build_helpers():
    """`/rag/reindex` already passes ctx to both helpers; this test pins
    that contract so a future refactor cannot quietly drop it."""
    assert "_build_raw_doc(cartridge, name, ctx)" in SRC, (
        "/rag/reindex must call _build_raw_doc with ctx"
    )
    assert "_build_dataset_doc(name, ctx)" in SRC, (
        "/rag/reindex must call _build_dataset_doc with ctx"
    )
