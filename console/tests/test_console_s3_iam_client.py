from __future__ import annotations

import pytest

from app.services import s3_client


_S3_ENV = (
    "LAKEHOUSE_PROVIDER",
    "LAKEHOUSE_ENDPOINT",
    "LAKEHOUSE_BUCKET",
    "GCS_BUCKET",
    "GCS_ACCESS_KEY_ID",
    "GCS_SECRET_ACCESS_KEY",
    "S3_BUCKET_NAME",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_SECURE",
    "S3_ENDPOINT_URL",
    "AWS_S3_ENDPOINT_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
)


@pytest.fixture(autouse=True)
def _clean_s3_env(monkeypatch):
    for name in _S3_ENV:
        monkeypatch.delenv(name, raising=False)


def test_minio_client_aws_endpoint_without_static_keys_uses_imds_provider(monkeypatch):
    captured = {}

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.setattr(s3_client, "Minio", FakeMinio)

    s3_client.get_minio_client()

    assert captured["kwargs"]["endpoint"] == "s3.us-east-1.amazonaws.com"
    assert captured["kwargs"]["secure"] is True
    assert isinstance(captured["kwargs"]["credentials"], s3_client.Ec2ImdsV2Provider)
    assert "access_key" not in captured["kwargs"]
    assert "secret_key" not in captured["kwargs"]


def test_minio_client_local_endpoint_keeps_static_keys(monkeypatch):
    captured = {}

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setattr(s3_client, "Minio", FakeMinio)

    s3_client.get_minio_client()

    assert captured["args"] == ("minio:9000",)
    assert captured["kwargs"]["access_key"] == "minio"
    assert captured["kwargs"]["secret_key"] == "minioadmin"
    assert captured["kwargs"]["secure"] is False


def test_minio_client_aws_endpoint_with_partial_static_keys_fails_closed(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "partial-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "")

    with pytest.raises(RuntimeError, match="^storage_credentials_missing$"):
        s3_client.get_minio_client()


def test_boto3_explorer_client_aws_endpoint_without_static_keys_uses_iam_chain(monkeypatch):
    captured = {}

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return object()

    import boto3

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "s3")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET_NAME", "omega-s3")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(boto3, "client", fake_client)

    s3_client.get_boto3_s3_client()

    assert captured["service_name"] == "s3"
    assert captured["kwargs"]["endpoint_url"] == "https://s3.us-east-1.amazonaws.com"
    assert captured["kwargs"]["region_name"] == "us-east-1"
    assert "aws_access_key_id" not in captured["kwargs"]
    assert "aws_secret_access_key" not in captured["kwargs"]


def test_boto3_explorer_client_local_minio_keeps_static_keys(monkeypatch):
    captured = {}

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return object()

    import boto3

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setattr(boto3, "client", fake_client)

    s3_client.get_boto3_s3_client()

    assert captured["kwargs"]["endpoint_url"] == "http://minio:9000"
    assert captured["kwargs"]["aws_access_key_id"] == "minio"
    assert captured["kwargs"]["aws_secret_access_key"] == "minioadmin"


def test_gcs_client_uses_only_exact_gcs_pair_and_auto_region(monkeypatch):
    captured = {}

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "https://storage.googleapis.com")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "stale-minio-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "stale-minio-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-ses-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-ses-secret")
    monkeypatch.setattr(s3_client, "Minio", FakeMinio)

    s3_client.get_minio_client()

    assert captured["args"] == ("storage.googleapis.com",)
    assert captured["kwargs"] == {
        "access_key": "gcs-access",
        "secret_key": "gcs-secret",
        "secure": True,
        "region": "auto",
    }


def test_gcs_boto_client_uses_gcs_pair_not_aws_pair(monkeypatch):
    captured = {}

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return object()

    import boto3

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-ses-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-ses-secret")
    monkeypatch.setattr(boto3, "client", fake_client)

    s3_client.get_boto3_s3_client()

    assert captured["kwargs"]["endpoint_url"] == "https://storage.googleapis.com"
    assert captured["kwargs"]["region_name"] == "auto"
    assert captured["kwargs"]["aws_access_key_id"] == "gcs-access"
    assert captured["kwargs"]["aws_secret_access_key"] == "gcs-secret"


def test_gcs_partial_pair_fails_closed_without_fallback(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.delenv("GCS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("MINIO_SECRET_KEY", "must-not-fallback")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-fallback")

    with pytest.raises(RuntimeError, match="^storage_credentials_missing$"):
        s3_client.get_minio_client()


def test_imds_provider_rejects_non_link_local_endpoint_before_network():
    provider = s3_client.Ec2ImdsV2Provider(base_url="http://example.com")

    with pytest.raises(ValueError, match="link-local metadata endpoint"):
        provider._read("/latest/meta-data/iam/security-credentials/")


def test_imds_provider_rejects_non_latest_paths_before_network():
    provider = s3_client.Ec2ImdsV2Provider()

    with pytest.raises(ValueError, match="/latest metadata paths"):
        provider._read("/not-latest/meta-data")
