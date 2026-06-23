"""Sprint v1.32 — SAP DAG files must import and expose a DAG object."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SAP_DAGS = (
    ROOT / "cartridges/sap_hcm/dags/sap_hcm_extract.py",
    ROOT / "cartridges/sap_hcm/dags/sap_hcm_extract_all.py",
    ROOT / "cartridges/sap_s4hana/dags/sap_s4hana_extract.py",
    ROOT / "cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py",
    ROOT / "cartridges/sap_successfactors/dags/sap_successfactors_extract.py",
    ROOT / "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py",
)


def _install_airflow_stubs(monkeypatch):
    airflow = types.ModuleType("airflow")
    decorators = types.ModuleType("airflow.decorators")
    exceptions = types.ModuleType("airflow.exceptions")

    def dag(*_args, **_kwargs):
        def decorate(fn):
            def wrapper(*args, **kwargs):
                fn(*args, **kwargs)
                return {"dag_id": fn.__name__}
            return wrapper
        return decorate

    def task(fn):
        def wrapper(*_args, **_kwargs):
            return None
        return wrapper

    decorators.dag = dag
    decorators.task = task
    exceptions.AirflowFailException = RuntimeError
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.decorators", decorators)
    monkeypatch.setitem(sys.modules, "airflow.exceptions", exceptions)


@pytest.mark.parametrize("path", SAP_DAGS)
def test_sap_dag_imports_and_exposes_dag(path: Path, monkeypatch):
    _install_airflow_stubs(monkeypatch)
    module_name = f"_sap_dag_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader

    spec.loader.exec_module(module)

    assert getattr(module, "dag", None) is not None
