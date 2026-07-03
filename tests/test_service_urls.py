from __future__ import annotations

from app.services import service_urls


def test_public_url_omits_localhost_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CONSOLE_URL", "http://localhost:8000")

    assert service_urls.public_url("CONSOLE_URL") == ""


def test_public_url_uses_development_default_outside_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CONSOLE_URL", raising=False)

    assert (
        service_urls.public_url(
            "CONSOLE_URL",
            development_default="http://localhost:8000/",
        )
        == "http://localhost:8000"
    )


def test_service_url_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("VAULT_URL", "https://vault.example.com/")

    assert (
        service_urls.service_url(
            "VAULT_URL",
            "http://vault:8300",
            "http://127.0.0.1:8300",
        )
        == "https://vault.example.com"
    )


def test_service_url_uses_container_default_when_in_kubernetes(monkeypatch):
    monkeypatch.delenv("VAULT_URL", raising=False)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")

    assert (
        service_urls.service_url(
            "VAULT_URL",
            "http://vault:8300",
            "http://127.0.0.1:8300",
        )
        == "http://vault:8300"
    )

