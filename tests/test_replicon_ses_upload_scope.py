from __future__ import annotations

import importlib.util
import sys
import types
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SES_DAG = ROOT / "cartridges" / "replicon" / "dags" / "replicon_ses_inbox_import.py"
AIRFLOW_MIRROR = ROOT / "airflow" / "dags" / "replicon_ses_inbox_import.py"
OUTLOOK_DAG = ROOT / "airflow" / "dags" / "replicon_outlook_audit_report_import.py"


def _load_ses_dag(monkeypatch: pytest.MonkeyPatch):
    class FakeDAG:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class FakePythonOperator:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __rshift__(self, other):
            return other

    class FakeVariable:
        values: dict[str, str] = {}

        @classmethod
        def get(cls, name: str, default_var: str = "") -> str:
            return cls.values.get(name, default_var)

    airflow = types.ModuleType("airflow")
    airflow.DAG = FakeDAG
    airflow_models = types.ModuleType("airflow.models")
    airflow_models.Variable = FakeVariable
    airflow_python = types.ModuleType("airflow.operators.python")
    airflow_python.PythonOperator = FakePythonOperator
    airflow_operators = types.ModuleType("airflow.operators")
    airflow_operators.python = airflow_python

    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.models", airflow_models)
    monkeypatch.setitem(sys.modules, "airflow.operators", airflow_operators)
    monkeypatch.setitem(sys.modules, "airflow.operators.python", airflow_python)

    module_name = "replicon_ses_inbox_import_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SES_DAG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_outlook_dag(monkeypatch: pytest.MonkeyPatch):
    class FakeDAG:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class FakePythonOperator:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __rshift__(self, other):
            return other

    class FakeVariable:
        values: dict[str, str] = {}

        @classmethod
        def get(cls, name: str, default_var: str = "") -> str:
            return cls.values.get(name, default_var)

    class FakeCopySource:
        def __init__(self, bucket: str, key: str):
            self.bucket = bucket
            self.key = key

    airflow = types.ModuleType("airflow")
    airflow.DAG = FakeDAG
    airflow_models = types.ModuleType("airflow.models")
    airflow_models.Variable = FakeVariable
    airflow_python = types.ModuleType("airflow.operators.python")
    airflow_python.PythonOperator = FakePythonOperator
    airflow_operators = types.ModuleType("airflow.operators")
    airflow_operators.python = airflow_python
    minio = types.ModuleType("minio")
    minio.Minio = object
    minio_common = types.ModuleType("minio.commonconfig")
    minio_common.CopySource = FakeCopySource

    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.models", airflow_models)
    monkeypatch.setitem(sys.modules, "airflow.operators", airflow_operators)
    monkeypatch.setitem(sys.modules, "airflow.operators.python", airflow_python)
    monkeypatch.setitem(sys.modules, "minio", minio)
    monkeypatch.setitem(sys.modules, "minio.commonconfig", minio_common)

    module_name = "replicon_outlook_audit_report_import_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, OUTLOOK_DAG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_replicon_ses_upload_prefix_is_scoped_before_in(monkeypatch):
    mod = _load_ses_dag(monkeypatch)

    assert (
        mod._scoped_uploads_prefix("tenant-a", "workspace-a")
        == "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/"
    )

    mod.UPLOADS_PREFIX = (
        "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/"
    )
    assert (
        mod._scoped_uploads_prefix("tenant-a", "workspace-a")
        == "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/"
    )
    with pytest.raises(ValueError, match="scope does not match"):
        mod._scoped_uploads_prefix("tenant-b", "workspace-a")


def test_replicon_ses_requires_scope_in_production(monkeypatch):
    mod = _load_ses_dag(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(ValueError, match="tenant_id and workspace_id are required"):
        mod._scope_values({})

    ctx = {
        "dag_run": SimpleNamespace(
            conf={
                "security_context": {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                }
            }
        )
    }
    assert mod._scope_values(ctx) == ("tenant-a", "workspace-a")


def test_replicon_ses_extract_lands_attachment_under_scoped_upload_prefix(monkeypatch):
    mod = _load_ses_dag(monkeypatch)

    msg = EmailMessage()
    msg["From"] = "ops@example.com"
    msg["Subject"] = "Replicon report"
    msg.set_content("attached")
    msg.add_attachment(
        b"hours,total\n1,8\n",
        maintype="text",
        subtype="csv",
        filename="../hours.csv",
    )

    class Body:
        def read(self):
            return msg.as_bytes()

    class FakeS3:
        def __init__(self):
            self.puts: list[dict] = []

        def get_object(self, **kwargs):
            return {"Body": Body()}

        def put_object(self, **kwargs):
            self.puts.append(kwargs)

    fake_s3 = FakeS3()
    monkeypatch.setattr(mod, "_inbox_s3", lambda: fake_s3)
    monkeypatch.setattr(mod, "_lakehouse_s3", lambda: fake_s3)

    class FakeTI:
        def xcom_pull(self, task_ids: str, key: str):
            return {
                ("list_inbox", "keys"): ["inbound/message-1"],
                (
                    "list_inbox",
                    "upload_prefix",
                ): "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/",
            }.get((task_ids, key))

        def xcom_push(self, **kwargs):
            return None

    result = mod.extract_attachments(task_instance=FakeTI())

    assert result["landed"] == 1
    assert fake_s3.puts[0]["Key"] == (
        "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/hours.csv"
    )


def test_replicon_ses_separates_aws_inbox_from_gcs_lakehouse(monkeypatch):
    monkeypatch.setenv("REPLICON_SES_INBOX_ENABLED", "true")
    mod = _load_ses_dag(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://storage.googleapis.com")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("SES_INBOX_AWS_ACCESS_KEY_ID", "ses-access")
    monkeypatch.setenv("SES_INBOX_AWS_SECRET_ACCESS_KEY", "ses-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-lakehouse-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-lakehouse-secret")
    monkeypatch.setenv("AWS_REGION", "auto")
    monkeypatch.delenv("SES_INBOX_S3_ENDPOINT_URL", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(
        mod.boto3,
        "client",
        lambda service, **kwargs: calls.append({"service": service, **kwargs})
        or object(),
    )

    mod._inbox_s3()
    mod._lakehouse_s3()

    assert calls[0] == {
        "service": "s3",
        "region_name": "us-east-1",
        "aws_access_key_id": "ses-access",
        "aws_secret_access_key": "ses-secret",
    }
    assert calls[1] == {
        "service": "s3",
        "region_name": "auto",
        "endpoint_url": "https://storage.googleapis.com",
        "aws_access_key_id": "gcs-access",
        "aws_secret_access_key": "gcs-secret",
    }


def test_replicon_ses_is_unscheduled_until_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("REPLICON_SES_INBOX_ENABLED", raising=False)
    mod = _load_ses_dag(monkeypatch)

    assert mod.dag.kwargs["schedule_interval"] is None
    with pytest.raises(RuntimeError, match="^ses_inbox_disabled$"):
        mod._inbox_s3()


def test_replicon_ses_gcp_requires_dedicated_aws_pair(monkeypatch):
    monkeypatch.setenv("REPLICON_SES_INBOX_ENABLED", "true")
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "must-not-be-borrowed")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-be-borrowed")
    monkeypatch.delenv("SES_INBOX_AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("SES_INBOX_AWS_SECRET_ACCESS_KEY", raising=False)
    mod = _load_ses_dag(monkeypatch)
    monkeypatch.setattr(
        mod.boto3,
        "client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("boto must not run without dedicated SES credentials")
        ),
    )

    with pytest.raises(RuntimeError, match="^ses_inbox_credentials_missing$"):
        mod._inbox_s3()


def test_replicon_ses_record_run_persists_scope_and_scoped_storage(monkeypatch):
    mod = _load_ses_dag(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr(
        mod, "_pipeline_run_save", lambda **kwargs: calls.append(kwargs)
    )

    class FakeTI:
        def xcom_pull(self, task_ids: str, key: str):
            return {
                ("list_inbox", "keys"): ["inbound/message-1"],
                ("extract_attachments", "succeeded"): ["inbound/message-1"],
                ("extract_attachments", "failed"): [],
                ("extract_attachments", "landed_total"): 1,
                ("extract_attachments", "bytes_total"): 17,
                ("archive_processed", "archived"): 1,
                ("list_inbox", "started_at"): "2026-06-01T00:00:00+00:00",
                ("list_inbox", "run_id"): "run-1",
                ("list_inbox", "tenant_id"): "tenant-a",
                ("list_inbox", "workspace_id"): "workspace-a",
                (
                    "list_inbox",
                    "upload_prefix",
                ): "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/",
            }.get((task_ids, key))

    result = mod.record_run(task_instance=FakeTI(), run_id="run-1")

    assert result["status"] == "success"
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["workspace_id"] == "workspace-a"
    assert calls[0]["storage_uri"] == (
        f"s3://{mod.LAKE_BUCKET}/uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/"
    )


def test_replicon_ses_airflow_mirror_stays_identical():
    assert SES_DAG.read_text(encoding="utf-8") == AIRFLOW_MIRROR.read_text(
        encoding="utf-8"
    )


def test_replicon_outlook_paths_are_scoped(monkeypatch):
    mod = _load_outlook_dag(monkeypatch)

    assert (
        mod._scoped_path(
            "uploads/replicon/in",
            "tenant-a",
            "workspace-a",
            "audit.csv",
        )
        == "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/audit.csv"
    )
    assert (
        mod._scoped_path(
            "uploads/replicon/bak",
            "tenant-a",
            "workspace-a",
            "audit.csv",
        )
        == "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/bak/audit.csv"
    )
    with pytest.raises(ValueError, match="scope does not match"):
        mod._scoped_path(
            "uploads/replicon/in/tenant_id=tenant-b/workspace_id=workspace-a",
            "tenant-a",
            "workspace-a",
            "audit.csv",
        )


def test_replicon_outlook_requires_scope_in_production(monkeypatch):
    mod = _load_outlook_dag(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(ValueError, match="tenant_id and workspace_id are required"):
        mod._scope_values({})

    ctx = {
        "dag_run": SimpleNamespace(
            conf={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
            }
        )
    }
    assert mod._scope_values(ctx) == ("tenant-a", "workspace-a")


def test_replicon_outlook_never_probes_or_creates_gcs_bucket(monkeypatch):
    mod = _load_outlook_dag(monkeypatch)
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")

    class FakeClient:
        def bucket_exists(self, _bucket):
            raise AssertionError("GCS bucket existence must not be probed")

        def make_bucket(self, _bucket):
            raise AssertionError("GCS bucket must never be created")

    monkeypatch.setattr(mod, "_minio_client", lambda: FakeClient())

    assert mod._ensure_bucket() == "omega-gcs"


def test_replicon_outlook_never_probes_or_creates_s3_bucket(monkeypatch):
    mod = _load_outlook_dag(monkeypatch)
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    class FakeClient:
        def bucket_exists(self, _bucket):
            raise AssertionError("S3 bucket existence must not be probed")

        def make_bucket(self, _bucket):
            raise AssertionError("S3 bucket must never be created")

    monkeypatch.setattr(mod, "_minio_client", lambda: FakeClient())

    assert mod._ensure_bucket() == "omega-s3"


def test_replicon_outlook_upload_and_backup_use_scoped_paths(monkeypatch, tmp_path):
    mod = _load_outlook_dag(monkeypatch)
    csv_path = tmp_path / "audit.csv"
    csv_path.write_text("id,total\n1,2\n", encoding="utf-8")

    class FakeClient:
        def __init__(self):
            self.puts: list[str] = []
            self.copies: list[str] = []
            self.removes: list[str] = []

        def put_object(self, bucket, key, data, length):
            self.puts.append(key)

        def copy_object(self, bucket, key, source):
            self.copies.append(key)

        def remove_object(self, bucket, key):
            self.removes.append(key)

    fake_client = FakeClient()
    monkeypatch.setattr(mod, "_ensure_bucket", lambda: "lakehouse")
    monkeypatch.setattr(mod, "_minio_client", lambda: fake_client)

    class FakeTI:
        pushed: dict[str, str] = {}

        def xcom_pull(self, task_ids: str, key: str):
            return {
                ("fetch_outlook_attachment", "csv_path"): str(csv_path),
                ("fetch_outlook_attachment", "csv_filename"): "audit.csv",
                (
                    "upload_csv_to_minio",
                    "minio_csv_uri",
                ): "s3://lakehouse/uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/audit.csv",
                ("upload_csv_to_minio", "tenant_id"): "tenant-a",
                ("upload_csv_to_minio", "workspace_id"): "workspace-a",
            }.get((task_ids, key))

        def xcom_push(self, key: str, value: str):
            self.pushed[key] = value

    ti = FakeTI()
    ctx = {
        "task_instance": ti,
        "dag_run": SimpleNamespace(
            conf={"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
        ),
    }

    mod.upload_csv_to_minio(**ctx)
    mod.move_csv_to_backup(**ctx)

    assert fake_client.puts == [
        "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/in/audit.csv"
    ]
    assert fake_client.copies[0].startswith(
        "uploads/replicon/tenant_id=tenant-a/workspace_id=workspace-a/bak/"
    )
    assert fake_client.copies[0].endswith("_audit.csv")


def test_replicon_outlook_project_audit_raw_path_is_scoped():
    source = OUTLOOK_DAG.read_text(encoding="utf-8")

    assert 'df["tenant_id"] = tenant_id' in source
    assert 'df["workspace_id"] = workspace_id' in source
    assert 'f"raw/{CARTRIDGE_ID}/{ENTITY}/"' in source
    assert 'f"tenant_id={tenant_id}/workspace_id={workspace_id}/"' in source
    assert 'f"load_date={today}/"' in source
