from __future__ import annotations

import hashlib
import hmac
import json

from app.services.security_context import build_security_context


def test_build_security_context_signs_trusted_payload(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "a" * 64)
    user = {
        "id": 1,
        "email": "tenant@example.com",
        "role": "admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
        "allowed_cartridges": ["hubspot"],
        "permissions": ["datasets.read"],
    }

    ctx = build_security_context(user)

    assert ctx["trusted"] is True
    assert ctx["tenant_id"] == user["active_tenant_id"]
    assert ctx["workspace_id"] == user["active_workspace_id"]
    assert ctx["allowed_cartridges"] == ["hubspot"]
    assert ctx["_signature_version"] == "hmac-sha256-v1"
    assert len(ctx["_signature"]) == 64


def test_build_security_context_requires_signing_material_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECURITY_CONTEXT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)

    try:
        build_security_context({"id": 1, "email": "a@example.com"})
    except RuntimeError as exc:
        assert "SECURITY_CONTEXT_SIGNING_KEY" in str(exc)
    else:
        raise AssertionError("expected production signing material failure")


def test_security_context_does_not_fall_back_to_internal_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("SECURITY_CONTEXT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", "b" * 64)

    try:
        build_security_context({"id": 1, "email": "a@example.com"})
    except RuntimeError as exc:
        assert "SECURITY_CONTEXT_SIGNING_KEY" in str(exc)
    else:
        raise AssertionError("expected signing key failure outside production too")


def test_security_context_signing_key_must_be_distinct_from_transport_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "b" * 64)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "b" * 64)

    try:
        build_security_context({"id": 1, "email": "a@example.com"})
    except RuntimeError as exc:
        assert "distinct" in str(exc)
    else:
        raise AssertionError("expected transport/signing key collision failure")


def test_security_context_signature_covers_signed_at(monkeypatch):
    key = "c" * 64
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", key)

    ctx = build_security_context({"id": 1, "email": "a@example.com"})
    payload = {name: value for name, value in ctx.items() if name != "_signature"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    expected = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    legacy_payload = {
        name: value
        for name, value in ctx.items()
        if name not in {"_signature", "_signed_at", "_signature_version"}
    }
    legacy_raw = json.dumps(legacy_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    legacy_expected = hmac.new(key.encode("utf-8"), legacy_raw, hashlib.sha256).hexdigest()

    assert ctx["_signature"] == expected
    assert ctx["_signature"] != legacy_expected
