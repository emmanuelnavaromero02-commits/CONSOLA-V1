from __future__ import annotations

from types import SimpleNamespace

from app.services import cartridge_service


class _Client:
    def __init__(self) -> None:
        self.bucket_checks = 0
        self.bucket_creates = 0

    def bucket_exists(self, _bucket: str) -> bool:
        self.bucket_checks += 1
        return False

    def make_bucket(self, _bucket: str) -> None:
        self.bucket_creates += 1


def _storage(provider: str):
    return SimpleNamespace(
        provider=provider,
        bucket="omega-bucket",
        validate=lambda: None,
    )


def test_cartridge_service_never_probes_or_creates_gcs_bucket(monkeypatch):
    client = _Client()
    monkeypatch.setattr(
        cartridge_service,
        "resolve_storage_config",
        lambda: _storage("gcs"),
    )

    assert cartridge_service._ensure_bucket(client) == "omega-bucket"

    assert client.bucket_checks == 0
    assert client.bucket_creates == 0
    assert cartridge_service._lakehouse_bucket() == "omega-bucket"


def test_cartridge_service_can_create_only_local_minio_bucket(monkeypatch):
    client = _Client()
    monkeypatch.setattr(
        cartridge_service,
        "resolve_storage_config",
        lambda: _storage("minio"),
    )

    assert cartridge_service._ensure_bucket(client) == "omega-bucket"

    assert client.bucket_checks == 1
    assert client.bucket_creates == 1
