from __future__ import annotations

import pytest

from app.services import s3_client


_S3_ENV = (
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

    monkeypatch.setenv("MINIO_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("MINIO_SECURE", "true")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "")
    monkeypatch.setenv("MINIO_SECRET_KEY", "")
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


def test_minio_client_aws_endpoint_with_partial_static_keys_still_uses_imds(monkeypatch):
    captured = {}

    class FakeMinio:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setenv("MINIO_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "partial-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(s3_client, "Minio", FakeMinio)

    s3_client.get_minio_client()

    assert isinstance(captured["kwargs"]["credentials"], s3_client.Ec2ImdsV2Provider)
    assert "access_key" not in captured["kwargs"]
    assert "secret_key" not in captured["kwargs"]


def test_boto3_explorer_client_aws_endpoint_without_static_keys_uses_iam_chain(monkeypatch):
    captured = {}

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return object()

    import boto3

    monkeypatch.setenv("MINIO_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("MINIO_SECURE", "true")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "")
    monkeypatch.setenv("MINIO_SECRET_KEY", "")
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


def test_imds_provider_rejects_non_link_local_endpoint_before_network():
    provider = s3_client.Ec2ImdsV2Provider(base_url="http://example.com")

    with pytest.raises(ValueError, match="link-local metadata endpoint"):
        provider._read("/latest/meta-data/iam/security-credentials/")


def test_imds_provider_rejects_non_latest_paths_before_network():
    provider = s3_client.Ec2ImdsV2Provider()

    with pytest.raises(ValueError, match="/latest metadata paths"):
        provider._read("/not-latest/meta-data")
