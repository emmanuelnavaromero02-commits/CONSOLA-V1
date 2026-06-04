from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test_internal_key_with_more_than_32_chars")

from app import main as vault_main


SIGNING_KEY = "s" * 64


def _signed(ctx: dict) -> str:
    signed = {**ctx, "_signed_at": 1, "_signature_version": "hmac-sha256-v1"}
    payload = {key: value for key, value in signed.items() if key not in {"_signature", "_signed_at", "_signature_version"}}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signed["_signature"] = hmac.new(SIGNING_KEY.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return json.dumps(signed)


def test_vault_accepts_signed_scoped_context(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    header = _signed(
        {
            "trusted": True,
            "source": "console",
            "role": "admin",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
            "allowed_cartridges": ["hubspot"],
        }
    )

    ctx = vault_main._security_context_from_header(header)

    assert ctx["trusted"] is True
    assert vault_main._vault_scope(ctx) == (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )


def test_vault_rejects_tampered_signed_context(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    header = _signed(
        {
            "trusted": True,
            "source": "console",
            "role": "admin",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
            "allowed_cartridges": ["hubspot"],
        }
    )
    payload = json.loads(header)
    payload["allowed_cartridges"] = ["*"]

    assert vault_main._security_context_from_header(json.dumps(payload)) == {}


def test_vault_blocks_cartridge_outside_signed_scope(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    ctx = vault_main._security_context_from_header(
        _signed(
            {
                "trusted": True,
                "source": "console",
                "role": "admin",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "22222222-2222-2222-2222-222222222222",
                "allowed_cartridges": ["hubspot"],
            }
        )
    )

    with pytest.raises(HTTPException) as exc:
        vault_main._require_cartridge_scope(ctx, "replicon")

    assert exc.value.status_code == 403


def test_vault_allows_workspace_secret_scopes_with_signed_context(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    ctx = vault_main._security_context_from_header(
        _signed(
            {
                "trusted": True,
                "source": "console",
                "role": "user",
                "workspace_role": "tenant_admin",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "22222222-2222-2222-2222-222222222222",
                "allowed_cartridges": ["*"],
            }
        )
    )

    vault_main._require_secret_scope(ctx, "llm")
    vault_main._require_secret_scope(ctx, "anthropic")


def test_vault_blocks_global_secret_scopes_for_workspace_context(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    ctx = vault_main._security_context_from_header(
        _signed(
            {
                "trusted": True,
                "source": "console",
                "role": "user",
                "workspace_role": "tenant_admin",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "22222222-2222-2222-2222-222222222222",
                "allowed_cartridges": ["*"],
            }
        )
    )

    with pytest.raises(HTTPException) as exc:
        vault_main._require_secret_scope(ctx, "global")

    assert exc.value.status_code == 403
