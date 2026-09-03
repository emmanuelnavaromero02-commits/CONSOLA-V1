from __future__ import annotations

from typing import Any

from app.services.intelligence.utils import public_json, sample_hash


def dataset_evidence_pack(
    *,
    dataset: str,
    id_field: str,
    entity_id: str,
    time_field: str,
    value_field: str,
    latest: dict[str, Any],
    history_values: list[float],
    method: str,
    confidence: float,
    source_system: str | None = None,
    gold_table: str | None = None,
    freshness_at: str | None = None,
    external_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    table = gold_table or f"gold_{dataset}"
    row_sample = {
        "latest": public_json(latest),
        "history_values": [round(value, 4) for value in history_values],
    }
    source_metadata = {
        "source_system": source_system,
        "dataset": dataset,
        "gold_table": table,
        "freshness_at": freshness_at,
        "freshness_field": time_field,
        "value_field": value_field,
        "entity_id": entity_id,
    }
    items = [
        {
            # This collector reads the server-resolved published Gold relation;
            # it is not a generic/user supplied dataset citation.
            "source_type": "gold",
            "source_ref": dataset,
            "query_text": (
                f"SELECT {time_field}, {id_field}, {value_field} "
                f"FROM {table} WHERE {id_field} = $entity_id ORDER BY {time_field}"
            ),
            "data": {
                **row_sample,
                "row_count": len(history_values) + 1,
                "history_window": len(history_values),
                "sample_hash": sample_hash(row_sample),
                "source_system": source_system,
                "dataset": dataset,
                "gold_table": table,
                "freshness_at": freshness_at,
            },
            "supports_hypothesis": "baseline_deviation",
            "strength": confidence,
            "metadata": source_metadata,
        }
    ]
    items.extend(external_items or [])
    external_count = len([item for item in items if item.get("source_type") == "external"])
    summary = f"Baseline {method} con {len(history_values)} muestras historicas."
    if external_count:
        summary += f" Contexto externo: {external_count} fuente(s) revisada(s)."
    return {
        "summary": summary,
        "confidence": confidence,
        "items": items,
        "metadata": source_metadata,
        "freshness_at": freshness_at,
        "source_system": source_system,
        "dataset": dataset,
        "gold_table": table,
    }
