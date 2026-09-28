from __future__ import annotations

import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.services import app_forge, copilot_local_tools, tool_manifest  # noqa: E402


VALID_ARGS = {"objetivo": "kpis de ventas por semana", "datasets": ["ventas_diarias"]}


def test_local_tool_schema_never_accepts_html():
    schema = copilot_local_tools.GENERATE_APP_INPUT_SCHEMA
    assert "html" not in schema["properties"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"objetivo", "datasets"}
    with pytest.raises(ValueError, match="html"):
        copilot_local_tools.validate_generate_app_args(
            {**VALID_ARGS, "html": "<html></html>"}
        )


def test_local_tool_args_are_small_and_bounded():
    assert copilot_local_tools.validate_generate_app_args(dict(VALID_ARGS)) == VALID_ARGS
    with pytest.raises(ValueError, match="objetivo"):
        copilot_local_tools.validate_generate_app_args({"datasets": ["a"]})
    with pytest.raises(ValueError, match="datasets"):
        copilot_local_tools.validate_generate_app_args({"objetivo": "x", "datasets": []})
    with pytest.raises(ValueError, match="máximo 5"):
        copilot_local_tools.validate_generate_app_args(
            {"objetivo": "x", "datasets": ["a", "b", "c", "d", "e", "f"]}
        )
    with pytest.raises(ValueError, match="80"):
        copilot_local_tools.validate_generate_app_args(
            {"objetivo": "x", "datasets": ["a" * 81]}
        )
    with pytest.raises(ValueError, match="80"):
        copilot_local_tools.validate_generate_app_args(
            {**VALID_ARGS, "name": "n" * 81}
        )


def test_local_tool_is_classified_as_write_with_approval():
    meta = tool_manifest.classify_tool(copilot_local_tools.GENERATE_APP_TOOL)
    assert meta["risk_level"] == "write"
    assert meta["requires_approval"] is True
    assert tool_manifest.requires_approval(copilot_local_tools.GENERATE_APP_TOOL) is True


@pytest.mark.asyncio
async def test_invoke_returns_card_shape_on_success(monkeypatch):
    async def fake_forge(user, **kwargs):
        assert "html" not in kwargs
        return {
            "name": "ventas_semana",
            "title": "Ventas semana",
            "url": "/analytics/viewer?app=ventas_semana",
            "datasets": ["ventas_diarias"],
        }

    monkeypatch.setattr(app_forge, "generate_and_publish_app", fake_forge)
    result = await copilot_local_tools.invoke_local_tool(
        copilot_local_tools.GENERATE_APP_TOOL, dict(VALID_ARGS), user={"id": 1}
    )
    assert result["published"] is True
    assert result["app_url"] == "/analytics/viewer?app=ventas_semana"
    assert result["url"] == result["app_url"]
    assert result["name"] == "ventas_semana"
    assert "html" not in result


@pytest.mark.asyncio
async def test_invoke_surfaces_forge_errors_honestly(monkeypatch):
    async def failing_forge(user, **kwargs):
        raise app_forge.AppForgeError("sin permiso apps.write")

    monkeypatch.setattr(app_forge, "generate_and_publish_app", failing_forge)
    result = await copilot_local_tools.invoke_local_tool(
        copilot_local_tools.GENERATE_APP_TOOL, dict(VALID_ARGS), user={"id": 1}
    )
    assert result == {"error": "sin permiso apps.write"}


@pytest.mark.asyncio
async def test_invoke_rejects_bad_args_without_calling_forge(monkeypatch):
    calls = []

    async def fake_forge(user, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(app_forge, "generate_and_publish_app", fake_forge)
    result = await copilot_local_tools.invoke_local_tool(
        copilot_local_tools.GENERATE_APP_TOOL,
        {**VALID_ARGS, "html": "<html>x</html>"},
        user={"id": 1},
    )
    assert "tool_args_rejected" in result["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_unknown_local_tool_is_refused():
    result = await copilot_local_tools.invoke_local_tool("otra", {}, user=None)
    assert "not registered" in result["error"]
