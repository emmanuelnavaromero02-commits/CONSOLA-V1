"""Mission 2 — the three domain KPI tools reach the LLM like talent_kpis_read.

Checks the whole registration chain: mcp-infra catalog (name, Spanish
description, top_n schema), the two allowlists that would otherwise silently
break the tool (mcp-infra _CONTROL_ROOM_READ_TOOLS -> security_context
injection; console tool_manifest.READ_ONLY_TOOLS -> risk=read without
approval), console-side argument validation, and invocation through
mcp-infra's scope gate: no security_context -> 403, valid signed context ->
data via the internal read bridge (console call mocked).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "domain_kpi_tools_signing_key_64_chars_aaaaaaaaaaaaaaaaaaaaa"
SERVICE_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)

DOMAIN_TOOLS = (
    "control_room__finance_kpis_read",
    "control_room__operations_kpis_read",
    "control_room__risk_kpis_read",
)
TOOL_VIEWS = {
    "control_room__finance_kpis_read": "finance_kpis",
    "control_room__operations_kpis_read": "operations_kpis",
    "control_room__risk_kpis_read": "risk_kpis",
}
TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _clean_imports():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    _purge_app_modules()


def _load_mcp_module(monkeypatch, module: str):
    _purge_app_modules()
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_MARKERS)
    ]
    sys.path.insert(0, str(ROOT / "mcp-infra"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "INTERNAL_API_KEY", "legacy_transport_key_64_chars_bbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
        "console_to_mcp_key_64_chars_cccccccccccccccccccccc",
    )
    monkeypatch.setenv(
        "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE",
        "mcp_to_console_key_64_chars_dddddddddddddddddddddd",
    )
    monkeypatch.setenv(
        "INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT",
        "mcp_to_refinement_key_64_chars_eeeeeeeeeeeeeeeeeee",
    )
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "miniosecret")
    return importlib.import_module(module)


def _load_console_module(module: str):
    _purge_app_modules()
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_MARKERS)
    ]
    sys.path.insert(0, str(ROOT / "console"))
    return importlib.import_module(module)


def _signed(ctx: dict[str, Any]) -> dict[str, Any]:
    signed = {
        **ctx,
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v1",
    }
    payload = {name: value for name, value in signed.items() if name != "_signature"}
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    signed["_signature"] = hmac.new(
        SIGNING_KEY.encode(), raw, hashlib.sha256
    ).hexdigest()
    return signed


def _ctx(permissions: list[str] | None = None) -> dict[str, Any]:
    return _signed(
        {
            "trusted": True,
            "source": "console",
            "role": "analyst",
            "email": "analyst@omega.local",
            "tenant_id": TENANT,
            "workspace_id": WORKSPACE,
            "permissions": permissions or ["datasets.read", "copilot.use"],
            "allowed_cartridges": [
                "replicon",
                "sap_hcm",
                "sap_successfactors",
                "salesforce",
            ],
        }
    )


# ── catalog + allowlists (the two traps) ────────────────────────────────────


def test_domain_kpi_tools_are_in_the_mcp_catalog(monkeypatch):
    _load_mcp_module(monkeypatch, "app.main")
    registry = importlib.import_module("app.registry")
    tools = {tool["name"]: tool for tool in registry.list_tools()}

    for name in DOMAIN_TOOLS:
        assert name in tools, name
        description = tools[name]["description"]
        assert "Solo lectura" in description, name
        assert "NO " in description, name  # says what it does not measure
        assert tools[name]["input_schema"]["additionalProperties"] is False
    for name in ("control_room__finance_kpis_read", "control_room__risk_kpis_read"):
        top_n = tools[name]["input_schema"]["properties"]["top_n"]
        assert top_n["type"] == "integer"
        assert (top_n["minimum"], top_n["maximum"]) == (0, 10)
    assert (
        tools["control_room__operations_kpis_read"]["input_schema"]["properties"] == {}
    )


def test_domain_kpi_tools_are_in_mcp_infra_read_allowlist(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    for name in DOMAIN_TOOLS:
        assert name in main._CONTROL_ROOM_READ_TOOLS, name
        assert name not in main._CONTROL_ROOM_OPERATIONAL_READ_TOOLS, name
        assert (
            name
            not in main._CONTROL_ROOM_ALERT_TOOLS | main._CONTROL_ROOM_ANALYSIS_TOOLS
        )


def test_domain_kpi_tools_are_console_read_only_without_approval():
    manifest = _load_console_module("app.services.tool_manifest")
    source = (ROOT / "console/app/services/tool_manifest.py").read_text(
        encoding="utf-8"
    )
    read_only_block = source.split("READ_ONLY_TOOLS = {", 1)[1].split("}", 1)[0]
    for name in DOMAIN_TOOLS:
        assert name in manifest.READ_ONLY_TOOLS, name
        assert f'"{name}"' in read_only_block, name
        meta = manifest.classify_tool(name)
        assert meta["risk_level"] == "read", name
        assert meta["requires_approval"] is False, name


def test_console_arg_validation_accepts_bounded_top_n_only(monkeypatch):
    # The schema console validates against is the one the catalog advertises,
    # and the server-side clamp is what actually bounds the answer.
    _load_mcp_module(monkeypatch, "app.main")
    registry = importlib.import_module("app.registry")
    control_room = importlib.import_module("app.tools.control_room")
    schema = {tool["name"]: tool["input_schema"] for tool in registry.list_tools()}[
        "control_room__risk_kpis_read"
    ]
    assert schema["properties"]["top_n"]["maximum"] == 10
    clamps = {
        value: control_room._named_rows(value) for value in (11, -3, None, "x", 4)
    }
    assert clamps == {11: 10, -3: 0, None: 0, "x": 0, 4: 4}

    tool_policy = _load_console_module("app.services.tool_policy")
    assert tool_policy.validate_tool_args(
        "control_room__risk_kpis_read", {"top_n": 5}, schema, risk_level="read"
    ) == {"top_n": 5}
    assert (
        tool_policy.validate_tool_args(
            "control_room__risk_kpis_read", {}, schema, risk_level="read"
        )
        == {}
    )
    with pytest.raises(tool_policy.ToolPolicyError):
        tool_policy.validate_tool_args(
            "control_room__risk_kpis_read", {"top_n": "5"}, schema, risk_level="read"
        )
    with pytest.raises(tool_policy.ToolPolicyError):
        tool_policy.validate_tool_args(
            "control_room__risk_kpis_read",
            {"security_context": {"trusted": True}},
            schema,
            risk_level="read",
        )
    # console does not enforce minimum/maximum: out-of-range args pass here and
    # are bounded server-side (clamps above), never by the LLM's request.
    assert tool_policy.validate_tool_args(
        "control_room__risk_kpis_read", {"top_n": 11}, schema, risk_level="read"
    ) == {"top_n": 11}


# ── invocation through the scope gate ───────────────────────────────────────


@pytest.mark.parametrize("tool", DOMAIN_TOOLS)
def test_invoke_without_security_context_is_403(monkeypatch, tool):
    main = _load_mcp_module(monkeypatch, "app.main")
    req = main.InvokeRequest(tool=tool, args={})

    with pytest.raises(HTTPException) as exc:
        main._enforce_data_scope(req, "console")
    assert exc.value.status_code == 403
    assert "trusted security_context required" in str(exc.value.detail)

    # The tool itself also fails closed when called without a trusted context.
    control_room = importlib.import_module("app.tools.control_room")
    with pytest.raises(HTTPException) as direct:
        asyncio.run(getattr(control_room, tool)())
    assert direct.value.status_code == 403


@pytest.mark.parametrize("tool", DOMAIN_TOOLS)
def test_invoke_requires_datasets_read_and_scope(monkeypatch, tool):
    main = _load_mcp_module(monkeypatch, "app.main")

    req = main.InvokeRequest(
        tool=tool, args={}, security_context=_ctx(["operations.read"])
    )
    with pytest.raises(HTTPException) as exc:
        main._enforce_data_scope(req, "console")
    assert exc.value.status_code == 403
    assert "datasets.read" in str(exc.value.detail)

    unscoped = _signed({**_ctx(), "workspace_id": ""})
    req = main.InvokeRequest(tool=tool, args={}, security_context=unscoped)
    with pytest.raises(HTTPException) as exc:
        main._enforce_data_scope(req, "console")
    assert exc.value.status_code == 403
    # Pin the detail: a broken re-sign would also raise 403 ("Invalid signed
    # security_context") and hide that the scope branch is no longer reached.
    assert "tenant/workspace scope" in str(exc.value.detail)

    req = main.InvokeRequest(
        tool=tool, args={"security_context": {"trusted": True}}, security_context=_ctx()
    )
    with pytest.raises(HTTPException) as exc:
        main._enforce_data_scope(req, "console")
    assert exc.value.status_code == 403
    assert "backend-owned arg" in str(exc.value.detail)


@pytest.mark.parametrize("tool", DOMAIN_TOOLS)
def test_invoke_with_valid_context_returns_data(monkeypatch, tool):
    main = _load_mcp_module(monkeypatch, "app.main")
    registry = importlib.import_module("app.registry")
    control_room = importlib.import_module("app.tools.control_room")
    captured: dict[str, Any] = {}

    async def fake_call_console(path, payload, timeout=0):
        captured["path"] = path
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {
            "ok": True,
            "data": {
                "domain": TOOL_VIEWS[tool].split("_")[0],
                "status": "ready",
                "metrics": {"example": {"status": "ready", "proxy_note": "nota"}},
            },
        }

    monkeypatch.setattr(control_room, "_call_console", fake_call_console)
    args = {"top_n": 50} if tool != "control_room__operations_kpis_read" else {}
    req = main.InvokeRequest(tool=tool, args=args, security_context=_ctx())

    main._enforce_data_scope(req, "console")

    assert req.args["security_context"]["tenant_id"] == TENANT
    result = asyncio.run(registry.invoke(req.tool, req.args))

    assert result["ok"] is True
    assert result["view"] == TOOL_VIEWS[tool]
    assert result["tenant_id"] == TENANT
    assert result["workspace_id"] == WORKSPACE
    assert result["data"]["status"] == "ready"
    assert captured["path"] == "/api/control-room/internal/read"
    assert captured["payload"]["view"] == TOOL_VIEWS[tool]
    assert captured["payload"]["security_context"]["workspace_id"] == WORKSPACE
    if tool == "control_room__operations_kpis_read":
        assert captured["payload"]["params"] == {}
    else:
        assert captured["payload"]["params"] == {"top_n": 10}  # clamped to the max


def test_invoke_over_http_end_to_end(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    control_room = importlib.import_module("app.tools.control_room")
    from fastapi.testclient import TestClient

    async def fake_call_console(path, payload, timeout=0):
        return {
            "ok": True,
            "data": {"domain": "risk", "status": "degraded", "metrics": {}},
        }

    monkeypatch.setattr(control_room, "_call_console", fake_call_console)
    client = TestClient(main.app)
    headers = {
        "x-api-key": "console_to_mcp_key_64_chars_cccccccccccccccccccccc",
        "x-internal-service": "console",
    }

    denied = client.post(
        "/mcp/invoke",
        json={"tool": "control_room__risk_kpis_read", "args": {"top_n": 3}},
        headers=headers,
    )
    assert denied.status_code == 403

    granted = client.post(
        "/mcp/invoke",
        json={
            "tool": "control_room__risk_kpis_read",
            "args": {"top_n": 3},
            "security_context": _ctx(),
        },
        headers=headers,
    )
    assert granted.status_code == 200, granted.text
    body = granted.json()["result"]
    assert body["view"] == "risk_kpis"
    assert body["data"]["status"] == "degraded"
