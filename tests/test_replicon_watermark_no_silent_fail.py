"""Sprint v1.32 — Replicon watermark updates must fail loudly.

v1.40 update: the cartridge was restored from the original ZIP and
the watermark logic now lives in
``cartridges/replicon/app/services/watermark_service.py`` (a proper
service module) rather than as an inline ``_watermark_set`` helper
in the DAG file. The contract is the same — never swallow a write
failure silently — so the test is rewritten to scan the new
location.
"""
from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WATERMARK_SERVICE = REPO_ROOT / "cartridges/replicon/app/services/watermark_service.py"
DAG_FILE = REPO_ROOT / "cartridges/replicon/dags/replicon_extract.py"


def _function_nodes(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def test_watermark_service_has_no_silent_except_pass():
    """No function in the watermark_service may swallow exceptions
    via a bare ``except: pass``. A silent failure here would leave
    incremental extracts replaying or skipping data forever."""
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
    """Every requests.get / requests.post in the cartridge DAG that
    the original ZIP shipped with the watermark logic must follow up
    with ``raise_for_status()`` so an HTTP 5xx surfaces as a task
    failure rather than silently writing a bogus watermark."""
    src = DAG_FILE.read_text(encoding="utf-8")
    # The original cartridge calls raise_for_status() right after
    # every requests.get/post; locking the count at >=4 catches a
    # regression that drops one.
    assert src.count("raise_for_status()") >= 4, (
        "replicon DAG dropped raise_for_status() — HTTP errors would "
        "be swallowed and the watermark would advance past failed "
        "extractions"
    )
