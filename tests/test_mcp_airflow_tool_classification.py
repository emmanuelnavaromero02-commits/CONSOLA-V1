from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.test_mcp_domain_kpi_tools import _load_mcp_module, _purge_app_modules


ROOT = Path(__file__).resolve().parents[1]
AIRFLOW_TOOLS = ROOT / "mcp-infra" / "app" / "tools" / "airflow.py"


@pytest.fixture(autouse=True)
def _clean_imports():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    _purge_app_modules()


def _declared_airflow_tools() -> set[str]:
    tree = ast.parse(AIRFLOW_TOOLS.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "tool"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.add(str(keyword.value.value))
    return names


def test_every_airflow_tool_belongs_to_exactly_one_scope_set(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    sets = {
        "read": main._AIRFLOW_READ_TOOLS,
        "run": main._AIRFLOW_RUN_TOOLS,
        "recovery": main._AIRFLOW_RECOVERY_TOOLS,
        "write": main._AIRFLOW_WRITE_TOOLS,
    }
    declared = _declared_airflow_tools()
    assert {"airflow_describe_dag", "airflow_unpause_manual_dag", "airflow_mark_dag_run_failed"} <= declared
    for name in sorted(declared):
        owners = [label for label, members in sets.items() if name in members]
        assert len(owners) == 1, (name, owners)
    assert set().union(*sets.values()) == declared


def test_new_airflow_tools_land_in_the_expected_sets(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    assert "airflow_describe_dag" in main._AIRFLOW_READ_TOOLS
    assert "airflow_unpause_manual_dag" in main._AIRFLOW_RUN_TOOLS
    assert main._AIRFLOW_RECOVERY_TOOLS == {"airflow_mark_dag_run_failed"}


def test_unclassified_tool_would_bypass_scope_so_none_exist(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    for name in sorted(_declared_airflow_tools()):
        req = main.InvokeRequest(tool=name, args={}, security_context=None)
        with pytest.raises(Exception) as exc:
            main._enforce_data_scope(req, "console")
        assert getattr(exc.value, "status_code", None) == 403, name


def test_console_manifest_keeps_mutating_airflow_tools_behind_approval():
    sys.path.insert(0, str(ROOT / "console"))
    _purge_app_modules()
    from app.services import tool_manifest

    assert tool_manifest.classify_tool("airflow_describe_dag") == {
        "risk_level": "read",
        "requires_approval": False,
        "freshness_minutes": tool_manifest.DEFAULT_FRESHNESS_MINUTES,
    }
    for name in ("airflow_unpause_manual_dag", "airflow_mark_dag_run_failed"):
        meta = tool_manifest.classify_tool(name)
        assert meta["risk_level"] in {"write", "destructive"}, name
        assert meta["requires_approval"] is True, name
        assert name not in tool_manifest.READ_ONLY_TOOLS
