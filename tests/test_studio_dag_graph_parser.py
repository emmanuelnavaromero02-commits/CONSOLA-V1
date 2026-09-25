from __future__ import annotations

import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.main import _parse_dag_graph  # noqa: E402


def test_studio_parser_links_nested_taskflow_calls():
    source = """
from airflow.decorators import dag, task

@dag(schedule=None, catchup=False)
def hubspot_extract():
    @task
    def extract():
        return {"entity": "deals"}

    @task
    def trigger_refresh_chain(result):
        return result

    trigger_refresh_chain(extract())

dag = hubspot_extract()
"""

    result = _parse_dag_graph(source)

    assert result.get("error") is None
    assert [task["id"] for task in result["tasks"]] == ["extract", "trigger_refresh_chain"]
    assert ["extract", "trigger_refresh_chain"] in result["edges"]


def test_studio_parser_accepts_seeded_airflow_and_cartridge_dags():
    dag_paths = [
        REPO / "airflow/dags/agent_runner.py",
        REPO / "airflow/dags/dataset_refresh_chain.py",
        REPO / "airflow/dags/entity_scheduler.py",
        REPO / "airflow/dags/file_ingest.py",
        REPO / "cartridges/hubspot/dags/hubspot_extract.py",
        REPO / "cartridges/hubspot/dags/hubspot_extract_all.py",
    ]

    failures: list[str] = []
    for path in dag_paths:
        result = _parse_dag_graph(path.read_text(encoding="utf-8"))
        if result.get("error"):
            failures.append(f"{path.relative_to(REPO)}: {result['error']}")
        assert result["tasks"], f"{path.relative_to(REPO)} should expose at least one Studio graph task"

    assert failures == []


def test_studio_frontend_renders_graph_from_nodes_and_edges_only():
    component = (REPO / "console-next/src/components/studio/DagGraph.tsx").read_text(encoding="utf-8")
    client = (REPO / "console-next/src/lib/studio/client.ts").read_text(encoding="utf-8")

    assert "layoutDagGraph(" in component
    assert "dangerouslySetInnerHTML" not in component
    assert ".svg" not in component
    graph_fn = client.split("export async function getDagGraph(", 1)[1].split("\n}\n", 1)[0]
    assert "svg" not in graph_fn
