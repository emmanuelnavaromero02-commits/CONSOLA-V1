"""Sprint v1.32 — Replicon watermark updates must fail loudly."""
from __future__ import annotations

import ast
from pathlib import Path


DAG = Path(__file__).resolve().parents[1] / "airflow/dags/replicon_extract.py"


def _watermark_set_node() -> ast.FunctionDef:
    tree = ast.parse(DAG.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_watermark_set":
            return node
    raise AssertionError("_watermark_set not found")


def test_watermark_set_has_no_silent_except_pass():
    node = _watermark_set_node()

    for child in ast.walk(node):
        assert not (
            isinstance(child, ast.ExceptHandler)
            and len(child.body) == 1
            and isinstance(child.body[0], ast.Pass)
        ), "_watermark_set must not swallow watermark write failures"


def test_watermark_set_raises_for_http_errors():
    source = ast.get_source_segment(DAG.read_text(), _watermark_set_node()) or ""

    assert "raise_for_status()" in source
