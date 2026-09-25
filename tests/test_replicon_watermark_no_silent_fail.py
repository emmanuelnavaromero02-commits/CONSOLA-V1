from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WATERMARK_SERVICE = REPO_ROOT / "cartridges/replicon/app/services/watermark_service.py"
DAG_FILE = REPO_ROOT / "cartridges/replicon/dags/replicon_extract.py"


def _function_nodes(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def test_watermark_service_has_no_silent_except_pass():
    src = WATERMARK_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for func in _function_nodes(tree):
        for child in ast.walk(func):
            assert not (
                isinstance(child, ast.ExceptHandler)
                and len(child.body) == 1
                and isinstance(child.body[0], ast.Pass)
            ), (
                f"{func.name} in watermark_service.py must not swallow "
                f"watermark write failures with `except: pass`"
            )


def test_replicon_dag_http_calls_raise_for_status():
    src = DAG_FILE.read_text(encoding="utf-8")
    assert src.count("raise_for_status()") >= 4, (
        "replicon DAG dropped raise_for_status() — HTTP errors would "
        "be swallowed and the watermark would advance past failed "
        "extractions"
    )
