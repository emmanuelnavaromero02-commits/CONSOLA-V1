from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

try:
    from app.publication_contract import PublicationScope
    from app.publication_snapshot import (
        PublicationSnapshot,
        PublicationSnapshotResolver,
    )
except ModuleNotFoundError:
    from refinement.app.publication_contract import PublicationScope
    from refinement.app.publication_snapshot import (
        PublicationSnapshot,
        PublicationSnapshotResolver,
    )


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
        values = parts[:3]
        if not all(_PUBLIC_SOURCE.fullmatch(part) for part in values):
            return ""
        return "/".join(values)
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
    ds: dict[str, Any],
    security_context: dict[str, Any] | None,
    resolver: PublicationSnapshotResolver | None = None,
) -> dict[str, Any]:
    context = _context(security_context)
    name = str(ds.get("name") or "")
    if not context["tenant_id"] or not context["workspace_id"]:
        return {"name": name, "lineage": []}
    snapshot = (resolver or PublicationSnapshotResolver()).published_snapshot(
        ds, context
    )
    if not snapshot or snapshot.head.get("status") == "legacy_unverified":
        return {"name": name, "lineage": []}
    evidence = snapshot.evidence
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
            "layer": snapshot.scope.layer,
            "row_count": evidence["row_count"],
            "created_at": evidence["created_at"].isoformat(),
        }
    )
    return {"name": name, "lineage": [lineage]}


def published_dataset_metadata(
    ds: dict[str, Any],
    security_context: dict[str, Any] | None,
    resolver: PublicationSnapshotResolver | None = None,
) -> dict[str, Any] | None:
    context = _context(security_context)
    if not context["tenant_id"] or not context["workspace_id"]:
        return None
    snapshot = (resolver or PublicationSnapshotResolver()).published_snapshot(
        ds, context
    )
    if not snapshot:
        return None
    return public_dataset_projection(ds, snapshot)


def public_dataset_projection(
    ds: dict[str, Any], snapshot: PublicationSnapshot
) -> dict[str, Any]:
    head, evidence = snapshot.head, snapshot.evidence
    lineage = (evidence or {}).get("lineage") or {}
    public_metadata = lineage.get("public_metadata") or {}
    public_sources = [
        source
        for value in lineage.get("public_sources") or []
        if (source := _public_source_entity(value))
    ]
    fields = []
    for value in (evidence or {}).get("catalog", []):
        if not isinstance(value, dict):
            continue
        name = _public_text(value.get("name"))
        data_type = _public_text(value.get("type"))
        if name and data_type:
            field = {"name": name, "type": data_type}
            for key in ("null_rate", "distinct_count", "min_value", "max_value"):
                if key in value:
                    field[key] = value[key]
            fields.append(field)
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
        "fields": fields,
    }


def published_catalog(
    datasets_meta: Iterable[dict[str, Any]],
    security_context: dict[str, Any] | None,
    *,
    layer: str | None = None,
    cartridge: str | None = None,
    tags: list[str] | None = None,
    datasets: list[str] | None = None,
    resolver: PublicationSnapshotResolver | None = None,
) -> dict[str, Any]:
    context = _context(security_context)
    if not context["tenant_id"] or not context["workspace_id"]:
        return {"datasets": {}, "relationships": []}
    resolver = resolver or PublicationSnapshotResolver()
    metadata = list(datasets_meta)
    output: dict[str, Any] = {}
    snapshot_by_dataset: dict[str, PublicationSnapshot] = {}
    selected_metadata = []
    for ds in metadata:
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
        selected_metadata.append(ds)
    published_snapshots = getattr(resolver, "published_snapshots", None)
    if callable(published_snapshots):
        snapshots = published_snapshots(selected_metadata, context)
    else:
        snapshots = [
            resolver.published_snapshot(ds, context) for ds in selected_metadata
        ]
    for ds, snapshot in zip(selected_metadata, snapshots, strict=True):
        name = str(ds.get("name") or "")
        ds_layer = str(ds.get("layer") or "silver")
        ds_cartridge = str(ds.get("cartridge") or "")
        if not snapshot:
            continue
        snapshot.validate_snapshot()
        snapshot_by_dataset[name] = snapshot
        head, evidence = snapshot.head, snapshot.evidence
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
            for key in ("null_rate", "distinct_count", "min_value", "max_value"):
                if key in field:
                    column[key] = field[key]
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
    for ds in selected_metadata:
        name = str(ds.get("name") or "")
        if name not in output:
            continue
        snapshot = snapshot_by_dataset.get(name)
        head = snapshot.head if snapshot else None
        if not head or head.get("status") == "legacy_unverified":
            continue
        evidence = snapshot.evidence
        metadata = ((evidence or {}).get("lineage") or {}).get("public_metadata") or {}
        for value in metadata.get("relationships") or []:
            if relationship := _public_relationship(value, from_dataset=name):
                relationships.append(relationship)
    return {"datasets": output, "relationships": relationships}
