"""In-memory doubles for the Catalog Copilot worker tests.

FakeCopilotStore mirrors the provenance guards that CatalogCopilotStore
enforces in SQL (tests/test_catalog_copilot_live.py runs the same scenarios
against the real schema). LocalEngine wraps a real DuckDBEngine over local
Parquet files so probes run genuine DuckDB queries.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from refinement.app.catalog_copilot_rules import evidence_is_counts_only
from refinement.app.catalog_copilot_store import WriteResult, edge_key
from refinement.app.duckdb_engine import DuckDBEngine

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
SEC = {
    "trusted": True,
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
    "role": "admin",
    "workspace_role": "workspace_admin",
    "allowed_cartridges": ["*"],
    "permissions": ["datasets.read", "datasets.write"],
}


@dataclass
class FakeSnapshot:
    head: dict[str, Any]
    evidence: dict[str, Any] | None


class FakeCopilotStore:
    def __init__(self) -> None:
        self.columns: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.protection: dict[str, str] = {}
        self.lock = threading.Lock()
        self.saved_states = 0
        self.fail_edges = False
        self.max_summary_bytes: int | None = None
        self.refuse_source_columns = False

    def add_column(self, dataset: str, column: str, **values: Any) -> None:
        row = {
            "dataset": dataset,
            "column_name": column,
            "data_type": values.pop("data_type", "VARCHAR"),
            "description": values.pop("description", ""),
            "description_origin": values.pop("description_origin", None),
            "tags": values.pop("tags", []),
            "is_key": values.pop("is_key", False),
            "is_metric": values.pop("is_metric", False),
            "null_rate": values.pop("null_rate", None),
            "distinct_count": values.pop("distinct_count", None),
            "semantic_type": None,
            "classifications": [],
            "classification_origin": None,
            "copilot_confidence": None,
            "copilot_evidence": {},
        }
        row.update(values)
        self.columns[(dataset, column)] = row

    def seed_from_snapshot(self, dataset: str, snapshot: "FakeSnapshot") -> None:
        """What _update_catalog persists at publication time (stats, no text)."""
        for field in (snapshot.evidence or {}).get("catalog", []):
            key = (dataset, field["name"])
            if key in self.columns:
                self.columns[key].update(
                    data_type=field["type"],
                    null_rate=field.get("null_rate"),
                    distinct_count=field.get("distinct_count"),
                )
            else:
                self.add_column(
                    dataset,
                    field["name"],
                    data_type=field["type"],
                    null_rate=field.get("null_rate"),
                    distinct_count=field.get("distinct_count"),
                )

    def add_edge(self, **edge: Any) -> None:
        row = {"join_hint": "LEFT", "cardinality": None, "confidence": None,
               "basis": {}, "description": "", "origin": "manual", "status": "active"}
        row.update(edge)
        self.edges[edge_key(row)] = row

    def load_states(self, sec, subjects=None):
        with self.lock:
            if subjects is None:
                return copy.deepcopy(self.states)
            return {key: copy.deepcopy(self.states[key]) for key in subjects if key in self.states}

    def load_catalog_columns(self, sec, datasets):
        wanted = set(datasets)
        out: dict[str, dict[str, dict[str, Any]]] = {}
        with self.lock:
            for (dataset, column), row in self.columns.items():
                if dataset in wanted:
                    out.setdefault(dataset, {})[column] = copy.deepcopy(row)
        return out

    def load_edges(self, sec, datasets):
        wanted = set(datasets)
        with self.lock:
            return [
                copy.deepcopy(row)
                for row in self.edges.values()
                if row["from_dataset"] in wanted or row["to_dataset"] in wanted
            ]

    def load_protection(self, sec, sources):
        return dict(self.protection)

    def upsert_column_annotations(self, sec, *, dataset, layer, cartridge, annotations):
        with self.lock:
            for item in annotations:
                assert evidence_is_counts_only(item.get("evidence") or {})
                key = (dataset, item["column"])
                row = self.columns.get(key)
                if row is None:
                    self.add_column(dataset, item["column"], data_type=item.get("data_type"))
                    row = self.columns[key]
                if not str(row.get("description") or "").strip() or row.get(
                    "description_origin"
                ) == "copilot":
                    row["description"] = item["description"]
                    row["description_origin"] = "copilot"
                existing_origin = row.get("classification_origin")
                incoming_origin = item.get("classification_origin") if item.get("classifications") else None
                if existing_origin in (None, "copilot") or (
                    existing_origin == "packaged" and incoming_origin == "packaged"
                ):
                    row["classifications"] = list(item.get("classifications") or [])
                    row["classification_origin"] = incoming_origin
                row["semantic_type"] = item.get("semantic_type")
                row["copilot_evidence"] = dict(item.get("evidence") or {})
                row["copilot_confidence"] = item.get("confidence")
        return WriteResult(len(annotations), 0)

    def upsert_copilot_edges(self, sec, edges):
        written = 0
        if self.fail_edges:
            return WriteResult(0, len(edges))
        with self.lock:
            for edge in edges:
                key = edge_key(edge)
                reverse = (key[2], key[3], key[0], key[1])
                blocked = any(
                    (row := self.edges.get(candidate)) is not None
                    and (row["status"] == "rejected" or row["origin"] in {"manual", "packaged"})
                    for candidate in (key, reverse)
                )
                if blocked:
                    continue
                current = self.edges.get(key)
                if current and not (
                    current["origin"] == "copilot" and current["status"] in {"active", "retired"}
                ):
                    continue
                self.edges[key] = {
                    **{k: edge[k] for k in ("from_dataset", "from_column", "to_dataset", "to_column")},
                    "join_hint": "LEFT",
                    "cardinality": edge.get("cardinality"),
                    "confidence": edge.get("confidence"),
                    "basis": dict(edge.get("basis") or {}),
                    "description": edge.get("description") or "",
                    "origin": "copilot",
                    "status": "active",
                }
                written += 1
        return WriteResult(written, 0)

    def retire_copilot_edges(self, sec, keys):
        retired = 0
        with self.lock:
            for key in keys:
                row = self.edges.get(key)
                if row and row["origin"] == "copilot" and row["status"] == "active":
                    row["status"] = "retired"
                    retired += 1
        return retired

    def reject_edge(self, sec, edge):
        key = edge_key(edge)
        with self.lock:
            row = self.edges.get(key) or {**edge, "join_hint": None, "description": ""}
            row.update({"origin": "manual", "status": "rejected"})
            self.edges[key] = row
        return True

    def save_state(self, sec, state):
        summary = state.get("summary") or {}
        assert not {"values", "examples", "sample", "samples", "min", "max"} & set(summary)
        if self.max_summary_bytes is not None:
            import json

            if len(json.dumps(summary, default=str).encode()) > self.max_summary_bytes:
                raise ValueError("summary exceeds the database bound")
        if self.refuse_source_columns and summary.get("columns"):
            raise ValueError("summary refused by the database")
        with self.lock:
            self.states[(state["subject_kind"], state["subject"])] = {
                **copy.deepcopy(state),
                "profiled_at": datetime.now(timezone.utc),
            }
            self.saved_states += 1

    def annotation_epoch(self, sec):
        with self.lock:
            if not self.states:
                return None
            return max(state["profiled_at"] for state in self.states.values()).isoformat()

    def load_annotations(self, sec, datasets, *, include_sources=False):
        names = set(datasets)
        columns = self.load_catalog_columns(sec, names)
        with self.lock:
            relationships = [
                copy.deepcopy(row)
                for row in self.edges.values()
                if row["from_dataset"] in names or row["to_dataset"] in names
            ]
            subjects = {
                key: copy.deepcopy(state)
                for key, state in self.states.items()
                if key[0] == "dataset" and key[1] in names
            }
            sources = [
                {**copy.deepcopy(state), "subject_kind": key[0], "subject": key[1]}
                for key, state in self.states.items()
                if include_sources and key[0] == "bronze_source"
            ]
        return {
            "columns": columns,
            "relationships": relationships,
            "subjects": subjects,
            "sources": sources,
        }


class LocalEngine:
    """A real DuckDBEngine whose storage resolves to a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.engine = DuckDBEngine()
        self.engine._con = duckdb.connect()
        self.engine._storage_uri = lambda key: str(root / str(key or "").strip("/"))
        self.engine._pg_gold_attach = lambda con, ctx=None: "pggold"
        self.heads: dict[str, dict[str, Any]] = {}
        self.engine._published_dataset_head = self._head
        self.engine._published_sql = lambda ds, head: (
            "SELECT * FROM read_parquet('" + head["object_uri"].replace("'", "''") + "')"
        )

    def _head(self, dataset: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        head = self.heads.get(str(dataset.get("name")))
        if head is None:
            raise RuntimeError("dataset is not published")
        return head

    def publish(self, name: str, sql: str, *, run_id: str = "run-1") -> FakeSnapshot:
        path = self.root / "published" / f"{name}-{run_id}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        con = self.engine._con
        con.sql(sql).write_parquet(str(path))
        published = con.read_parquet(str(path))
        rows = published.shape[0]
        schema = list(zip(published.columns, [str(t) for t in published.types]))
        stats = self.engine._profile_columns(con, "read_parquet(" + repr(str(path)) + ")")
        catalog = []
        for column, data_type in schema:
            field = {"name": column, "type": data_type}
            stat = stats.get(column) or {}
            field["null_rate"] = stat.get("null_rate")
            field["distinct_count"] = stat.get("distinct_count")
            field["min_value"] = stat.get("min")
            field["max_value"] = stat.get("max")
            catalog.append(field)
        head = {
            "materialization_run_id": run_id,
            "generation": 1,
            "status": "published",
            "row_count": int(rows),
            "schema_digest": f"digest-{name}-{len(schema)}",
            "published_at": datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc),
            "object_uri": str(path),
        }
        self.heads[name] = head
        return FakeSnapshot(head=head, evidence={"catalog": catalog})


class FakeHost:
    def __init__(self, local: LocalEngine) -> None:
        self.local = local
        self.datasets: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, FakeSnapshot] = {}
        self.sources: list[str] = []

    def add(self, name: str, sql: str, *, cartridge: str = "sap_successfactors",
            layer: str = "silver", sources: list[str] | None = None,
            run_id: str = "run-1") -> FakeSnapshot:
        snapshot = self.local.publish(name, sql, run_id=run_id)
        self.datasets[name] = {
            "name": name,
            "layer": layer,
            "cartridge": cartridge,
            "sources": sources or [],
            "row_count": snapshot.head["row_count"],
            "workspace_id": WORKSPACE,
        }
        self.snapshots[name] = snapshot
        return snapshot

    def list_datasets(self, sec):
        return [dict(ds) for ds in self.datasets.values()]

    def get_dataset(self, sec, name):
        ds = self.datasets.get(name)
        return dict(ds) if ds else None

    def published_snapshots(self, datasets, sec):
        return [self.snapshots.get(str(ds.get("name"))) for ds in datasets]

    def list_sources(self, sec):
        return list(self.sources)

    def source_allowed(self, sec, source):
        return source in self.sources


EMPLOYEES_SQL = """
SELECT
    'EMP' || lpad(CAST(i AS VARCHAR), 6, '0') AS employee_id,
    'persona' || i || '@empresa.com.mx' AS email,
    'GODE' || lpad(CAST(800101 + (i % 300) AS VARCHAR), 6, '0') || 'AB' || CAST(i % 10 AS VARCHAR) AS rfc,
    'Nombre' || (i % 97) AS first_name,
    'Apellido' || (i % 89) AS last_name,
    'D' || lpad(CAST(i % 40 AS VARCHAR), 3, '0') AS department_id,
    DATE '2015-01-01' + CAST(i % 3000 AS INTEGER) AS hire_date,
    CAST(10000 + (i % 5000) * 3.5 AS DECIMAL(18, 2)) AS salary_amount,
    (i % 2 = 0) AS is_active,
    CASE WHEN i % 3 = 0 THEN 'Activo' WHEN i % 3 = 1 THEN 'Baja' ELSE 'Licencia' END AS status,
    CAST(i % 13 AS INTEGER) AS job_level,
    CAST((i % 100) / 100.0 AS DOUBLE) AS turnover_rate,
    'Loc' || (i % 25) AS location_name,
    CASE WHEN i % 5 = 0 THEN NULL ELSE 'Proyecto ' || (i % 50) END AS project_name,
    CAST(i % 60 AS INTEGER) AS tenure_months,
    'MX' AS country,
    'Turno ' || (i % 3) AS shift,
    CAST(i * 1.0 AS DOUBLE) AS score,
    'Nivel ' || (i % 7) AS grade,
    CAST(i % 12 AS INTEGER) AS month_number,
    'Equipo ' || (i % 30) AS team,
    DATE '2026-01-01' + CAST(i % 200 AS INTEGER) AS review_date,
    'Comentario ' || (i % 17) AS notes,
    CAST(i % 1000 AS INTEGER) AS badge_number,
    'Centro ' || (i % 11) AS site_label,
    CAST(i % 4 AS INTEGER) AS children,
    'Plan ' || (i % 6) AS benefit_plan,
    CAST(i % 8 AS INTEGER) AS vacation_days,
    'Cat ' || (i % 9) AS category,
    CAST(i % 365 AS INTEGER) AS day_of_year
FROM range(10000) t(i)
"""

DEPARTMENTS_SQL = """
SELECT
    'D' || lpad(CAST(i AS VARCHAR), 3, '0') AS department_id,
    'Departamento ' || i AS department_name
FROM range(40) t(i)
"""


__all__ = [
    "DEPARTMENTS_SQL",
    "EMPLOYEES_SQL",
    "FakeCopilotStore",
    "FakeHost",
    "FakeSnapshot",
    "LocalEngine",
    "SEC",
    "TENANT",
    "WORKSPACE",
]
