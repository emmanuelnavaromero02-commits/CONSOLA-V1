from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test_internal_key_with_more_than_32_chars")

for name in list(sys.modules):
    if name == "app" or name.startswith("app."):
        del sys.modules[name]

VAULT_ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(VAULT_ROOT)]
sys.path.insert(0, str(VAULT_ROOT))

vault_main = importlib.import_module("app.main")


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


def test_vault_blocks_missing_security_context_for_connections():
    with pytest.raises(HTTPException) as exc:
        vault_main._require_cartridge_scope({}, "hubspot")

    assert exc.value.status_code == 403


def test_vault_blocks_unscoped_admin_connection_writes(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    ctx = vault_main._security_context_from_header(
        _signed(
            {
                "trusted": True,
                "source": "console",
                "role": "admin",
                "allowed_cartridges": ["*"],
            }
        )
    )

    vault_main._require_cartridge_scope(ctx, "hubspot")
    with pytest.raises(HTTPException) as exc:
        vault_main._require_cartridge_scope(ctx, "hubspot", allow_platform_global=False)

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


def test_vault_blocks_unscoped_admin_workspace_secret_scope(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    ctx = vault_main._security_context_from_header(
        _signed(
            {
                "trusted": True,
                "source": "console",
                "role": "admin",
                "allowed_cartridges": ["*"],
            }
        )
    )

    with pytest.raises(HTTPException) as exc:
        vault_main._require_secret_scope(ctx, "anthropic")

    assert exc.value.status_code == 403


def test_seed_skips_unscoped_connections_in_production(monkeypatch, tmp_path):
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text(
        """
secrets:
  platform:
    API_TOKEN: test
  anthropic:
    API_KEY: should-not-seed
destinations:
  warehouse:
    host: db
connections:
  replicon:
    default:
      base_url: https://api.example
""",
        encoding="utf-8",
    )
    calls: list[tuple[str, str, str, dict]] = []
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_UNSCOPED_VAULT_CONNECTIONS", "true")
    monkeypatch.setattr(vault_main, "_SECRETS_FILE", secrets_file)
    monkeypatch.setattr(vault_main, "_db_upsert_if_absent", lambda *args: calls.append(args))

    vault_main._seed()

    assert ("secrets", "platform", "API_TOKEN", {"value": "test"}) in calls
    assert not any(call[:3] == ("secrets", "anthropic", "API_KEY") for call in calls)
    assert ("destinations", "platform", "warehouse", {"host": "db"}) in calls
    assert not any(call[0] == "connections" for call in calls)
