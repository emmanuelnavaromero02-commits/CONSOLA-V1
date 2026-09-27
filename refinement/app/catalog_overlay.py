"""Live catalog annotations layered over the attested publication snapshot.

The snapshot (publication evidence) stays the authority for structural facts:
columns, types, row counts and profile statistics. Descriptions, tags,
classifications and relationships are read live so a manual edit or a
Copilot annotation shows immediately, without a new publication. Evidence
itself is never modified here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

try:
    from app.catalog_copilot_rules import (
        CLASSIFICATIONS,
        SEMANTIC_TYPES,
        SENSITIVE_STATS,
        classify_column,
        semantic_type,
    )
except ModuleNotFoundError:
    from refinement.app.catalog_copilot_rules import (
        CLASSIFICATIONS,
        SEMANTIC_TYPES,
        SENSITIVE_STATS,
        classify_column,
        semantic_type,
    )

_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,600}$")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_BASIS = re.compile(r"^[a-z_]+:[a-z0-9_.:]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ORIGINS = frozenset({"manual", "packaged", "copilot"})
_JOIN_HINTS = frozenset({"INNER", "LEFT", "RIGHT", "FULL"})
_CARDINALITIES = frozenset({"1:1", "1:N", "N:1", "N:N"})
_STAT_KEYS = ("min_value", "max_value")
_MAX_BASIS = 12


@dataclass(frozen=True)
class CatalogAnnotations:
    columns: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    subjects: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False

    @classmethod
    def unavailable(cls) -> "CatalogAnnotations":
        return cls(degraded=True)


def _text(value: object) -> str:
    text = str(value or "").strip()
    return text if _TEXT.fullmatch(text) else ""


def _origin(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in _ORIGINS else None


def _classifications(value: object) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else []
    wanted = {str(item) for item in items}
    return [name for name in CLASSIFICATIONS if name in wanted]


def _basis_codes(evidence: object) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    codes = evidence.get("basis")
    if not isinstance(codes, list):
        return []
    return [str(code) for code in codes if _BASIS.fullmatch(str(code))][:_MAX_BASIS]


def _confidence(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number < 0 or number > 1:
        return None
    return round(number, 3)


def _redact(column: dict[str, Any]) -> None:
    for key in _STAT_KEYS:
        column.pop(key, None)
    if "example_values" in column:
        column["example_values"] = []
    column["stats_redacted"] = True


def _apply_column(column: dict[str, Any], live: dict[str, Any] | None) -> None:
    if live:
        description = _text(live.get("description"))
        if description:
            column["description"] = description
            column["description_origin"] = _origin(live.get("description_origin")) or "manual"
        tags = [str(tag) for tag in list(live.get("tags") or [])[:16] if _TAG.fullmatch(str(tag))]
        if tags:
            column["tags"] = tags
        if live.get("is_key") is not None:
            column["is_key"] = bool(live.get("is_key"))
        if live.get("is_metric") is not None:
            column["is_metric"] = bool(live.get("is_metric"))
        semantic = str(live.get("semantic_type") or "")
        if semantic in SEMANTIC_TYPES:
            column["semantic_type"] = semantic
        classes = _classifications(live.get("classifications"))
        column["classifications"] = classes
        origin = _origin(live.get("classification_origin"))
        if classes and origin:
            column["classification_origin"] = origin
        confidence = _confidence(live.get("copilot_confidence"))
        if confidence is not None:
            column["copilot_confidence"] = confidence
        basis = _basis_codes(live.get("copilot_evidence"))
        if basis:
            column["copilot_basis"] = basis
            if "key:exact" in basis:
                column["is_key"] = True
    sensitive = set(column.get("classifications") or []) & SENSITIVE_STATS
    if not sensitive and not (live and live.get("classifications")):
        # Unprofiled column: still hide literal min/max when the name alone
        # says the column is personal or financial.
        inferred = classify_column(
            str(column.get("name") or ""),
            semantic_type(str(column.get("type") or ""), str(column.get("name") or "")),
        )
        sensitive = set(inferred.classifications) & SENSITIVE_STATS
    if sensitive:
        _redact(column)


def _relationship_key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("from_dataset") or ""),
        str(value.get("from_column") or ""),
        str(value.get("to_dataset") or ""),
        str(value.get("to_column") or ""),
    )


def _live_relationship(row: dict[str, Any]) -> dict[str, Any] | None:
    key = _relationship_key(row)
    if not all(_IDENTIFIER.fullmatch(part) for part in key):
        return None
    join_hint = str(row.get("join_hint") or "").upper()
    cardinality = str(row.get("cardinality") or "")
    out: dict[str, Any] = {
        "from_dataset": key[0],
        "from_column": key[1],
        "to_dataset": key[2],
        "to_column": key[3],
        "join_hint": join_hint if join_hint in _JOIN_HINTS else "",
        "description": _text(row.get("description")),
        "origin": _origin(row.get("origin")) or "manual",
        "status": "active",
    }
    if cardinality in _CARDINALITIES:
        out["cardinality"] = cardinality
    confidence = _confidence(row.get("confidence"))
    if confidence is not None:
        out["confidence"] = confidence
    basis = row.get("basis")
    if isinstance(basis, dict):
        codes = [
            str(code)
            for code in basis.get("codes") or []
            if _BASIS.fullmatch(str(code))
        ][:_MAX_BASIS]
        if codes:
            out["basis"] = codes
    return out


def _copilot_block(state: dict[str, Any]) -> dict[str, Any]:
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    profiled_at = state.get("profiled_at")

    def _count(key: str) -> int:
        value = summary.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
        return 0

    return {
        "status": str(state.get("status") or "")
        if state.get("status") in {"ready", "partial", "failed"}
        else "failed",
        "profiled_at": profiled_at.isoformat()
        if hasattr(profiled_at, "isoformat")
        else (str(profiled_at) if profiled_at else None),
        "rules_version": _text(state.get("rules_version")) or None,
        "pii_columns": _count("pii_columns"),
        "financial_columns": _count("financial_columns"),
        "relations": _count("relations"),
    }


def _source_entry(state: dict[str, Any]) -> dict[str, Any]:
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    columns = []
    for item in summary.get("columns") or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        column: dict[str, Any] = {
            "name": name,
            "type": _text(item.get("type")),
            "description": _text(item.get("description")),
            "description_origin": "copilot" if _text(item.get("description")) else None,
            "tags": [],
            "is_key": False,
            "is_metric": False,
            "example_values": [],
            "classifications": _classifications(item.get("classifications")),
            "stats_redacted": bool(
                set(_classifications(item.get("classifications"))) & SENSITIVE_STATS
            ),
        }
        semantic = str(item.get("semantic_type") or "")
        if semantic in SEMANTIC_TYPES:
            column["semantic_type"] = semantic
        null_rate = item.get("null_rate")
        if isinstance(null_rate, (int, float)) and not isinstance(null_rate, bool):
            column["null_rate"] = round(float(null_rate), 6)
        if column["classifications"]:
            column["classification_origin"] = _origin(item.get("classification_origin")) or "copilot"
            confidence = _confidence(item.get("confidence"))
            if confidence is not None:
                column["copilot_confidence"] = confidence
            codes = [
                str(code) for code in item.get("basis") or [] if _BASIS.fullmatch(str(code))
            ][:_MAX_BASIS]
            if codes:
                column["copilot_basis"] = codes
        columns.append(column)
    rows = summary.get("rows")
    return {
        "layer": "bronze",
        "cartridge": _text(state.get("cartridge")),
        "row_count": rows if isinstance(rows, int) and not isinstance(rows, bool) else None,
        "last_refresh": _text(summary.get("load_date")) or None,
        "description": _text(state.get("description")),
        "description_origin": "copilot" if _text(state.get("description")) else None,
        "display_name": _text(state.get("display_name")) or None,
        "kind": "bronze_source",
        "columns": columns,
        "copilot": _copilot_block(state),
    }


def apply_catalog_annotations(
    output: dict[str, Any],
    annotations: CatalogAnnotations,
    *,
    visible: set[str],
    include_sources: bool = False,
    layer: str | None = None,
    cartridge: str | None = None,
    tags: list[str] | None = None,
    datasets: list[str] | None = None,
) -> dict[str, Any]:
    result_datasets: dict[str, Any] = {}
    for name, entry in dict(output.get("datasets") or {}).items():
        dataset = dict(entry)
        live_columns = annotations.columns.get(name, {})
        columns = []
        for value in dataset.get("columns") or []:
            column = dict(value)
            _apply_column(column, live_columns.get(str(column.get("name") or "")))
            columns.append(column)
        dataset["columns"] = columns
        dataset["kind"] = "dataset"
        state = annotations.subjects.get(("dataset", name))
        if dataset.get("description"):
            dataset["description_origin"] = "manual"
        elif state and _text(state.get("description")):
            dataset["description"] = _text(state.get("description"))
            dataset["description_origin"] = "copilot"
        if state:
            dataset["display_name"] = _text(state.get("display_name")) or None
            dataset["copilot"] = _copilot_block(state)
        result_datasets[name] = dataset

    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for value in output.get("relationships") or []:
        if isinstance(value, dict):
            relation = dict(value)
            relation.setdefault("origin", "manual")
            relation.setdefault("status", "active")
            merged[_relationship_key(relation)] = relation
    for row in annotations.relationships:
        key = _relationship_key(row)
        status = str(row.get("status") or "active")
        if status != "active":
            merged.pop(key, None)
            continue
        if key[0] not in result_datasets:
            continue
        live = _live_relationship(row)
        if live is not None:
            merged[key] = live
    relationships = [
        relation
        for key, relation in sorted(merged.items())
        if key[0] in visible and key[2] in visible
    ]

    if include_sources and not tags and (not layer or layer == "bronze"):
        for state in annotations.sources:
            subject = str(state.get("subject") or "")
            if not subject:
                continue
            if cartridge and str(state.get("cartridge") or "") != cartridge:
                continue
            if datasets and subject not in datasets:
                continue
            result_datasets[subject] = _source_entry(state)

    out = {**output, "datasets": result_datasets, "relationships": relationships}
    if annotations.degraded:
        out["annotations_degraded"] = True
    return out


__all__ = ["CatalogAnnotations", "apply_catalog_annotations"]
