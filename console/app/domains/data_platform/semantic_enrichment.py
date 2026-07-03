"""Semantic catalog enrichment helpers."""

from __future__ import annotations

import re
from typing import Any


SEMANTIC_WEAK_DESCRIPTIONS = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "null",
    "pendiente",
    "sin descripcion",
    "sin descripción",
    "sin detalle",
}


def semantic_humanize_identifier(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def semantic_description_is_missing(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text.lower() in SEMANTIC_WEAK_DESCRIPTIONS


def semantic_column_traits(
    column_name: str, data_type: str | None = None
) -> dict[str, Any]:
    col = str(column_name or "").strip()
    lowered = semantic_humanize_identifier(col)
    words = set(lowered.split())
    data_type_text = str(data_type or "").strip().lower()
    is_key = (
        lowered == "id"
        or lowered.endswith(" id")
        or lowered.endswith(" key")
        or lowered in {"uuid", "entity uuid", "external code"}
    )
    is_date = bool(
        words & {"date", "time", "fecha", "created", "modified", "updated", "at"}
    ) or any(token in lowered for token in ("datetime", "timestamp"))
    is_status = any(token in lowered for token in ("status", "state", "estado", "active"))
    is_amount = any(
        token in lowered
        for token in ("amount", "salary", "pay", "cost", "revenue", "valor", "importe")
    )
    is_count = any(
        token in lowered for token in ("count", "total", "qty", "quantity", "filas", "records")
    )
    is_score = any(
        token in lowered
        for token in ("score", "rating", "percent", "pct", "ratio", "rate", "confidence")
    )
    is_metric = is_amount or is_count or is_score or data_type_text in {
        "int",
        "integer",
        "bigint",
        "float",
        "double",
        "decimal",
        "numeric",
    }
    tags = ["semantic_enrichment", "auto_described"]
    if is_key:
        tags.append("key")
    if is_date:
        tags.append("date")
    if is_status:
        tags.append("status")
    if is_metric:
        tags.append("metric")
    return {
        "label": lowered or col,
        "is_key": is_key,
        "is_metric": is_metric,
        "is_date": is_date,
        "is_status": is_status,
        "tags": tags,
    }


def semantic_build_column_description(
    *, dataset: str, layer: str | None, column_name: str, data_type: str | None = None
) -> tuple[str, dict[str, Any]]:
    traits = semantic_column_traits(column_name, data_type)
    dataset_label = semantic_humanize_identifier(dataset)
    layer_label = str(layer or "").strip().lower() or "catalogada"
    column_label = traits["label"]
    if traits["is_key"]:
        description = (
            f"Identificador de {column_label} usado para relacionar registros del dataset "
            f"{dataset_label} en la capa {layer_label}."
        )
    elif traits["is_date"]:
        description = (
            f"Fecha o marca temporal asociada a {column_label} dentro del dataset "
            f"{dataset_label}; se usa para ordenar, filtrar o auditar cambios."
        )
    elif traits["is_status"]:
        description = (
            f"Estado operativo de {column_label} en {dataset_label}; permite segmentar "
            "registros activos, cerrados, bloqueados o pendientes segun el origen."
        )
    elif traits["is_metric"]:
        description = (
            f"Metrica o valor cuantitativo de {column_label} en {dataset_label}; se usa "
            "para agregaciones, KPIs y analisis operativo."
        )
    else:
        description = (
            f"Atributo descriptivo de {column_label} proveniente de {dataset_label}; "
            "aporta contexto de negocio para analisis y busqueda semantica."
        )
    return description, traits


def semantic_enrichment_candidates(
    catalog: dict[str, Any], *, cartridge: str, limit: int
) -> dict[str, Any]:
    datasets = catalog.get("datasets") if isinstance(catalog, dict) else {}
    if not isinstance(datasets, dict):
        datasets = {}

    scanned_datasets = 0
    scanned_columns = 0
    entries: list[dict[str, Any]] = []
    for dataset_name in sorted(datasets):
        dataset_meta = datasets.get(dataset_name) or {}
        if not isinstance(dataset_meta, dict):
            continue
        ds_cartridge = str(dataset_meta.get("cartridge") or "").strip()
        if cartridge and ds_cartridge and ds_cartridge != cartridge:
            continue
        scanned_datasets += 1
        layer = str(dataset_meta.get("layer") or "").strip()
        columns = dataset_meta.get("columns") or []
        if not isinstance(columns, list):
            continue
        for column in columns:
            if len(entries) >= limit:
                break
            scanned_columns += 1
            if isinstance(column, str):
                column_name = column
                data_type = ""
                existing_description = ""
                existing_tags: list[str] = []
            elif isinstance(column, dict):
                column_name = str(
                    column.get("name")
                    or column.get("column")
                    or column.get("field")
                    or column.get("column_name")
                    or ""
                ).strip()
                data_type = str(column.get("type") or column.get("data_type") or "").strip()
                existing_description = column.get("description")
                existing_tags = column.get("tags") if isinstance(column.get("tags"), list) else []
            else:
                continue
            if not column_name or not semantic_description_is_missing(existing_description):
                continue
            description, traits = semantic_build_column_description(
                dataset=dataset_name,
                layer=layer,
                column_name=column_name,
                data_type=data_type,
            )
            tags = sorted(
                {
                    *(str(tag) for tag in existing_tags if str(tag).strip()),
                    *traits["tags"],
                    f"cartridge:{cartridge}",
                }
            )
            entries.append(
                {
                    "dataset": dataset_name,
                    "column_name": column_name,
                    "description": description,
                    "tags": tags,
                    "is_key": traits["is_key"],
                    "is_metric": traits["is_metric"],
                    "cartridge": cartridge,
                }
            )
        if len(entries) >= limit:
            break

    return {
        "entries": entries,
        "scanned_datasets": scanned_datasets,
        "scanned_columns": scanned_columns,
    }


def semantic_enrichment_limit(
    body: dict[str, Any] | None, *, default: int = 80, maximum: int = 200
) -> int:
    try:
        limit = int((body or {}).get("limit") or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


def semantic_enrichment_empty_response(
    *, cartridge: str, candidate_payload: dict[str, Any]
) -> dict[str, Any]:
    return {
        "ok": True,
        "cartridge": cartridge,
        "mode": "direct_semantic_enrichment",
        "approval_required": False,
        "enriched": 0,
        "candidate_count": 0,
        "scanned_datasets": candidate_payload["scanned_datasets"],
        "scanned_columns": candidate_payload["scanned_columns"],
        "message": "No hay columnas pendientes de descripción en el catálogo visible.",
    }


def semantic_enrichment_success_response(
    *,
    cartridge: str,
    candidate_payload: dict[str, Any],
    result: dict[str, Any] | Any,
) -> dict[str, Any]:
    entries = candidate_payload["entries"]
    updated = int(result.get("updated") or 0) if isinstance(result, dict) else 0
    return {
        "ok": True,
        "cartridge": cartridge,
        "mode": "direct_semantic_enrichment",
        "approval_required": False,
        "candidate_count": len(entries),
        "enriched": updated,
        "scanned_datasets": candidate_payload["scanned_datasets"],
        "scanned_columns": candidate_payload["scanned_columns"],
        "entries_preview": entries[:5],
        "result": result,
    }
