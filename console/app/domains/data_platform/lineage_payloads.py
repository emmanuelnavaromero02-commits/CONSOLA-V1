from __future__ import annotations

from collections.abc import Callable, Container
from typing import Any


def dataset_source_match(source: str, names: Container[str]) -> str | None:
    """Return the dataset name a non-raw source refers to, or None."""
    source = (source or "").strip()
    if not source or source.lower().startswith("raw/"):
        return None
    candidates = [
        source,
        source.replace("silver_", "", 1),
        source.replace("gold_", "", 1),
    ]
    if "/" in source:
        candidates.append(source.rsplit("/", 1)[-1])
    return next((candidate for candidate in candidates if candidate in names), None)


def lineage_graph_payload(
    datasets: list[dict[str, Any]],
    *,
    source_visible: Callable[[str], bool],
) -> dict[str, list[dict[str, Any]]]:
    by_name = {d["name"]: d for d in datasets if d.get("name")}
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    for dataset in datasets:
        name = dataset.get("name")
        if not name:
            continue
        node_id = f"ds:{name}"
        nodes[node_id] = {
            "id": node_id,
            "label": name,
            "type": dataset.get("layer", "silver"),
            "cartridge": dataset.get("cartridge", ""),
            "is_stale": bool(dataset.get("is_stale")),
            "staleness_reason": dataset.get("staleness_reason"),
            "row_count": dataset.get("row_count"),
            "last_refresh": dataset.get("last_refresh"),
        }
        for raw_source in dataset.get("sources") or []:
            source = (raw_source or "").strip()
            if not source_visible(source):
                continue
            source_lower = source.lower()
            if source_lower.startswith("raw/"):
                raw_id = f"raw:{source[4:]}"
                if raw_id not in nodes:
                    parts = source[4:].split("/", 1)
                    nodes[raw_id] = {
                        "id": raw_id,
                        "label": parts[-1] if parts else source,
                        "type": "raw",
                        "cartridge": parts[0] if len(parts) > 1 else "",
                    }
                edges.append({"from": raw_id, "to": node_id})
                continue
            matched = dataset_source_match(source, by_name)
            if matched:
                edges.append({"from": f"ds:{matched}", "to": node_id})

    return {"nodes": list(nodes.values()), "edges": edges}
