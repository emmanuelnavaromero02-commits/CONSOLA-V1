from __future__ import annotations

import importlib.util
import ast
import sys
from pathlib import Path
from urllib.parse import urlsplit

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


def test_generated_dag_uses_guard_for_all_external_url_sources():
    rendered = gen.generate_dag_code(
        "synthetic",
        "Orders",
        {
            "connector": {
                "api": {"base_url_env": "SYNTHETIC_BASE_URL"},
                "auth": {"type": "oauth2_client_credentials"},
            }
        },
    )["code"]

    assert "from outbound_egress_guard import guarded_session" in rendered
    assert "return guarded_session(retries=retry)" in rendered
    assert "requests.Session()" not in rendered
    assert "session.get(next_url" in rendered
    assert "session.get(status_url" in rendered
    assert "session.get(download_url" in rendered
    assert gen.validate_dag_code(rendered)["valid"] is True


def test_generated_dag_is_gcs_provider_aware_and_cloud_bucket_safe():
    rendered = gen.generate_dag_code(
        "synthetic",
        "Orders",
        {"connector": {"api": {}, "auth": {"type": "bearer_token"}}},
    )["code"]

    assert 'provider == "gcs"' in rendered
    assert 'os.environ.get("GCS_ACCESS_KEY_ID")' in rendered
    assert 'os.environ.get("GCS_SECRET_ACCESS_KEY")' in rendered
    assert '"region": "auto"' in rendered
    assert 'storage["provider"] == "minio"' in rendered
    assert 'storage["provider"] == "s3" and not storage["access_key"]' in rendered
    assert "IamAwsProvider" in rendered
    assert "MINIO_BUCKET =" not in rendered
    assert "_pipeline_run_save(run_id, \"failed\", 0, None, str(exc))" not in rendered
    assert "raise RuntimeError(failure_code) from None" in rendered
    assert gen.validate_dag_code(rendered)["valid"] is True


def test_generated_endpoint_parser_preserves_local_minio_host_and_port():
    rendered = gen.generate_dag_code(
        "synthetic",
        "Orders",
        {"connector": {"api": {}, "auth": {"type": "bearer_token"}}},
    )["code"]
    tree = ast.parse(rendered)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_endpoint_host"
    )
    namespace = {"urlsplit": urlsplit}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "<helper>", "exec"), namespace)

    assert namespace["_endpoint_host"]("minio:9000") == "minio:9000"


def test_generated_storage_requires_gcs_pair_but_allows_s3_identity(monkeypatch):
    import os

    rendered = gen.generate_dag_code(
        "synthetic",
        "Orders",
        {"connector": {"api": {}, "auth": {"type": "bearer_token"}}},
    )["code"]
    tree = ast.parse(rendered)
    wanted = {"_airflow_variable", "_endpoint_host", "_storage_config"}
    helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]

    class FakeVariable:
        @staticmethod
        def get(_name, default_var=""):
            return default_var

    namespace = {"os": os, "urlsplit": urlsplit, "Variable": FakeVariable}
    exec(compile(ast.Module(body=helpers, type_ignores=[]), "<helpers>", "exec"), namespace)

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.delenv("GCS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("GCS_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="^storage_credentials_missing$"):
        namespace["_storage_config"]()

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    storage = namespace["_storage_config"]()
    assert storage["provider"] == "s3"
    assert storage["access_key"] == ""
    assert storage["secret_key"] == ""


def test_generated_public_failure_code_drops_secret_url_and_body():
    rendered = gen.generate_dag_code(
        "synthetic",
        "Orders",
        {"connector": {"api": {}, "auth": {"type": "bearer_token"}}},
    )["code"]
    tree = ast.parse(rendered)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_public_failure_code"
    )
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "<helper>", "exec"), namespace)
    sentinel = "SECRET-TOKEN https://private.example/odata body=employee@example.com"

    code = namespace["_public_failure_code"](RuntimeError(sentinel))

    assert code == "extraction_failed"
    assert "SECRET" not in code


def test_packaged_replicon_dag_guards_base_and_secondary_downloads():
    source = (
        REPO / "cartridges/replicon/dags/replicon_extract.py"
    ).read_text(encoding="utf-8")

    assert "from outbound_egress_guard import guarded_session" in source
    assert "self._s = guarded_session()" in source
    assert "self._s.get(url, timeout=120)" in source
    assert "_req.Session()" not in source
    assert "_req.get(url" not in source


def test_airflow_shared_guard_rejects_loopback_without_network():
    path = REPO / "airflow/dags/outbound_egress_guard.py"
    spec = importlib.util.spec_from_file_location("_fseg_airflow_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    with pytest.raises(module.EgressGuardError, match="non-public"):
        module.resolve_public_url("http://127.0.0.1:8000/api/health")
