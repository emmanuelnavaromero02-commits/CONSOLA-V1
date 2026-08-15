from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException

SIGNING_KEY = "unit-test-security-context-signing-key-0123456789"


def _signed_context(tenant_id: str = "tenant-a", workspace_id: str = "ws-a", **overrides) -> dict:
    ctx = {
        "trusted": True,
        "source": "console",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "allowed_cartridges": ["banxico"],
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v1",
    }
    ctx.update(overrides)
    unsigned = {key: value for key, value in ctx.items() if key != "_signature"}
    ctx["_signature"] = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return ctx


@pytest.fixture()
def signing_env(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)


def _capture_runs(monkeypatch):
    from app.api import routes_skills

    calls: list[dict] = []

    def _fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success"}

    monkeypatch.setattr(routes_skills, "run_series_observations", _fake_run)
    return routes_skills, calls


def test_body_tenant_mismatch_is_rejected(monkeypatch, signing_env):
    routes_skills, calls = _capture_runs(monkeypatch)
    header = json.dumps(_signed_context(), ensure_ascii=False)
    body = {"tenant_id": "tenant-b", "workspace_id": "ws-a"}
    with pytest.raises(HTTPException) as excinfo:
        routes_skills.run_incremental(
            "series_observations", body=body, conn_id=None, x_security_context=header
        )
    assert excinfo.value.status_code == 403
    assert "tenant_id" in str(excinfo.value.detail)
    assert calls == []


def test_body_workspace_mismatch_is_rejected(monkeypatch, signing_env):
    routes_skills, calls = _capture_runs(monkeypatch)
    header = json.dumps(_signed_context(), ensure_ascii=False)
    body = {"tenant_id": "tenant-a", "workspace_id": "ws-b"}
    with pytest.raises(HTTPException) as excinfo:
        routes_skills.run_full_load(
            "series_observations", body=body, conn_id=None, x_security_context=header
        )
    assert excinfo.value.status_code == 403
    assert calls == []


def test_unsigned_body_context_never_routes_scope(monkeypatch, signing_env):
    routes_skills, calls = _capture_runs(monkeypatch)
    body = {
        "tenant_id": "tenant-b",
        "workspace_id": "ws-b",
        "security_context": {"trusted": True, "tenant_id": "tenant-b", "workspace_id": "ws-b"},
    }
    with pytest.raises(HTTPException) as excinfo:
        routes_skills.run_incremental(
            "series_observations", body=body, conn_id=None, x_security_context=None
        )
    assert excinfo.value.status_code == 403
    assert calls == []


def test_scope_resolves_only_from_verified_signed_context(monkeypatch, signing_env):
    routes_skills, calls = _capture_runs(monkeypatch)
    header = json.dumps(_signed_context(), ensure_ascii=False)
    result = routes_skills.run_incremental(
        "series_observations", body={}, conn_id=None, x_security_context=header
    )
    assert result == {"status": "success"}
    assert len(calls) == 1
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["workspace_id"] == "ws-a"


def test_tampered_signature_is_rejected(monkeypatch, signing_env):
    routes_skills, calls = _capture_runs(monkeypatch)
    ctx = _signed_context()
    ctx["tenant_id"] = "tenant-b"
    with pytest.raises(HTTPException) as excinfo:
        routes_skills.run_incremental(
            "series_observations",
            body={},
            conn_id=None,
            x_security_context=json.dumps(ctx, ensure_ascii=False),
        )
    assert excinfo.value.status_code == 403
    assert calls == []
