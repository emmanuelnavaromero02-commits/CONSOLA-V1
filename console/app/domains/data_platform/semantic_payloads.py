"""Pure response helpers for semantic catalog routes."""

from __future__ import annotations

from typing import Any


def semantic_entities_with_catalog(
    manifest_entities: list[dict[str, Any]], catalog_entities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    entities = list(manifest_entities or [])
    existing_names = {
        str(item.get("entity") or item.get("name") or "").strip()
        for item in entities
        if isinstance(item, dict)
    }
    for item in catalog_entities or []:
        if item["name"] not in existing_names:
            entities.append(item)
    return entities


def semantic_manifest_response(
    *,
    cartridge: str,
    manifest: dict[str, Any],
    catalog_entities: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "cartridge": cartridge,
        "server": manifest,
        "entities": semantic_entities_with_catalog(
            list(manifest.get("entities") or []),
            catalog_entities,
        ),
    }
