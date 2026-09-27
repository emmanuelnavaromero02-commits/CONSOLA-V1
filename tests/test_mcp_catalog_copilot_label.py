from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEC = {
    "trusted": True,
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
}


def _load(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(ROOT / "mcp-infra"))
    for key, value in {
        "AIRFLOW_URL": "http://airflow:8080/airflow",
        "AIRFLOW_USER": "admin",
        "AIRFLOW_PASSWORD": "admin",
        "PG_PASSWORD": "postgres",
        "SUPERSET_USER": "admin",
        "SUPERSET_PASSWORD": "admin",
    }.items():
        monkeypatch.setenv(key, value)
    try:
        return importlib.import_module("app.tools.cartridges")
    finally:
        sys.path.remove(str(ROOT / "mcp-infra"))


class _Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return self.results.pop(0) if self.results else []


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def test_label_marks_only_copilot_text(monkeypatch):
    tools = _load(monkeypatch)
    label = tools.copilot_labelled_description
    assert label("Correo de la persona.", "copilot") == (
        "Correo de la persona. (inferido por Copiloto)"
    )
    assert label("Salario autorizado", "manual") == "Salario autorizado"
    assert label("Salario autorizado", None) == "Salario autorizado"
    assert label("", "copilot") == ""


def test_semantic_readers_select_the_origin_and_label_copilot_text(monkeypatch):
    tools = _load(monkeypatch)
    monkeypatch.setattr(tools, "published_dataset_names", lambda sec: {"employees"})
    monkeypatch.setattr(tools, "_set_pg_scope", lambda cur, sec: (SEC["tenant_id"], SEC["workspace_id"]))
    cursor = _Cursor(
        [
            [],
            [
                ("employees", "email", "VARCHAR", "Correo de la persona.", [], False, "copilot"),
                ("employees", "salary", "DECIMAL", "Salario autorizado", ["finance"], True, "manual"),
                ("hidden", "x", "VARCHAR", "No visible", [], False, "copilot"),
            ],
        ]
    )
    monkeypatch.setattr(tools, "_conn", lambda: _Conn(cursor))
    result = tools.cartridge_get_semantic("sap_successfactors", SEC)
    assert "description_origin" in cursor.executed[1]
    assert [column["description"] for column in result["data_catalog"]] == [
        "Correo de la persona. (inferido por Copiloto)",
        "Salario autorizado",
    ]

    search_cursor = _Cursor(
        [[], [("employees", "email", "VARCHAR", "Correo de la persona.", "copilot")]]
    )
    monkeypatch.setattr(tools, "_conn", lambda: _Conn(search_cursor))

    async def no_sources():
        return []

    monkeypatch.setattr(tools, "_safe_list_rag_sources", no_sources)
    monkeypatch.setattr(
        tools, "_scoped_rag_source_name", lambda base, sec=None, *a, **k: base
    )
    rag_store = importlib.import_module("app.rag.store")
    monkeypatch.setattr(rag_store, "list_sources", no_sources)
    found = asyncio.run(tools.cartridge_search_term("sap_successfactors", "correo", SEC))
    assert "description_origin" in search_cursor.executed[1]
    assert found["matches_in_data_catalog"] == [
        {
            "dataset": "employees",
            "column": "email",
            "type": "VARCHAR",
            "description": "Correo de la persona. (inferido por Copiloto)",
        }
    ]


def test_every_mcp_catalog_reader_carries_the_origin():
    cartridges = (ROOT / "mcp-infra/app/tools/cartridges.py").read_text(encoding="utf-8")
    main = (ROOT / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    assert cartridges.count("description_origin") >= 3
    assert cartridges.count("copilot_labelled_description(") >= 4
    assert "description_origin" in main.split("async def _rebuild_semantic_doc", 1)[1]
    assert "copilot_labelled_description(r[2], r[4])" in main
