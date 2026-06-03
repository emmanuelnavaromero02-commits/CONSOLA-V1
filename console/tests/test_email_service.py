from __future__ import annotations

import importlib

import pytest

from app.services import email_service as email_service_module


def _reload_email(monkeypatch, *, provider: str):
    monkeypatch.setenv("EMAIL_PROVIDER", provider)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "smtp-user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-pass")
    monkeypatch.setenv("SMTP_FROM", "no-reply@example.test")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    return importlib.reload(email_service_module)


def test_send_sync_uses_smtp_provider(monkeypatch):
    module = _reload_email(monkeypatch, provider="smtp")
    calls: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls["connect"] = (host, port, timeout)
            self.sent = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            calls["starttls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["message"] = msg

    monkeypatch.setattr(module.smtplib, "SMTP", FakeSMTP)

    module._send_sync("user@example.test", "Subject", "<p>Hello</p>")

    assert calls["connect"] == ("smtp.example.test", 587, 15)
    assert calls["starttls"] is True
    assert calls["login"] == ("smtp-user", "smtp-pass")
    assert calls["message"]["To"] == "user@example.test"
    assert calls["message"]["From"] == "no-reply@example.test"


def test_send_sync_uses_ses_provider(monkeypatch):
    module = _reload_email(monkeypatch, provider="ses")
    calls: dict[str, object] = {}

    class FakeSES:
        def send_raw_email(self, **kwargs):
            calls["send_raw_email"] = kwargs
            return {"MessageId": "unit-message"}

    def fake_client(service_name, region_name=None):
        calls["client"] = (service_name, region_name)
        return FakeSES()

    monkeypatch.setattr(module.boto3, "client", fake_client)

    module._send_sync("user@example.test", "Reset password", "<p>Hello</p>")

    assert calls["client"] == ("ses", "us-east-1")
    payload = calls["send_raw_email"]
    assert payload["Source"] == "no-reply@example.test"
    assert payload["Destinations"] == ["user@example.test"]
    assert b"Reset password" in payload["RawMessage"]["Data"]


def test_send_sync_rejects_unknown_provider(monkeypatch):
    module = _reload_email(monkeypatch, provider="bogus")

    with pytest.raises(RuntimeError, match="Unsupported EMAIL_PROVIDER"):
        module._send_sync("user@example.test", "Subject", "<p>Hello</p>")
