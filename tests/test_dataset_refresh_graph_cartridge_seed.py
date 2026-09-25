from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from airflow.dags.dataset_refresh_graph import _build_plan, _validate_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
HEADER_RE = re.compile(r"^--\s*(\w+)\s+\((silver|gold)\)\s+cartridge:\s*([\w-]+)", re.M)
SOURCES_RE = re.compile(r"^-- sources:\s*(\[.*\])\s*$", re.M)


def _graph(*cartridges: str) -> dict[str, dict]:
    graph = {}
    for cartridge in cartridges:
        for path in sorted((REPO_ROOT / "cartridges" / cartridge / "datasets").glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            name, layer, owner = HEADER_RE.search(sql).groups()
            graph[name] = {
                "layer": layer,
                "cartridge": owner,
                "sources": json.loads(SOURCES_RE.search(sql).group(1)),
            }
    return graph


def test_cartridge_seed_plans_every_sap_b1_dataset_in_dependency_order():
    graph = _graph("sap_b1", "hubspot")
    plan = _build_plan(graph, seed_raw="raw/sap_b1/*", seed_dataset="", maximum_depth=10)
    _validate_plan(plan, "sap_b1")

    sap_b1 = {name for name, info in graph.items() if info["cartridge"] == "sap_b1"}
    rank = {item["name"]: item["rank"] for item in plan}
    assert set(rank) == sap_b1
    assert len(plan) == len(sap_b1)
    for name in sap_b1:
        for source in graph[name]["sources"]:
            if source.startswith("silver/"):
                assert rank[name] > rank[source.split("/")[2]]
    assert [item["rank"] for item in plan] == sorted(item["rank"] for item in plan)


def test_single_entity_seed_still_plans_only_its_dependents():
    plan = _build_plan(_graph("sap_b1"), seed_raw="raw/sap_b1/OINV", seed_dataset="", maximum_depth=10)
    names = {item["name"] for item in plan}

    assert "sap_b1_ar_invoice_lines" in names
    assert "sap_b1_sales_by_company_month" in names
    assert "sap_b1_journal_lines" not in names


@pytest.mark.parametrize("seed", ["raw/*", "raw/sap_b1/OINV/*", "raw/absent/*"])
def test_invalid_or_unknown_cartridge_seed_is_rejected(seed):
    with pytest.raises(ValueError):
        _build_plan(_graph("sap_b1"), seed_raw=seed, seed_dataset="", maximum_depth=10)
