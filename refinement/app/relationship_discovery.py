from __future__ import annotations

from typing import Any


def _normalize_name(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _normalize_type(data_type: str | None) -> str:
    t = str(data_type or "").strip().lower()
    if not t:
        return ""
    if any(k in t for k in ("char", "text", "string", "uuid")):
        return "text"
    if any(k in t for k in ("int", "serial", "bigint", "smallint")):
        return "int"
    if any(k in t for k in ("numeric", "decimal", "double", "real", "float")):
        return "number"
    if any(k in t for k in ("date", "time")):
        return "temporal"
    if "bool" in t:
        return "bool"
    return t


def _is_key(column: dict[str, Any], row_count: int | None) -> bool:
    distinct = column.get("distinct_count")
    null_rate = column.get("null_rate")
    return bool(
        distinct is not None
        and isinstance(row_count, int)
        and row_count > 0
        and distinct == row_count
        and (null_rate == 0 or null_rate == 0.0)
    )


def discover_relationship_candidates(
    columns: list[dict[str, Any]],
    row_counts: dict[str, Any],
    *,
    min_key_cardinality: int = 2,
) -> list[dict[str, Any]]:
    keys_by_signature: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for col in columns:
        dataset = str(col.get("dataset") or "")
        if not dataset:
            continue
        rc = row_counts.get(dataset)
        if not _is_key(col, rc):
            continue
        distinct = int(col.get("distinct_count"))
        if distinct < min_key_cardinality:
            continue
        sig = (_normalize_name(col.get("column_name") or ""), _normalize_type(col.get("data_type")))
        keys_by_signature.setdefault(sig, []).append(col)

    candidates: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    for col in columns:
        from_dataset = str(col.get("dataset") or "")
        from_column = str(col.get("column_name") or "")
        if not from_dataset or not from_column:
            continue
        from_distinct = col.get("distinct_count")
        sig = (_normalize_name(from_column), _normalize_type(col.get("data_type")))
        for key in keys_by_signature.get(sig, []):
            to_dataset = str(key.get("dataset") or "")
            to_column = str(key.get("column_name") or "")
            if to_dataset == from_dataset:
                continue
            key_distinct = key.get("distinct_count")
            if (
                from_distinct is not None
                and key_distinct is not None
                and from_distinct > key_distinct
            ):
                continue
            edge = (from_dataset, from_column, to_dataset, to_column)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            exact_name = from_column.lower() == to_column.lower()
            child_is_not_key = not _is_key(col, row_counts.get(from_dataset))
            confidence = 0.6
            if exact_name:
                confidence += 0.2
            if child_is_not_key:
                confidence += 0.1
            candidates.append(
                {
                    "from_dataset": from_dataset,
                    "from_column": from_column,
                    "to_dataset": to_dataset,
                    "to_column": to_column,
                    "join_hint": "many_to_one",
                    "confidence": round(min(confidence, 0.9), 3),
                    "basis": "profiler_key_and_cardinality",
                    "status": "candidate",
                    "description": (
                        f"Candidato automático: {from_column} referencia la llave única "
                        f"{to_column} de {to_dataset} (nombre+tipo+cardinalidad). "
                        "Confirmar con contención de valores antes de usar."
                    ),
                }
            )

    candidates.sort(key=lambda c: (-c["confidence"], c["from_dataset"], c["from_column"]))
    return candidates
