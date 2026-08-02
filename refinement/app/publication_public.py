from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

try:
    from app.publication_contract import PublicationScope
    from app.publication_evidence import PublicationEvidenceStore
    from app.publication_reader import PublicationReader
except ModuleNotFoundError:
    from refinement.app.publication_contract import PublicationScope
    from refinement.app.publication_evidence import PublicationEvidenceStore
    from refinement.app.publication_reader import PublicationReader


def _context(security_context: dict[str, Any] | None) -> dict[str, str]:
    value = security_context or {}
    return {
        "tenant_id": str(value.get("tenant_id") or "").strip(),
        "workspace_id": str(value.get("workspace_id") or "").strip(),
    }


def _scope(ds: dict[str, Any], context: dict[str, str]) -> PublicationScope:
    return PublicationScope(
        context["tenant_id"],
        context["workspace_id"],
        str(ds["name"]),
        str(ds.get("layer") or "silver"),
    )


_PUBLIC_SOURCE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_PUBLIC_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")
_PUBLIC_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def _public_source_entity(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or any(ord(char) < 32 for char in raw):
        return ""
    parsed = urlsplit(raw)
    path = parsed.path.lstrip("/") if parsed.scheme else raw.strip("/")
    parts = path.split("/")
    if len(parts) >= 3 and parts[0].lower() in {"raw", "silver", "gold"}:
        candidate = parts[2]
    elif len(parts) == 1:
        candidate = parts[0]
    else:
        return ""
    return candidate if _PUBLIC_SOURCE.fullmatch(candidate) else ""


def _public_text(value: object) -> str:
    text = str(value or "").strip()
    return text if _PUBLIC_TEXT.fullmatch(text) else ""


def _public_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(tag) for tag in value[:16] if _PUBLIC_TAG.fullmatch(str(tag))]


def _public_examples(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return [
        item for item in value[:20] if item is None or type(item) in {bool, int, float}
    ]


def _public_relationship(value: object, *, from_dataset: str) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: _public_text(value.get(key))
        for key in (
            "from_column",
            "to_dataset",
            "to_column",
            "join_hint",
            "description",
        )
    }
    result["from_dataset"] = (
        from_dataset if _PUBLIC_SOURCE.fullmatch(from_dataset) else ""
    )
    required = ("from_dataset", "from_column", "to_dataset", "to_column")
    return result if all(result[key] for key in required) else None


def published_lineage(
    ds: dict[str, Any], security_context: dict[str, Any] | None
) -> dict[str, Any]:
    context = _context(security_context)
    name = str(ds.get("name") or "")
    if not context["tenant_id"] or not context["workspace_id"]:
        return {"name": name, "lineage": []}
    scope = _scope(ds, context)
    head = PublicationReader().published_head(
        scope.layer, str(ds.get("cartridge") or ""), name, context
    )
    if not head or head.get("status") == "legacy_unverified":
        return {"name": name, "lineage": []}
    evidence = PublicationEvidenceStore().read_exact(
        str(head["materialization_run_id"]), scope
    )
    if not evidence:
        return {"name": name, "lineage": [], "degraded": True}
    source = dict(evidence["lineage"])
    lineage = {
        "source_entity": _public_source_entity(source.get("source_entity")),
        "source_load_date": source.get("source_load_date") or "",
        "source_batch_id": source.get("source_batch_id") or "",
    }
    lineage.update(
        {
            "silver_name": name,
            "cartridge_id": ds.get("cartridge") or "",
            "layer": scope.layer,
            "row_count": evidence["row_count"],
            "created_at": evidence["created_at"].isoformat(),
        }
    )
    return {"name": name, "lineage": [lineage]}


def published_dataset_metadata(
    ds: dict[str, Any], security_context: dict[str, Any] | None
) -> dict[str, Any] | None:
    context = _context(security_context)
    if not context["tenant_id"] or not context["workspace_id"]:
        return None
    head = PublicationReader().published_head(
        str(ds.get("layer") or "silver"),
        str(ds.get("cartridge") or ""),
        str(ds.get("name") or ""),
        context,
    )
    if not head:
        return None
    evidence = None
    if head.get("status") != "legacy_unverified":
        evidence = PublicationEvidenceStore().read_exact(
            str(head["materialization_run_id"]), _scope(ds, context)
        )
        if not evidence:
            return None
    lineage = (evidence or {}).get("lineage") or {}
    public_metadata = lineage.get("public_metadata") or {}
    public_sources = [
        source
        for value in lineage.get("public_sources") or []
        if (source := _public_source_entity(value))
    ]
    return {
        "name": str(ds.get("name") or ""),
        "layer": str(ds.get("layer") or "silver"),
        "cartridge": str(ds.get("cartridge") or ""),
        "row_count": head.get("row_count"),
        "last_refresh": (
            head["published_at"].isoformat() if head.get("published_at") else None
        ),
        "status": "legacy_unverified"
        if head.get("status") == "legacy_unverified"
        else "published",
        "description": _public_text(public_metadata.get("description")),
        "sources": public_sources,
    }


def published_catalog(
    datasets_meta: Iterable[dict[str, Any]],
    security_context: dict[str, Any] | None,
    *,
    layer: str | None = None,
    cartridge: str | None = None,
    tags: list[str] | None = None,
    datasets: list[str] | None = None,
) -> dict[str, Any]:
    context = _context(security_context)
    if not context["tenant_id"] or not context["workspace_id"]:
        return {"datasets": {}, "relationships": []}
    reader = PublicationReader()
    evidence_store = PublicationEvidenceStore()
    output: dict[str, Any] = {}
    for ds in datasets_meta:
        name = str(ds.get("name") or "")
        ds_layer = str(ds.get("layer") or "silver")
        ds_cartridge = str(ds.get("cartridge") or "")
        if (
            not name
            or (layer and ds_layer != layer)
            or (cartridge and ds_cartridge != cartridge)
        ):
            continue
        if datasets and name not in datasets:
            continue
        scope = _scope(ds, context)
        head = reader.published_head(ds_layer, ds_cartridge, name, context)
        if not head:
            continue
        evidence = None
        if head.get("status") != "legacy_unverified":
            evidence = evidence_store.read_exact(
                str(head["materialization_run_id"]), scope
            )
            if not evidence:
                continue
        columns = []
        for field in (evidence or {}).get("catalog", []):
            field_tags = _public_tags(field.get("tags"))
            if tags and not set(tags).intersection(field_tags):
                continue
            column = {
                "name": field.get("name"),
                "type": field.get("type"),
                "description": _public_text(field.get("description")),
                "tags": field_tags,
                "is_key": bool(field.get("is_key")),
                "is_metric": bool(field.get("is_metric")),
                "example_values": _public_examples(field.get("example_values")),
            }
            columns.append(column)
        if tags and not columns:
            continue
        output[name] = {
            "layer": ds_layer,
            "cartridge": ds_cartridge,
            "row_count": head.get("row_count"),
            "last_refresh": head["published_at"].isoformat()
            if head.get("published_at")
            else None,
            "description": _public_text(
                (
                    ((evidence or {}).get("lineage") or {}).get("public_metadata") or {}
                ).get("description")
            ),
            "columns": columns,
        }
    relationships = []
    for ds in datasets_meta:
        name = str(ds.get("name") or "")
        if name not in output:
            continue
        scope = _scope(ds, context)
        head = reader.published_head(
            scope.layer, str(ds.get("cartridge") or ""), name, context
        )
        if not head or head.get("status") == "legacy_unverified":
            continue
        evidence = evidence_store.read_exact(str(head["materialization_run_id"]), scope)
        metadata = ((evidence or {}).get("lineage") or {}).get("public_metadata") or {}
        for value in metadata.get("relationships") or []:
            if relationship := _public_relationship(value, from_dataset=name):
                relationships.append(relationship)
    return {"datasets": output, "relationships": relationships}
