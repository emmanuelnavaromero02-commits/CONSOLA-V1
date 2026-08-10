from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

from app.services import dag_code_generator as gen  # noqa: E402


def test_dag_constants_are_json_encoded():
    rendered = gen._const("BASE_URL_ENV", 'EVIL"\nBAD = 1\n"')
    assert rendered == 'BASE_URL_ENV = "EVIL\\"\\nBAD = 1\\n\\""'


def test_normal_generated_style_code_passes():
    code = """
from __future__ import annotations
import csv
import io
import os
import time
import uuid
from datetime import datetime
from urllib.parse import urljoin
import pandas as pd
import requests
from airflow.decorators import dag, task
from minio import Minio
from urllib3.util.retry import Retry
from runtime_security_context import build_pipeline_run_context

VALUE = os.environ.get("X", "ok")
"""
    result = gen.validate_dag_code(code)
    assert result["valid"] is True
    assert "ast_guard" in result["checks"]


def test_validate_dag_code_rejects_bad_syntax():
    result = gen.validate_dag_code("def broken(:\n    pass\n")
    assert result["valid"] is False
    assert "SyntaxError" in result["stderr"] or "py_compile" in result["stderr"]


def test_validate_dag_code_rejects_imports_outside_allowlist():
    result = gen.validate_dag_code("import definitely_not_an_airflow_runtime_dep\n")
    assert result["valid"] is False
    assert "Missing import" in result["stderr"]


@pytest.mark.parametrize(
    "code, expected",
    [
        ("import os\nos.system('id')\n", "os.system"),
        ("import os\nos.popen('id')\n", "os.popen"),
        ("import subprocess\nsubprocess.run(['id'])\n", "subprocess"),
        ("eval('1+1')\n", "eval"),
        ("exec('x=1')\n", "exec"),
        ("compile('x=1', '<x>', 'exec')\n", "compile"),
        ("__import__('os')\n", "__import__"),
        ("import pickle\npickle.loads(b'bad')\n", "pickle"),
        ("import yaml\nyaml.load('a: 1')\n", "yaml.load"),
        ("open('/tmp/x', 'w')\n", "open"),
        ("from pathlib import Path\nPath('/tmp/x').open(mode='w')\n", "open"),
    ],
)
def test_validate_dag_code_rejects_forbidden_ast_calls(code, expected):
    result = gen.validate_dag_code(code)
    assert result["valid"] is False
    assert expected in result["stderr"]


def test_validate_dag_code_allows_safe_yaml_loader():
    result = gen.validate_dag_code("import yaml\nyaml.safe_load('a: 1')\n")
    assert result["valid"] is True
