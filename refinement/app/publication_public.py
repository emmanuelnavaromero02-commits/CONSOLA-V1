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
            column = {"name": field.get("name"), "type": field.get("type")}
            if tags:
                continue
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
            "columns": columns,
        }
    return {"datasets": output, "relationships": []}
