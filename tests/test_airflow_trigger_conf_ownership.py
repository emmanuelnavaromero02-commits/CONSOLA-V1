from __future__ import annotations

import asyncio
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
SIGNING_KEY = "airflow-trigger-conf-ownership-key-0123456789"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


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


@pytest.fixture
def mcp(monkeypatch):
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
    module = importlib.import_module("app.main")
    yield module
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _admin(**overrides) -> dict:
    ctx = {
        "trusted": True,
        "source": "console",
        "role": "super_admin",
        "permissions": ["pipelines.run", "cartridges.execute", "cartridges.read"],
        "allowed_cartridges": ["*"],
    }
    ctx.update(overrides)
    return _sign(ctx)


def _scoped(**overrides) -> dict:
    return _admin(
        role="admin",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        allowed_cartridges=["hubspot"],
        **overrides,
    )


def _forged() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "tenant_id": OTHER,
        "workspace_id": OTHER,
        "permissions": ["pipelines.run"],
        "allowed_cartridges": ["*"],
        "_signature": "0" * 64,
    }


def test_unscoped_admin_cannot_hand_a_context_to_a_dag(mcp):
    args = {
        "dag_id": "hubspot_extract",
        "conf": {
            "cartridge_id": "hubspot",
            "entity": "deals",
            "tenant_id": OTHER,
            "workspace_id": OTHER,
            "security_context": _forged(),
        },
    }
    mcp._validate_airflow_trigger_scope(_admin(), args)
    assert "security_context" not in args["conf"]
    assert args["conf"]["entity"] == "deals"


def test_unscoped_admin_cannot_replay_a_genuine_signed_context(mcp):
    genuine = _scoped()
    args = {"dag_id": "hubspot_extract", "conf": {"cartridge_id": "hubspot", "security_context": genuine}}
    mcp._validate_airflow_trigger_scope(_admin(), args)
    assert "security_context" not in args["conf"]


def test_scoped_caller_context_replaces_any_supplied_context(mcp, monkeypatch):
    monkeypatch.setattr(mcp, "_require_dag_registered_for_cartridge", lambda dag_id, cartridge_id: None)
    caller = _scoped()
    args = {
        "dag_id": "hubspot_extract",
        "conf": {"cartridge_id": "hubspot", "entity": "deals", "security_context": _forged()},
    }
    mcp._validate_airflow_trigger_scope(caller, args)
    assert args["conf"]["security_context"] is caller
    assert (args["conf"]["tenant_id"], args["conf"]["workspace_id"]) == (TENANT, WORKSPACE)


def test_scoped_caller_cannot_claim_another_workspace(mcp, monkeypatch):
    monkeypatch.setattr(mcp, "_require_dag_registered_for_cartridge", lambda dag_id, cartridge_id: None)
    args = {"dag_id": "hubspot_extract", "conf": {"cartridge_id": "hubspot", "workspace_id": OTHER}}
    with pytest.raises(HTTPException) as error:
        mcp._validate_airflow_trigger_scope(_scoped(), args)
    assert error.value.status_code == 403


def test_invoke_path_strips_a_supplied_context_for_unscoped_admins(mcp):
    request = mcp.InvokeRequest(
        tool="airflow_trigger_dag",
        args={"dag_id": "hubspot_extract", "conf": {"cartridge_id": "hubspot", "security_context": _forged()}},
        security_context=_admin(),
    )
    mcp._enforce_data_scope(request)
    assert "security_context" not in request.args["conf"]


@pytest.mark.parametrize("tool", ["cartridge_extract", "cartridge_extract_all"])
def test_cartridge_run_triggers_require_a_workspace_scope(mcp, tool):
    args = {"cartridge_id": "hubspot", "entity": "deals"} if tool == "cartridge_extract" else {"cartridge_id": "hubspot"}
    request = mcp.InvokeRequest(tool=tool, args=args, security_context=_admin())
    with pytest.raises(HTTPException) as error:
        mcp._enforce_data_scope(request)
    assert error.value.status_code == 403
    assert error.value.detail == "workspace scope required"


@pytest.mark.parametrize("tool", ["cartridge_extract", "cartridge_extract_all"])
def test_cartridge_run_triggers_inject_the_verified_scoped_context(mcp, tool):
    caller = _scoped()
    args = {"cartridge_id": "hubspot", "entity": "deals"} if tool == "cartridge_extract" else {"cartridge_id": "hubspot"}
    request = mcp.InvokeRequest(tool=tool, args=args, security_context=caller)
    mcp._enforce_data_scope(request)
    assert request.args["security_context"] == caller


@pytest.mark.parametrize("tool", ["cartridge_extract", "cartridge_extract_all"])
def test_cartridge_run_tools_refuse_unscoped_contexts_before_any_io(mcp, monkeypatch, tool):
    cartridges = importlib.import_module("app.tools.cartridges")
    monkeypatch.setattr(cartridges, "_conn", lambda: pytest.fail("no database access expected"))
    fn = getattr(cartridges, tool)
    kwargs = {"cartridge_id": "hubspot", "security_context": _admin()}
    if tool == "cartridge_extract":
        kwargs["entity"] = "deals"
    with pytest.raises(HTTPException) as error:
        asyncio.run(fn(**kwargs))
    assert error.value.status_code == 403
