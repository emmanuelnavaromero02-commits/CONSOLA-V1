"""control_room__sap_b1_kpis_read: catalog, allowlists, scope gate and view routing."""

from __future__ import annotations

import asyncio
import importlib
import sys
from typing import Any

import pytest
from fastapi import HTTPException

from tests.test_mcp_domain_kpi_tools import TENANT, WORKSPACE, _ctx, _load_console_module, _load_mcp_module, _purge_app_modules

TOOL = "control_room__sap_b1_kpis_read"


@pytest.fixture(autouse=True)
def _clean_imports():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    _purge_app_modules()


def test_tool_is_catalogued_read_only_with_a_case_enum(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    registry = importlib.import_module("app.registry")
    tool = {item["name"]: item for item in registry.list_tools()}[TOOL]
    schema = tool["input_schema"]
    assert "Solo lectura" in tool["description"] and "NO " in tool["description"]
    assert schema["additionalProperties"] is False and schema["required"] == ["case"]
    assert schema["properties"]["case"]["enum"] == ["margen"]
    assert (schema["properties"]["top_n"]["minimum"], schema["properties"]["top_n"]["maximum"]) == (0, 10)
    assert TOOL in main._CONTROL_ROOM_READ_TOOLS
    assert TOOL not in main._CONTROL_ROOM_ALERT_TOOLS | main._CONTROL_ROOM_ANALYSIS_TOOLS


def test_console_classifies_it_read_only_without_approval():
    manifest = _load_console_module("app.services.tool_manifest")
    meta = manifest.classify_tool(TOOL)
    assert TOOL in manifest.READ_ONLY_TOOLS and meta["risk_level"] == "read" and meta["requires_approval"] is False


def test_invoke_is_scoped_and_routes_the_case_to_its_view(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    registry = importlib.import_module("app.registry")
    control_room = importlib.import_module("app.tools.control_room")

    with pytest.raises(HTTPException) as exc:
        main._enforce_data_scope(main.InvokeRequest(tool=TOOL, args={"case": "margen"}), "console")
    assert exc.value.status_code == 403

    captured: dict[str, Any] = {}

    async def fake_call_console(path, payload, timeout=0):
        captured.update(path=path, payload=payload)
        return {"ok": True, "data": {"domain": "sap_b1_margin", "status": "ready", "metrics": {}}}

    monkeypatch.setattr(control_room, "_call_console", fake_call_console)
    req = main.InvokeRequest(tool=TOOL, args={"case": "Margen", "top_n": 40}, security_context=_ctx())
    main._enforce_data_scope(req, "console")
    result = asyncio.run(registry.invoke(req.tool, req.args))
    assert result["view"] == "sap_b1_margin_kpis" and result["workspace_id"] == WORKSPACE and result["tenant_id"] == TENANT
    assert captured["payload"]["view"] == "sap_b1_margin_kpis"
    assert captured["payload"]["params"] == {"top_n": 10}

    with pytest.raises(ValueError):
        asyncio.run(control_room.control_room__sap_b1_kpis_read(case="ventas", security_context=req.args["security_context"]))
