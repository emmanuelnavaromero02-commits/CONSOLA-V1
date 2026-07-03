from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
import yaml

from app.domains.studio.validation import clean_identifier
from app.services import schema_introspect


def schema_entities_from_fields(
    fields_by_entity: dict[str, list[schema_introspect.Field]],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for name, fields in sorted(fields_by_entity.items()):
        try:
            entity = clean_identifier(str(name), label="entity")
        except HTTPException:
            continue
        primary_key = next(
            (field["name"] for field in fields if field.get("primary_key")), ""
        )
        entities.append(
            {
                "name": entity,
                "entity": entity,
                "display_name": entity,
                "primary_key": primary_key,
                "fields": fields,
            }
        )
    return entities


def load_static_entity_specs(
    cartridge_id: str,
    *,
    registry_root: Path = Path("/registry/cartridges"),
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    repo_root = repo_root or Path(__file__).resolve().parents[4]
    candidates = [
        registry_root / cartridge_id / "app" / "config" / "entities.yaml",
        repo_root / "cartridges" / cartridge_id / "app" / "config" / "entities.yaml",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return []
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_entities = parsed.get("entities") if isinstance(parsed, dict) else []
    return [dict(item) for item in raw_entities if isinstance(item, dict)]


def fields_from_static_entity(entity: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    existing = entity.get("fields")
    if isinstance(existing, list):
        for field in existing:
            if isinstance(field, dict) and field.get("name"):
                fields.append(schema_introspect.normalize_field(field))
    select_fields = entity.get("select_fields")
    if isinstance(select_fields, list):
        seen = {field["name"] for field in fields}
        primary_key = str(entity.get("primary_key") or "")
        for name in select_fields:
            name = str(name)
            if name in seen:
                continue
            lower = name.lower()
            guessed_type = (
                "timestamp"
                if any(fragment in lower for fragment in ("date", "time", "aedtm"))
                else "string"
            )
            fields.append(
                {
                    "name": name,
                    "type": guessed_type,
                    "nullable": True,
                    "primary_key": name == primary_key,
                    "source_type": "static_select_field",
                }
            )
    properties = entity.get("properties")
    if isinstance(properties, list):
        seen = {field["name"] for field in fields}
        primary_key = str(entity.get("primary_key") or entity.get("id_field") or "")
        for name in properties:
            name = str(name)
            if not name or name in seen:
                continue
            lower = name.lower()
            guessed_type = (
                "timestamp"
                if any(
                    fragment in lower
                    for fragment in ("date", "time", "updated", "modified")
                )
                else "string"
            )
            fields.append(
                {
                    "name": name,
                    "type": guessed_type,
                    "nullable": True,
                    "primary_key": name == primary_key,
                    "source_type": "static_property",
                }
            )
            seen.add(name)
    if not fields and entity.get("primary_key"):
        fields.append(
            {
                "name": str(entity["primary_key"]),
                "type": "string",
                "nullable": False,
                "primary_key": True,
                "source_type": "static_primary_key",
            }
        )
    if entity.get("watermark_field") and all(
        f["name"] != entity["watermark_field"] for f in fields
    ):
        fields.append(
            {
                "name": str(entity["watermark_field"]),
                "type": "timestamp",
                "nullable": True,
                "primary_key": False,
                "source_type": "static_watermark_field",
            }
        )
    return fields


def static_introspection_payload(
    cartridge_id: str,
    connector_schema: dict[str, Any],
    *,
    reason: str = "",
    entity_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    specs = (
        entity_specs
        if entity_specs is not None
        else load_static_entity_specs(cartridge_id)
    )
    for item in specs:
        name = item.get("entity") or item.get("name")
        if not name:
            continue
        try:
            entity = clean_identifier(str(name), label="entity")
        except HTTPException:
            continue
        fields = fields_from_static_entity(item)
        entities.append(
            {
                "name": entity,
                "entity": entity,
                "display_name": item.get("display_name") or item.get("title") or entity,
                "description": item.get("description") or "",
                "mode": item.get("mode") or "full",
                "primary_key": item.get("primary_key")
                or next((f["name"] for f in fields if f.get("primary_key")), ""),
                "watermark_field": item.get("watermark_field") or "",
                "odata_entity": item.get("odata_entity") or "",
                "fields": fields,
            }
        )
    return {
        "cartridge_id": cartridge_id,
        "endpoint": f"/api/cartridges/{cartridge_id}/connector_schema",
        "connector_schema": connector_schema,
        "entities": entities,
        "source": "static",
        "reason": reason
        or "live introspection unavailable; returned connector.yaml/entities.yaml fallback",
    }
