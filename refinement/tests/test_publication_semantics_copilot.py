from __future__ import annotations

from types import SimpleNamespace

from refinement.app.publication_semantics import snapshot_public_semantics


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.description: list = []
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "FROM data_catalog" in sql:
            self.description = [
                SimpleNamespace(name=name)
                for name in ("column_name", "description", "example_values", "tags", "is_key", "is_metric")
            ]
            self._rows = [
                ("authored", "Autorizado", [1, 2], ["finance"], True, True),
                ("templated", "", None, ["semantic_enrichment", "auto_described", "key"], True, True),
            ]
        elif "FROM data_relationships" in sql:
            self.description = [
                SimpleNamespace(name=name)
                for name in ("from_column", "to_dataset", "to_column", "join_hint", "description", "cardinality")
            ]
            self._rows = [
                ("department_id", "departments", "department_id", "left", "Autorizada", "N:1"),
                ("company", "companies", "company", "LEFT", "", "many"),
            ]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self) -> None:
        self.cursor_obj = _Cursor()

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


def test_snapshot_keeps_copilot_inference_out_of_evidence():
    conn = _Conn()
    semantics = snapshot_public_semantics(
        lambda: conn,
        tenant_id="t",
        workspace_id="w",
        dataset={"name": "employees", "description": "Plantilla"},
    )
    catalog_sql = next(sql for sql in conn.cursor_obj.executed if "FROM data_catalog" in sql)
    edges_sql = next(sql for sql in conn.cursor_obj.executed if "FROM data_relationships" in sql)
    assert "CASE WHEN description_origin='copilot' THEN ''" in catalog_sql
    assert "classifications" not in catalog_sql
    assert "origin<>'copilot' AND status='active'" in edges_sql
    assert semantics["columns"]["authored"] == {
        "description": "Autorizado",
        "tags": ["finance"],
        "is_key": True,
        "is_metric": True,
        "example_values": [1, 2],
    }
    assert semantics["columns"]["templated"] == {
        "description": "",
        "tags": [],
        "is_key": False,
        "is_metric": False,
        "example_values": [],
    }
    assert semantics["relationships"] == [
        {
            "from_dataset": "employees",
            "from_column": "department_id",
            "to_dataset": "departments",
            "to_column": "department_id",
            "join_hint": "LEFT",
            "description": "Autorizada",
            "cardinality": "N:1",
        },
        {
            "from_dataset": "employees",
            "from_column": "company",
            "to_dataset": "companies",
            "to_column": "company",
            "join_hint": "LEFT",
            "description": "",
        },
    ]
