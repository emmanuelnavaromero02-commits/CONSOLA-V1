from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DAGS = ROOT / "airflow" / "dags"


class _FakeDAG:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakePythonOperator:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __rshift__(self, other):
        return other


def load_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    alias: str | None = None,
):
    monkeypatch.syspath_prepend(str(DAGS))
    module_name = alias or f"_test_{name}"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, DAGS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def load_dag(monkeypatch: pytest.MonkeyPatch, name: str):
    airflow = types.ModuleType("airflow")
    airflow.DAG = _FakeDAG
    airflow_python = types.ModuleType("airflow.operators.python")
    airflow_python.PythonOperator = _FakePythonOperator
    airflow_operators = types.ModuleType("airflow.operators")
    airflow_operators.python = airflow_python
    airflow_trigger = types.ModuleType("airflow.utils.trigger_rule")
    airflow_trigger.TriggerRule = types.SimpleNamespace(ALL_DONE="all_done")
    airflow_utils = types.ModuleType("airflow.utils")
    airflow_utils.trigger_rule = airflow_trigger
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.operators", airflow_operators)
    monkeypatch.setitem(sys.modules, "airflow.operators.python", airflow_python)
    monkeypatch.setitem(sys.modules, "airflow.utils", airflow_utils)
    monkeypatch.setitem(sys.modules, "airflow.utils.trigger_rule", airflow_trigger)
    return load_module(monkeypatch, name)
