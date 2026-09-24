from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPLICON_DAG = ROOT / "cartridges" / "replicon" / "dags" / "replicon_extract.py"


def _load_replicon_dag(monkeypatch: pytest.MonkeyPatch):
    class DummyTaskResult:
        pass

    class FakeVariable:
        values: dict[str, str] = {}

        @classmethod
        def get(cls, name: str, default_var: str = "") -> str:
            return cls.values.get(name, default_var)

    def task(fn):
        def call(*_args, **_kwargs):
            return DummyTaskResult()

        call.__name__ = fn.__name__
        return call

    def dag(*_args, **_kwargs):
        return lambda fn: fn

    airflow = types.ModuleType("airflow")
    airflow_decorators = types.ModuleType("airflow.decorators")
    airflow_decorators.dag = dag
    airflow_decorators.task = task
    airflow_models = types.ModuleType("airflow.models")
    airflow_models.Variable = FakeVariable
    outbound = types.ModuleType("outbound_egress_guard")
    outbound.guarded_session = lambda: object()
    app = types.ModuleType("app")
    app.__path__ = []
    app_core = types.ModuleType("app.core")
    app_core.__path__ = []
    auth_factory = types.ModuleType("app.core.auth_factory")
    auth_factory.auth_trace = lambda *_args, **_kwargs: "auth"
    auth_factory.build_auth_headers = lambda *_args, **_kwargs: ({}, "none", {})

    for name, module in {
        "airflow": airflow,
        "airflow.decorators": airflow_decorators,
        "airflow.models": airflow_models,
        "outbound_egress_guard": outbound,
        "app": app,
        "app.core": app_core,
        "app.core.auth_factory": auth_factory,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    name = "_phase0_replicon_extract"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, REPLICON_DAG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, FakeVariable


def test_replicon_gcs_uses_exact_pair_and_never_creates_bucket(monkeypatch):
    module, variable = _load_replicon_dag(monkeypatch)
    variable.values = {
        "minio_endpoint": "stale-minio:9000",
        "minio_access_key": "stale-minio-access",
        "minio_secret_key": "stale-minio-secret",
        "minio_bucket": "stale-minio-bucket",
    }
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-ses-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-ses-secret")

    storage = module._storage_config()

    assert storage == {
        "provider": "gcs",
        "endpoint": "storage.googleapis.com",
        "access_key": "gcs-access",
        "secret_key": "gcs-secret",
        "bucket": "omega-gcs",
        "secure": True,
        "region": "auto",
    }

    class FakeClient:
        def bucket_exists(self, _bucket):
            raise AssertionError("GCS bucket existence must not be probed")

        def make_bucket(self, _bucket):
            raise AssertionError("GCS bucket must never be created by the DAG")

        def put_object(self, bucket, key, data, size, **_kwargs):
            assert bucket == "omega-gcs"
            assert key.startswith("raw/replicon/User/")
            assert data.read() == b"parquet"
            assert size == len(b"parquet")

    monkeypatch.setattr(module, "_minio_client", lambda _storage: FakeClient())
    pyarrow = types.ModuleType("pyarrow")
    pyarrow.Table = types.SimpleNamespace(from_pandas=lambda _df: object())
    parquet = types.ModuleType("pyarrow.parquet")
    parquet.write_table = lambda _table, buffer: buffer.write(b"parquet")
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet)

    uri = module._upload_parquet(object(), "User", "run-1")

    assert uri.startswith("s3://omega-gcs/raw/replicon/User/")


def test_replicon_gcs_missing_pair_fails_without_minio_fallback(monkeypatch):
    module, variable = _load_replicon_dag(monkeypatch)
    variable.values = {
        "minio_access_key": "stale-minio-access",
        "minio_secret_key": "stale-minio-secret",
        "minio_bucket": "stale-minio-bucket",
    }
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.delenv("GCS_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(RuntimeError, match="^storage_credentials_missing$"):
        module._storage_config()


def test_replicon_s3_without_static_pair_uses_aws_identity_provider(monkeypatch):
    module, _variable = _load_replicon_dag(monkeypatch)
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    captured: dict[str, object] = {}

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    import minio
    from minio.credentials.providers import IamAwsProvider

    monkeypatch.setattr(minio, "Minio", FakeMinio)
    module._minio_client(module._storage_config())

    assert captured["kwargs"]["endpoint"] == "s3.us-east-1.amazonaws.com"
    assert isinstance(captured["kwargs"]["credentials"], IamAwsProvider)
    assert "access_key" not in captured["kwargs"]
    assert "secret_key" not in captured["kwargs"]


def _load_mcp_config(monkeypatch: pytest.MonkeyPatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.syspath_prepend(str(ROOT / "mcp-infra"))
    for name in (
        "AIRFLOW_USER",
        "AIRFLOW_PASSWORD",
        "PG_PASSWORD",
        "SUPERSET_USER",
        "SUPERSET_PASSWORD",
    ):
        monkeypatch.setenv(name, "test-value")
    config = importlib.import_module("app.config")
    runtime = importlib.import_module("app.lakehouse_runtime")
    return config, runtime


def test_mcp_gcs_resolver_and_duckdb_use_only_gcs_pair(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "stale-minio-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "stale-minio-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-ses-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-ses-secret")
    config, runtime = _load_mcp_config(monkeypatch)

    resolved = config.settings.resolved_storage
    assert resolved.provider == "gcs"
    assert resolved.endpoint == "storage.googleapis.com"
    assert resolved.bucket == "omega-gcs"
    assert resolved.access_key == "gcs-access"
    assert resolved.secret_key == "gcs-secret"
    assert resolved.region == "auto"

    class FakeDuckDB:
        statements: list[tuple[str, list[str] | None]] = []

        def execute(self, statement, params=None):
            self.statements.append((statement, params))
            return self

    connection = FakeDuckDB()
    runtime.configure_duckdb_s3(connection)
    joined = "\n".join(statement for statement, _params in connection.statements)
    params = [value for _statement, values in connection.statements for value in (values or [])]
    assert "gcs-access" not in joined
    assert "gcs-secret" not in joined
    assert "gcs-access" in params
    assert "gcs-secret" in params
    assert "stale-minio" not in joined
    assert "unrelated-ses" not in joined
    assert ("SET s3_region = ?", ["auto"]) in connection.statements


def test_mcp_never_creates_cloud_bucket(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    _, runtime = _load_mcp_config(monkeypatch)

    class FakeClient:
        def bucket_exists(self, _bucket):
            raise AssertionError("cloud bucket existence must not be probed")

        def make_bucket(self, _bucket):
            raise AssertionError("cloud bucket must not be created")

    assert runtime.ensure_local_bucket(FakeClient()) == "omega-gcs"


def test_mcp_local_minio_preserves_host_port_and_can_create_bucket(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "local-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "local-secret")
    monkeypatch.setenv("MINIO_BUCKET", "lakehouse")
    config, runtime = _load_mcp_config(monkeypatch)

    assert config.settings.resolved_storage.endpoint == "minio:9000"

    class FakeClient:
        checked = False
        created = False

        def bucket_exists(self, bucket):
            self.checked = bucket == "lakehouse"
            return False

        def make_bucket(self, bucket):
            self.created = bucket == "lakehouse"

    client = FakeClient()
    assert runtime.ensure_local_bucket(client) == "lakehouse"
    assert client.checked is True
    assert client.created is True


def test_mcp_s3_client_uses_imdsv2_when_static_pair_absent(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    _, runtime = _load_mcp_config(monkeypatch)
    captured: dict[str, object] = {}

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr("minio.Minio", FakeMinio)
    runtime.minio_compatible_client()

    assert captured["args"] == ("s3.us-east-1.amazonaws.com",)
    assert isinstance(
        captured["kwargs"]["credentials"], runtime.Ec2ImdsV2Provider
    )
    assert "access_key" not in captured["kwargs"]
    assert "secret_key" not in captured["kwargs"]


def test_mcp_duckdb_s3_role_loads_preinstalled_aws_extension(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    _, runtime = _load_mcp_config(monkeypatch)
    statements: list[tuple[str, list[str] | None]] = []

    class FakeDuckDB:
        def execute(self, statement, params=None):
            statements.append((statement, params))
            return self

    runtime.configure_duckdb_s3(FakeDuckDB())

    sql = "\n".join(statement for statement, _params in statements)
    assert "LOAD aws;" in sql
    assert "CALL load_aws_credentials();" in sql
    assert "SET s3_access_key_id" not in sql
    assert "SET s3_secret_access_key" not in sql


def test_mcp_duckdb_exception_cannot_echo_bound_secret(monkeypatch):
    sentinel = "SECRET-GCS-SENTINEL"
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", sentinel)
    _, runtime = _load_mcp_config(monkeypatch)

    class RejectingDuckDB:
        def execute(self, statement, params=None):
            assert sentinel not in statement
            raise RuntimeError("duckdb setup failed")

    with pytest.raises(RuntimeError) as caught:
        runtime.configure_duckdb_s3(RejectingDuckDB())

    assert str(caught.value) == "storage_access_denied"
    assert sentinel not in str(caught.value)


def test_mcp_kb_tool_does_not_return_duckdb_secret_error(monkeypatch):
    sentinel = "SECRET-GCS-SENTINEL https://storage.googleapis.com/private"
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    _load_mcp_config(monkeypatch)
    cartridges = importlib.import_module("app.tools.cartridges")

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return ("SELECT 1", None, None)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(cartridges, "_conn", lambda: FakeConnection())
    monkeypatch.setattr(
        cartridges,
        "_duckdb",
        lambda: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    result = cartridges.cartridge_run_kb("synthetic", "kb_one")

    assert result["error"] == "lakehouse_query_failed"
    assert "SECRET" not in repr(result)
    assert "storage.googleapis.com" not in repr(result)


@pytest.mark.parametrize(
    "cartridge",
    ("hubspot", "salesforce", "sap_hcm", "sap_s4hana"),
)
def test_cartridge_duckdb_setup_binds_secret_and_returns_safe_error(cartridge):
    sentinel = "SECRET-GCS-HMAC-SENTINEL"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "cartridges" / cartridge),
        "LAKEHOUSE_PROVIDER": "gcs",
        "LAKEHOUSE_ENDPOINT": "storage.googleapis.com",
        "GCS_BUCKET": "omega-gcs",
        "GCS_ACCESS_KEY_ID": "gcs-access",
        "GCS_SECRET_ACCESS_KEY": sentinel,
        "DATABASE_URL": "postgresql://example.invalid/omega",
        "PG_USER": "test-user",
        "PG_PASSWORD": "test-password",
        "FIELD_ENCRYPTION_KEY": "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=",
    }
    script = f"""
from app.services import duckdb_service as service

sentinel = {sentinel!r}

class FakeConnection:
    def execute(self, statement, params=None):
        assert sentinel not in statement
        if statement.startswith("SET s3_secret_access_key"):
            assert params == [sentinel]
            raise RuntimeError("driver detail " + sentinel)
        return self

    def close(self):
        return None

service.duckdb.connect = lambda: FakeConnection()
try:
    service._get_duckdb_connection()
except RuntimeError as exc:
    assert str(exc) == "storage_access_denied"
    assert sentinel not in str(exc)
else:
    raise AssertionError("expected provider setup failure")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "cartridge",
    ("hubspot", "replicon", "salesforce", "sap_hcm", "sap_s4hana"),
)
def test_cartridge_s3_role_only_uses_imdsv2_and_never_creates_bucket(cartridge):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GCS_ACCESS_KEY_ID",
            "GCS_SECRET_ACCESS_KEY",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
        }
    }
    env.update(
        {
            "PYTHONPATH": str(ROOT / "cartridges" / cartridge),
            "LAKEHOUSE_PROVIDER": "s3",
            "LAKEHOUSE_ENDPOINT": "s3.us-east-1.amazonaws.com",
            "LAKEHOUSE_BUCKET": "omega-role-bucket",
            "S3_BUCKET_NAME": "omega-role-bucket",
            "AWS_REGION": "us-east-1",
            "DATABASE_URL": "postgresql://example.invalid/omega",
            "PG_USER": "test-user",
            "PG_PASSWORD": "test-password",
            "FIELD_ENCRYPTION_KEY": "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=",
        }
    )
    script = """
from minio.credentials.providers import IamAwsProvider
from app.core import minio_client
from app.core.config import settings

captured = {}
class FakeMinio:
    def __init__(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

minio_client.Minio = FakeMinio
minio_client.get_minio_client()
storage = settings.resolved_minio
assert storage["provider"] == "s3"
assert storage["access_key"] == ""
assert storage["secret_key"] == ""
assert storage["bucket"] == "omega-role-bucket"
assert captured["kwargs"]["endpoint"] == "s3.us-east-1.amazonaws.com"
assert isinstance(captured["kwargs"]["credentials"], IamAwsProvider)
assert "access_key" not in captured["kwargs"]
assert "secret_key" not in captured["kwargs"]

class CloudClient:
    def bucket_exists(self, _bucket):
        raise AssertionError("native S3 bucket must not be probed for creation")
    def make_bucket(self, _bucket):
        raise AssertionError("native S3 bucket must never be created")

minio_client.get_minio_client = lambda: CloudClient()
minio_client.ensure_bucket_exists(settings.minio_bucket)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "cartridge",
    ("hubspot", "replicon", "salesforce", "sap_hcm", "sap_s4hana"),
)
def test_cartridge_s3_static_session_uses_only_native_aws_pair(cartridge):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "cartridges" / cartridge),
        "LAKEHOUSE_PROVIDER": "s3",
        "LAKEHOUSE_ENDPOINT": "s3.us-east-1.amazonaws.com",
        "S3_BUCKET_NAME": "omega-static-bucket",
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "aws-access",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "AWS_SESSION_TOKEN": "aws-session",
        "MINIO_ACCESS_KEY": "stale-minio-access",
        "MINIO_SECRET_KEY": "stale-minio-secret",
        "GCS_ACCESS_KEY_ID": "stale-gcs-access",
        "GCS_SECRET_ACCESS_KEY": "stale-gcs-secret",
        "DATABASE_URL": "postgresql://example.invalid/omega",
        "PG_USER": "test-user",
        "PG_PASSWORD": "test-password",
        "FIELD_ENCRYPTION_KEY": "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=",
    }
    script = """
from app.core import minio_client
from app.core.config import settings

captured = {}
class FakeMinio:
    def __init__(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

minio_client.Minio = FakeMinio
minio_client.get_minio_client()
storage = settings.resolved_minio
assert storage["provider"] == "s3"
assert storage["access_key"] == "aws-access"
assert storage["secret_key"] == "aws-secret"
assert storage["session_token"] == "aws-session"
assert "stale-minio" not in repr(storage)
assert "stale-gcs" not in repr(storage)
assert captured["kwargs"]["access_key"] == "aws-access"
assert captured["kwargs"]["secret_key"] == "aws-secret"
assert captured["kwargs"]["session_token"] == "aws-session"
assert "credentials" not in captured["kwargs"]
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "cartridge",
    ("hubspot", "replicon", "salesforce", "sap_hcm", "sap_s4hana"),
)
def test_cartridge_duckdb_s3_role_loads_preinstalled_aws_extension(cartridge):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GCS_ACCESS_KEY_ID",
            "GCS_SECRET_ACCESS_KEY",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
        }
    }
    env.update(
        {
            "PYTHONPATH": str(ROOT / "cartridges" / cartridge),
            "LAKEHOUSE_PROVIDER": "s3",
            "LAKEHOUSE_ENDPOINT": "s3.us-east-1.amazonaws.com",
            "S3_BUCKET_NAME": "omega-role-bucket",
            "AWS_REGION": "us-east-1",
            "DATABASE_URL": "postgresql://example.invalid/omega",
            "PG_USER": "test-user",
            "PG_PASSWORD": "test-password",
            "FIELD_ENCRYPTION_KEY": "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=",
        }
    )
    script = f"""
from app.services import duckdb_service as service

statements = []
class FakeConnection:
    def execute(self, statement, params=None):
        statements.append((statement, params))
        return self
    def close(self):
        return None

service.duckdb.connect = lambda *args, **kwargs: FakeConnection()
if {cartridge!r} == "replicon":
    service._get_duckdb_connection("SELECT * FROM read_parquet('s3://omega-role-bucket/x.parquet')")
else:
    service._get_duckdb_connection()

sql = "\\n".join(statement for statement, _params in statements)
assert "LOAD aws;" in sql
assert "PROVIDER credential_chain" in sql
assert "SET s3_access_key_id" not in sql
assert "SET s3_secret_access_key" not in sql
assert "s3_url_style='vhost'" in sql
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_all_role_aware_cartridge_images_preinstall_aws_extension():
    for cartridge in (
        "hubspot",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "sap_b1",
    ):
        dockerfile = (ROOT / "cartridges" / cartridge / "Dockerfile").read_text(
            encoding="utf-8"
        )
        assert "INSTALL httpfs;" in dockerfile
        assert "INSTALL aws;" in dockerfile
