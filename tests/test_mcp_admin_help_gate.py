from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]
SIGNING_KEY = "test-security-context-signing-key-0123456789"


def _sign(ctx: dict) -> dict:
    signed = dict(ctx)
    signed["_signed_at"] = int(time.time())
    signed["_signature_version"] = "hmac-sha256-v1"
    unsigned = {key: value for key, value in signed.items() if key != "_signature"}
    signed["_signature"] = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signed


def _main_module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key-aaaaaaaaaaaaaaaaaaaaaaaa")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.syspath_prepend(str(REPO / "mcp-infra"))
    return importlib.import_module("app.main")


def _request(module, security_context):
    return module.InvokeRequest(
        tool="request_admin_help",
        args={
            "user_question": "q",
            "why_unanswerable": "w",
            "what_is_needed": "n",
        },
        security_context=security_context,
    )


def test_admin_help_rejects_missing_context(monkeypatch):
    module = _main_module(monkeypatch)
    with pytest.raises(HTTPException) as error:
        module._enforce_data_scope(_request(module, None))
    assert error.value.status_code == 403
    assert "trusted" in str(error.value.detail)


def test_admin_help_rejects_untrusted_context(monkeypatch):
    module = _main_module(monkeypatch)
    ctx = {"trusted": False, "permissions": ["datasets.read"]}
    with pytest.raises(HTTPException) as error:
        module._enforce_data_scope(_request(module, ctx))
    assert error.value.status_code == 403


def test_admin_help_rejects_context_without_permission(monkeypatch):
    module = _main_module(monkeypatch)
    ctx = _sign({
        "trusted": True,
        "source": "workspace",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "permissions": [],
    })
    with pytest.raises(HTTPException) as error:
        module._enforce_data_scope(_request(module, ctx))
    assert error.value.status_code == 403
    assert "datasets.read" in str(error.value.detail)


def test_admin_help_allows_trusted_assistant_context(monkeypatch):
    module = _main_module(monkeypatch)
    ctx = _sign({
        "trusted": True,
        "source": "workspace",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "permissions": ["datasets.read", "apps.read", "workspace.access"],
    })
    resolved = module._enforce_data_scope(_request(module, ctx))
    assert isinstance(resolved, dict)
    assert resolved.get("trusted") is True
