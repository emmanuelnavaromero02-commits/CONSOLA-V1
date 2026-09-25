from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "39_audit_tool_columns.sql"


@pytest.fixture()
def audit_module():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    _SIBLINGS = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    import importlib
    return importlib.import_module("app.services.audit_service")


def test_migration_39_exists():
    assert MIGRATION.exists(), "migration 39 not found"


def test_migration_39_adds_tool_columns():
    sql = MIGRATION.read_text(encoding="utf-8")
    for col in ("tool_name", "tool_args", "tool_result_status", "risk_level", "conversation_id"):
        assert re.search(rf"ADD COLUMN IF NOT EXISTS\s+{col}\b", sql, re.IGNORECASE), \
            f"column {col} missing from migration 39"


def test_migration_39_conversation_id_fk():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "REFERENCES conversations(id)" in sql
    assert "ON DELETE SET NULL" in sql


def test_migration_39_indices():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "idx_audit_tool_name" in sql
    assert "idx_audit_conversation" in sql


def test_record_event_accepts_tool_metadata(audit_module):
    sig = inspect.signature(audit_module.record_event)
    for kw in ("tool_name", "tool_args", "tool_result_status", "risk_level", "conversation_id"):
        assert kw in sig.parameters, f"{kw} not in record_event signature"
        assert sig.parameters[kw].default is None
        assert sig.parameters[kw].kind == inspect.Parameter.KEYWORD_ONLY


def test_record_event_backwards_compatible(audit_module):
    sig = inspect.signature(audit_module.record_event)
    sig.bind(
        user_id=1,
        email="a@b.com",
        action="user.created",
        resource_type="user",
        resource_id="42",
        metadata={"role": "admin"},
    )


def test_record_event_accepts_tool_kwargs_without_error(audit_module):
    sig = inspect.signature(audit_module.record_event)
    sig.bind(
        user_id=1,
        email="x@y.com",
        action="tool.invoke",
        tool_name="postgres_select",
        tool_args={"table": "users"},
        tool_result_status="ok",
        risk_level="read",
        conversation_id="00000000-0000-0000-0000-000000000000",
    )
