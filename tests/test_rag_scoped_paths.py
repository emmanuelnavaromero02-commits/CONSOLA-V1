from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_MAIN = REPO_ROOT / "mcp-infra/app/main.py"
SRC = MCP_MAIN.read_text(encoding="utf-8")
TREE = ast.parse(SRC, filename=str(MCP_MAIN))


def _function_parameter_names(name: str) -> set[str]:
    for node in TREE.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            parameters = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                parameters.append(node.args.vararg)
            if node.args.kwarg is not None:
                parameters.append(node.args.kwarg)
            return {parameter.arg for parameter in parameters}
    raise AssertionError(f"function {name} not found in {MCP_MAIN}")


def _function_source(name: str) -> str:
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


def test_build_raw_doc_accepts_ctx_parameter():
    assert "ctx" in _function_parameter_names("_build_raw_doc"), (
        "_build_raw_doc must accept a `ctx` security-context parameter so "
        "RAG documents are scoped to the caller's tenant/workspace."
    )


def test_build_dataset_doc_accepts_ctx_parameter():
    assert "ctx" in _function_parameter_names(
        "_build_dataset_doc"
    ), "_build_dataset_doc must accept a `ctx` security-context parameter."


def test_cartridge_of_accepts_ctx_parameter():
    assert "ctx" in _function_parameter_names(
        "_cartridge_of"
    ), "_cartridge_of is invoked with ctx; its signature must accept it."


def test_build_raw_doc_uses_scoped_partition_when_ctx_present():
    body = _function_source("_build_raw_doc")
    assert (
        "_has_tenant_workspace_scope(ctx)" in body
    ), "_build_raw_doc must check whether the caller is tenant/workspace-scoped"
    assert (
        "tenant_id={tenant_id}/workspace_id={workspace_id}" in body
    ), "_build_raw_doc must build a hive partition path when scoped"


def test_build_dataset_doc_rejects_out_of_scope_cartridge():
    body = _function_source("_build_dataset_doc")
    assert "allowed_cartridges" in body, (
        "_build_dataset_doc must consult ctx.allowed_cartridges and refuse "
        "to expose a dataset whose cartridge is outside the caller's scope"
    )


def test_rebuild_semantic_doc_scopes_the_glob_pattern():
    body = _async_function_source("_rebuild_semantic_doc")
    assert "glob_pattern" in body, (
        "_rebuild_semantic_doc must compute a `glob_pattern` that already "
        "includes the tenant/workspace partition when scoped"
    )
    assert "scoped_raw_glob:" in body and "glob_pattern" in body, (
        "_rebuild_semantic_doc must build the scoped glob_pattern in the "
        "`if scoped_raw_glob:` branch"
    )


def test_rebuild_semantic_doc_scopes_the_silver_read_parquet():
    body = _async_function_source("_rebuild_semantic_doc")
    assert "scoped_raw_read" in body, "silver describe must apply scoped_raw_read"
    assert (
        'f"s3://{bucket}/silver/{cartridge}/{name}/"' in body
        or 'f"s3://{bucket}/silver/{cartridge}/{name}/{scoped_raw_read}data.parquet"'
        in body
    ), (
        "_rebuild_semantic_doc must build a scoped silver parquet path when "
        "the caller is tenant/workspace-scoped"
    )


def test_rag_reindex_passes_ctx_to_build_helpers():
    assert (
        "_build_raw_doc(cartridge, name, ctx)" in SRC
    ), "/rag/reindex must call _build_raw_doc with ctx"
    assert (
        "_build_dataset_doc(name, ctx)" in SRC
    ), "/rag/reindex must call _build_dataset_doc with ctx"
