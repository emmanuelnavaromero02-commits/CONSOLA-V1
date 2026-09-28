from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import HTTPException


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _GeneratedSQLValidationError(ValueError):
    pass


class _DuckDBEngineStub:
    pass


class _RecordingEngine:
    _STATEMENT_TIMEOUT_SECONDS = 30

    def __init__(self, schema):
        self.schema = schema
        self.calls: list[tuple[str, str]] = []
        self.timeouts: list[float | None] = []

    def get_source_schema(self, source, ctx, *, timeout_seconds=None):
        self.calls.append(("schema", source))
        self.timeouts.append(timeout_seconds)
        return self.schema

    def preview_source(self, source, limit, ctx):
        self.calls.append(("preview", source))
        return {"data": [{"id": "1"}]}


@pytest.fixture()
def refinement_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "test_internal_api_key_with_more_than_32_chars")
    monkeypatch.setitem(sys.modules, "app.duckdb_engine", _module(DuckDBEngine=_DuckDBEngineStub))
    monkeypatch.setitem(sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object()))

    async def generate_sql(*args, **kwargs):
        return "", ""

    monkeypatch.setitem(
        sys.modules,
        "app.llm_sql",
        _module(GeneratedSQLValidationError=_GeneratedSQLValidationError, generate_sql=generate_sql),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.security",
        _module(get_internal_api_key=lambda: "test_internal_api_key_with_more_than_32_chars"),
    )
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "_require_source_scope", lambda body, source: {"source": source})
    monkeypatch.setattr(main, "_trusted_user_context", lambda body, args: {"workspace_id": "w"})
    yield main
    sys.modules.pop("app.main", None)


def _describe(main, **args):
    return main._mcp_invoke_sync({"tool": "describe_source", "args": {"source": "raw/acme/E", **args}})


def test_schema_only_skips_the_row_preview(refinement_main, monkeypatch):
    engine = _RecordingEngine({"fields": [{"name": "id", "type": "VARCHAR"}]})
    monkeypatch.setattr(refinement_main, "engine", engine)

    result = _describe(refinement_main, schema_only=True)

    assert engine.calls == [("schema", "raw/acme/E")]
    assert engine.timeouts == [30]
    assert result == {
        "source": "raw/acme/E",
        "fields": [{"name": "id", "type": "VARCHAR"}],
        "sample": [],
        "error": None,
    }


def test_schema_only_reports_schema_errors(refinement_main, monkeypatch):
    engine = _RecordingEngine({"source": "raw/acme/E", "error": "No files found"})
    monkeypatch.setattr(refinement_main, "engine", engine)

    result = _describe(refinement_main, schema_only=True)

    assert result["error"] == "No files found"
    assert result["fields"] == []
    assert engine.calls == [("schema", "raw/acme/E")]


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_default_describe_still_returns_a_sample(refinement_main, monkeypatch, flag):
    engine = _RecordingEngine({"fields": [{"name": "id", "type": "VARCHAR"}]})
    monkeypatch.setattr(refinement_main, "engine", engine)
    args = {} if flag is None else {"schema_only": flag}

    result = _describe(refinement_main, **args)

    assert engine.calls == [("schema", "raw/acme/E"), ("preview", "raw/acme/E")]
    assert engine.timeouts == [None]
    assert result["sample"] == [{"id": "1"}]


def test_schema_only_still_enforces_source_scope(refinement_main, monkeypatch):
    engine = _RecordingEngine({"fields": []})
    monkeypatch.setattr(refinement_main, "engine", engine)

    def deny(body, source):
        raise HTTPException(403, "source prefix not allowed")

    monkeypatch.setattr(refinement_main, "_require_source_scope", deny)
    with pytest.raises(HTTPException) as exc:
        _describe(refinement_main, schema_only=True)
    assert exc.value.status_code == 403
    assert engine.calls == []


def test_tool_manifest_declares_schema_only(refinement_main):
    import asyncio

    tools = asyncio.run(refinement_main.mcp_tools())
    items = tools.get("tools", tools) if isinstance(tools, dict) else tools
    describe = next(tool for tool in items if tool["name"] == "describe_source")
    schema_only = describe["input_schema"]["properties"]["schema_only"]
    assert schema_only["type"] == "boolean"
    assert schema_only["default"] is False
