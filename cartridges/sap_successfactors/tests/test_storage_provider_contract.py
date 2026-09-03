from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE="
)

from app.core import minio_client
from app.services import duckdb_service, extraction_service, kb_service


@pytest.fixture(autouse=True)
def _reset_storage_settings(monkeypatch):
    values = {
        "lakehouse_provider": "",
        "lakehouse_endpoint": "",
        "lakehouse_bucket": "",
        "gcs_bucket": "",
        "gcs_access_key_id": "",
        "gcs_secret_access_key": "",
        "s3_bucket_name": "",
        "aws_access_key_id": "",
        "aws_secret_access_key": "",
        "aws_session_token": "",
        "aws_region": "",
        "aws_default_region": "",
        "minio_endpoint": "minio:9000",
        "minio_bucket": "lakehouse",
        "minio_access_key": "local-access",
        "minio_secret_key": "local-secret",
        "minio_secure": False,
    }
    for name, value in values.items():
        monkeypatch.setattr(minio_client.settings, name, value)


def test_gcs_uses_only_complete_gcs_pair_and_region_auto(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        minio_client.settings, "lakehouse_endpoint", "storage.googleapis.com"
    )
    monkeypatch.setattr(minio_client.settings, "gcs_bucket", "omega-gcs")
    monkeypatch.setattr(minio_client.settings, "gcs_access_key_id", "gcs-access")
    monkeypatch.setattr(minio_client.settings, "gcs_secret_access_key", "gcs-secret")
    # Complete local credentials may coexist in a developer shell, but are
    # never selected for GCS.
    monkeypatch.setattr(minio_client.settings, "minio_access_key", "wrong-access")
    monkeypatch.setattr(minio_client.settings, "minio_secret_key", "wrong-secret")
    monkeypatch.setattr(minio_client, "Minio", lambda **kwargs: captured.update(kwargs))

    config = minio_client.resolve_storage_config()
    minio_client.get_minio_client(config=config)

    assert config.provider == "gcs"
    assert captured == {
        "endpoint": "storage.googleapis.com",
        "secure": True,
        "region": "auto",
        "access_key": "gcs-access",
        "secret_key": "gcs-secret",
    }


def test_gcs_incomplete_pair_cannot_be_completed_by_minio_secret(monkeypatch):
    monkeypatch.setattr(minio_client.settings, "lakehouse_provider", "gcs")
    monkeypatch.setattr(
        minio_client.settings, "lakehouse_endpoint", "storage.googleapis.com"
    )
    monkeypatch.setattr(minio_client.settings, "gcs_bucket", "omega-gcs")
    monkeypatch.setattr(minio_client.settings, "gcs_access_key_id", "gcs-access")
    monkeypatch.setattr(minio_client.settings, "gcs_secret_access_key", "")
    monkeypatch.setattr(minio_client.settings, "minio_secret_key", "must-not-be-used")

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        minio_client.resolve_storage_config()

    assert str(exc.value) == "storage_credentials_missing"


def test_gcs_never_borrows_local_minio_endpoint_or_bucket(monkeypatch):
    monkeypatch.setattr(minio_client.settings, "lakehouse_provider", "gcs")
    monkeypatch.setattr(minio_client.settings, "lakehouse_endpoint", "")
    monkeypatch.setattr(minio_client.settings, "lakehouse_bucket", "")
    monkeypatch.setattr(minio_client.settings, "gcs_bucket", "")
    monkeypatch.setattr(minio_client.settings, "gcs_access_key_id", "gcs-access")
    monkeypatch.setattr(minio_client.settings, "gcs_secret_access_key", "gcs-secret")
    monkeypatch.setattr(minio_client.settings, "minio_endpoint", "stale-minio:9000")
    monkeypatch.setattr(minio_client.settings, "minio_bucket", "stale-local-bucket")

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        minio_client.resolve_storage_config()

    assert exc.value.code == "storage_bucket_missing"
    monkeypatch.setattr(minio_client.settings, "gcs_bucket", "omega-gcs")
    config = minio_client.resolve_storage_config()
    assert config.endpoint == "storage.googleapis.com"
    assert config.bucket == "omega-gcs"


def test_aws_without_native_static_pair_keeps_imdsv2(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        minio_client.settings, "lakehouse_endpoint", "s3.us-east-1.amazonaws.com"
    )
    monkeypatch.setattr(minio_client.settings, "s3_bucket_name", "omega-s3")
    monkeypatch.setattr(minio_client.settings, "aws_region", "us-east-1")
    monkeypatch.setattr(minio_client, "Minio", lambda **kwargs: captured.update(kwargs))

    config = minio_client.resolve_storage_config()
    minio_client.get_minio_client(config=config)

    assert config.provider == "s3"
    assert isinstance(captured["credentials"], minio_client.Ec2ImdsV2Provider)
    assert "access_key" not in captured
    assert "secret_key" not in captured


def test_local_minio_requires_its_own_complete_pair(monkeypatch):
    monkeypatch.setattr(minio_client.settings, "minio_secret_key", "")
    monkeypatch.setattr(minio_client.settings, "gcs_secret_access_key", "gcs-secret")

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        minio_client.resolve_storage_config()

    assert exc.value.code == "storage_credentials_missing"


@pytest.mark.parametrize(
    ("provider", "expected_create_calls"),
    [("gcs", 0), ("s3", 0), ("minio", 1)],
)
def test_only_local_minio_may_create_a_missing_bucket(
    monkeypatch, provider, expected_create_calls
):
    calls = {"exists": 0, "list": 0, "create": 0}

    class FakeClient:
        def list_objects(self, _bucket, **_kwargs):
            calls["list"] += 1
            return iter(())

        def bucket_exists(self, _bucket):
            calls["exists"] += 1
            return calls["create"] > 0

        def make_bucket(self, _bucket):
            calls["create"] += 1

    config = minio_client.StorageRuntimeConfig(
        provider=provider,
        endpoint="storage.example",
        bucket="omega-lakehouse",
        secure=True,
        region="auto" if provider == "gcs" else "us-east-1",
        access_key="access",
        secret_key="secret",
    )
    monkeypatch.setattr(minio_client, "resolve_storage_config", lambda: config)
    monkeypatch.setattr(
        minio_client, "get_minio_client", lambda **_kwargs: FakeClient()
    )

    status = minio_client.check_storage_access()

    assert calls["create"] == expected_create_calls
    if provider == "minio":
        assert status == {"component": "minio", "configured": True, "missing": []}
    else:
        assert status == {"component": "minio", "configured": True, "missing": []}
        assert calls["list"] == 1
        assert calls["exists"] == 0


@pytest.mark.parametrize(
    ("provider_error", "safe_code"),
    [
        (SimpleNamespace(code="SignatureDoesNotMatch"), "storage_signature_invalid"),
        (SimpleNamespace(code="AccessDenied"), "storage_access_denied"),
        (SimpleNamespace(code="NoSuchBucket"), "storage_bucket_missing"),
    ],
)
def test_authenticated_preflight_exposes_only_safe_codes(
    monkeypatch, provider_error, safe_code
):
    leaked_secret = "do-not-leak-this-secret"

    class ProviderFailure(RuntimeError):
        def __init__(self):
            self.code = provider_error.code
            super().__init__(f"provider detail {leaked_secret}")

    class FakeClient:
        def list_objects(self, _bucket, **_kwargs):
            raise ProviderFailure()

    config = minio_client.StorageRuntimeConfig(
        provider="gcs",
        endpoint="storage.googleapis.com",
        bucket="omega-gcs",
        secure=True,
        region="auto",
        access_key="access",
        secret_key="secret",
    )
    monkeypatch.setattr(minio_client, "resolve_storage_config", lambda: config)
    monkeypatch.setattr(
        minio_client, "get_minio_client", lambda **_kwargs: FakeClient()
    )

    status = minio_client.check_storage_access()

    assert status["code"] == safe_code
    assert leaked_secret not in repr(status)


def test_storage_preflight_runs_before_sap_client_is_created(monkeypatch):
    sap_created = False

    class FakeSapClient:
        def __init__(self, **_kwargs):
            nonlocal sap_created
            sap_created = True

    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapClient)
    monkeypatch.setattr(
        extraction_service,
        "require_storage_access",
        lambda: (_ for _ in ()).throw(
            minio_client.StoragePreflightError("storage_signature_invalid")
        ),
    )

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        extraction_service.run_entity({"entity": "FOCompany", "conn_id": "sf-live"})

    assert str(exc.value) == "storage_signature_invalid"
    assert sap_created is False


def test_storage_preflight_runs_before_metadata_guard_contacts_sap(monkeypatch):
    from app.services import catalog_service

    metadata_called = False

    def unexpected_metadata(*_args, **_kwargs):
        nonlocal metadata_called
        metadata_called = True
        raise AssertionError("metadata must not run without storage")

    monkeypatch.setattr(
        catalog_service,
        "prepare_entity_config_for_metadata",
        unexpected_metadata,
    )
    monkeypatch.setattr(
        extraction_service,
        "require_storage_access",
        lambda: (_ for _ in ()).throw(
            minio_client.StoragePreflightError("storage_signature_invalid")
        ),
    )

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        extraction_service.run_entity_with_metadata_guard(
            {"entity": "FOCompany", "conn_id": "sf-live"}
        )

    assert exc.value.code == "storage_signature_invalid"
    assert metadata_called is False


def test_storage_canary_creates_stats_attests_and_deletes(monkeypatch):
    calls: list[str] = []
    captured_checksum = ""
    captured_payload = b""

    class FakeClient:
        def put_object(self, bucket, object_name, stream, length, **kwargs):
            nonlocal captured_checksum, captured_payload
            payload = stream.read()
            assert bucket == "omega-gcs"
            assert len(payload) == length
            captured_checksum = kwargs["metadata"]["omega-sha256"]
            captured_payload = payload
            calls.append("create")

        def stat_object(self, bucket, object_name):
            calls.append("stat")
            return SimpleNamespace(
                size=len(b"omega-successfactors-storage-canary:") + 32,
                metadata={"X-Goog-Meta-Omega-Sha256": captured_checksum},
            )

        def get_object(self, bucket, object_name):
            calls.append("read")
            return SimpleNamespace(
                read=lambda: captured_payload,
                close=lambda: None,
                release_conn=lambda: None,
            )

        def remove_object(self, bucket, object_name):
            calls.append("delete")

    config = minio_client.StorageRuntimeConfig(
        provider="gcs",
        endpoint="storage.googleapis.com",
        bucket="omega-gcs",
        secure=True,
        region="auto",
        access_key="access",
        secret_key="secret",
    )
    monkeypatch.setattr(minio_client, "require_storage_access", lambda: config)
    monkeypatch.setattr(
        minio_client, "get_minio_client", lambda **_kwargs: FakeClient()
    )

    result = minio_client.run_storage_canary()

    assert calls == ["create", "stat", "read", "delete"]
    assert result == {
        "status": "ok",
        "checksum_verified": True,
        "deleted": True,
        "provider": "gcs",
    }


def test_storage_canary_deletes_object_after_attestation_failure(monkeypatch):
    calls: list[str] = []

    class FakeClient:
        def put_object(self, *_args, **_kwargs):
            calls.append("create")

        def stat_object(self, *_args, **_kwargs):
            calls.append("stat")
            return SimpleNamespace(size=1, metadata={})

        def remove_object(self, *_args, **_kwargs):
            calls.append("delete")

    config = minio_client.StorageRuntimeConfig(
        provider="gcs",
        endpoint="storage.googleapis.com",
        bucket="omega-gcs",
        secure=True,
        region="auto",
        access_key="access",
        secret_key="secret",
    )
    monkeypatch.setattr(minio_client, "require_storage_access", lambda: config)
    monkeypatch.setattr(
        minio_client, "get_minio_client", lambda **_kwargs: FakeClient()
    )

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        minio_client.run_storage_canary()

    assert exc.value.code == "storage_access_denied"
    assert calls == ["create", "stat", "delete"]


def test_storage_canary_cleans_up_after_ambiguous_put_failure(monkeypatch):
    calls: list[str] = []

    class FakeClient:
        def put_object(self, *_args, **_kwargs):
            calls.append("create")
            raise TimeoutError("SECRET response lost after object persisted")

        def remove_object(self, *_args, **_kwargs):
            calls.append("delete")

    config = minio_client.StorageRuntimeConfig(
        provider="gcs",
        endpoint="storage.googleapis.com",
        bucket="omega-gcs",
        secure=True,
        region="auto",
        access_key="access",
        secret_key="secret",
    )
    monkeypatch.setattr(minio_client, "require_storage_access", lambda: config)
    monkeypatch.setattr(
        minio_client, "get_minio_client", lambda **_kwargs: FakeClient()
    )

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        minio_client.run_storage_canary()

    assert exc.value.code == "storage_access_denied"
    assert "SECRET" not in str(exc.value)
    assert calls == ["create", "delete"]


def test_knowledge_bits_use_the_active_provider_bucket(monkeypatch):
    monkeypatch.setattr(
        kb_service,
        "require_tenant_workspace_scope",
        lambda context: context,
    )
    monkeypatch.setattr(
        kb_service,
        "active_storage_bucket",
        lambda: "omega-production-gcs",
    )
    context = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222",
    }

    resolved = kb_service._scope_kb_sql(
        "SELECT * FROM read_parquet('s3://{bucket}/gold/"
        "sap_successfactors/talent_9box/*.parquet')",
        context,
    )

    assert "s3://omega-production-gcs/gold/sap_successfactors/" in resolved
    assert all(
        prefix.startswith("s3://omega-production-gcs/")
        for prefix in kb_service._kb_allowed_prefixes()
    )


def test_successfactors_duckdb_uses_resolved_gcs_pair_and_auto_region(monkeypatch):
    statements: list[tuple[str, object | None]] = []

    class FakeConnection:
        def execute(self, sql, params=None):
            statements.append((sql, params))
            return self

        def close(self):
            return None

    config = minio_client.StorageRuntimeConfig(
        provider="gcs",
        endpoint="storage.googleapis.com",
        bucket="omega-gcs",
        secure=True,
        region="auto",
        access_key="gcs-access",
        secret_key="gcs-secret",
    )
    monkeypatch.setattr(duckdb_service, "resolve_storage_config", lambda: config)
    monkeypatch.setattr(duckdb_service.duckdb, "connect", lambda: FakeConnection())

    duckdb_service._get_duckdb_connection()

    assert ("SET s3_endpoint=?;", ["storage.googleapis.com"]) in statements
    assert ("SET s3_region=?;", ["auto"]) in statements
    assert ("SET s3_access_key_id=?;", ["gcs-access"]) in statements
    assert ("SET s3_secret_access_key=?;", ["gcs-secret"]) in statements


def test_successfactors_duckdb_does_not_expose_secret_on_configuration_error(
    monkeypatch,
):
    secret = "gcs-secret-must-not-leak"

    class FakeConnection:
        def execute(self, sql, params=None):
            if sql == "SET s3_secret_access_key=?;":
                raise RuntimeError(f"invalid setting {params[0]}")
            return self

        def close(self):
            return None

    config = minio_client.StorageRuntimeConfig(
        provider="gcs",
        endpoint="storage.googleapis.com",
        bucket="omega-gcs",
        secure=True,
        region="auto",
        access_key="gcs-access",
        secret_key=secret,
    )
    monkeypatch.setattr(duckdb_service, "resolve_storage_config", lambda: config)
    monkeypatch.setattr(duckdb_service.duckdb, "connect", lambda: FakeConnection())

    with pytest.raises(minio_client.StoragePreflightError) as exc:
        duckdb_service._get_duckdb_connection()

    assert str(exc.value) == "storage_access_denied"
    assert secret not in str(exc.value)


def test_successfactors_duckdb_aws_role_uses_credential_chain(monkeypatch):
    statements: list[tuple[str, object | None]] = []

    class FakeConnection:
        def execute(self, sql, params=None):
            statements.append((sql, params))
            return self

        def close(self):
            return None

    config = minio_client.StorageRuntimeConfig(
        provider="s3",
        endpoint="s3.amazonaws.com",
        bucket="omega-aws",
        secure=True,
        region="us-east-1",
        access_key=None,
        secret_key=None,
    )
    monkeypatch.setattr(duckdb_service, "resolve_storage_config", lambda: config)
    monkeypatch.setattr(duckdb_service.duckdb, "connect", lambda: FakeConnection())

    duckdb_service._get_duckdb_connection()

    assert ("SET s3_region=?;", ["us-east-1"]) in statements
    assert any(sql == "LOAD aws;" for sql, _ in statements)
    assert any("PROVIDER credential_chain" in sql for sql, _ in statements)
    assert not any(
        sql in {"SET s3_access_key_id=?;", "SET s3_secret_access_key=?;"}
        for sql, _ in statements
    )
