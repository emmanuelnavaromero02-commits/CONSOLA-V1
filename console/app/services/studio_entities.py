"""Studio entity authoring service.

``entity_config`` is still the operational source of truth for extraction.
This module adds a Studio-facing spec registry and keeps entity_config in sync
so UI-created entities are immediately visible to cartridges, DAGs, and MCP.
"""
from __future__ import annotations

import json
import re
from typing import Any

import asyncpg
import yaml
from fastapi import HTTPException

from app.services import audit_service, auth, cartridge_service


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class DuplicateEntityError(ValueError):
    pass


class UnknownCartridgeError(ValueError):
    pass


def _clean_identifier(value: str, *, label: str) -> str:
    ident = (value or "").strip()
    if not _IDENT_RE.fullmatch(ident):
        raise ValueError(f"Invalid {label}: use letters, numbers and underscores only")
    return ident


def _normalise_spec(name: str, cartridge: str, spec: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(spec or {})
    raw["name"] = _clean_identifier(str(raw.get("name") or raw.get("entity") or name), label="entity")
    raw["cartridge"] = _clean_identifier(str(raw.get("cartridge") or cartridge), label="cartridge")
    if raw["name"] != name:
        raise ValueError("spec.name must match requested entity")
    if raw["cartridge"] != cartridge:
        raise ValueError("spec.cartridge must match requested cartridge")
    fields = raw.get("fields")
    if fields is None:
        fields = raw.get("columns")
    if not isinstance(fields, list) or not fields:
        raise ValueError("spec.fields must be a non-empty list")
    for idx, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ValueError(f"spec.fields[{idx}] must be an object")
        if not field.get("name"):
            raise ValueError(f"spec.fields[{idx}].name is required")
        field["name"] = _clean_identifier(str(field["name"]), label=f"field {idx}")
    raw["fields"] = fields
    return raw


def _entity_config_fields(spec: dict[str, Any]) -> dict[str, Any]:
    fields = spec.get("fields") or []
    primary_key = spec.get("primary_key") or spec.get("id_field") or ""
    if not primary_key:
        pk_field = next((f for f in fields if f.get("primary_key") or f.get("is_primary_key")), None)
        primary_key = (pk_field or {}).get("name") or ""
    return {
        "display_name": spec.get("display_name") or spec.get("title") or spec["name"],
        "mode": spec.get("mode") or "full",
        "primary_key": primary_key,
        "dag_id": spec.get("dag_id") or "",
        "trigger_type": spec.get("trigger_type") or "manual",
        "cron_expression": spec.get("cron_expression") or "",
        "description": spec.get("description") or "",
        "enabled": bool(spec.get("enabled", True)),
    }


def _parse_spec_text(text: str) -> Any:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = json.loads(text)
    if parsed is None:
        raise ValueError("spec is empty")
    return parsed


def _iter_entity_specs(parsed: Any, default_cartridge: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(parsed, dict):
        raise ValueError("spec must be a YAML/JSON object")

    default = default_cartridge or parsed.get("cartridge") or parsed.get("cartridge_id")
    raw_entities = parsed.get("entities")
    if raw_entities is None and ("name" in parsed or "entity" in parsed):
        raw_entities = [parsed]

    entities: list[dict[str, Any]] = []
    if isinstance(raw_entities, dict):
        for name, cfg in raw_entities.items():
            item = dict(cfg or {}) if isinstance(cfg, dict) else {}
            item.setdefault("name", str(name))
            entities.append(item)
    elif isinstance(raw_entities, list):
        for item in raw_entities:
            if not isinstance(item, dict):
                raise ValueError("entities[] entries must be objects")
            entities.append(dict(item))
    else:
        raise ValueError("spec must include entities[] or a single entity object")

    if default:
        for item in entities:
            item["cartridge"] = default
    return entities


def _serialize_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    if item.get("id") is not None:
        item["id"] = str(item["id"])
    spec = item.get("spec")
    if isinstance(spec, str):
        item["spec"] = json.loads(spec)
    return item


async def _ensure_cartridge(cartridge: str) -> dict:
    manifest = await cartridge_service.get_cartridge(cartridge)
    if not manifest:
        raise UnknownCartridgeError(f"Cartridge '{cartridge}' not found")
    return manifest


async def _sync_entity_config(conn, cartridge_id: str, entity: str, spec: dict[str, Any]) -> None:
    fields = _entity_config_fields(spec)
    await conn.execute(
        """
        INSERT INTO entity_config
            (cartridge_id, entity, display_name, mode, primary_key,
             dag_id, trigger_type, cron_expression, description, enabled)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (cartridge_id, entity) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            mode = EXCLUDED.mode,
            primary_key = EXCLUDED.primary_key,
            dag_id = EXCLUDED.dag_id,
            trigger_type = EXCLUDED.trigger_type,
            cron_expression = EXCLUDED.cron_expression,
            description = EXCLUDED.description,
            enabled = EXCLUDED.enabled
        """,
        cartridge_id,
        entity,
        fields.get("display_name"),
        fields.get("mode", "full"),
        fields.get("primary_key"),
        fields.get("dag_id"),
        fields.get("trigger_type", "manual"),
        fields.get("cron_expression"),
        fields.get("description", ""),
        fields.get("enabled", True),
    )


async def list_entities(cartridge: str | None = None) -> list[dict[str, Any]]:
    cartridge = _clean_identifier(cartridge, label="cartridge") if cartridge else None
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT id, name, cartridge, spec, created_by, created_at, updated_at
        FROM studio_entities
        WHERE ($1::text IS NULL OR cartridge = $1)
        ORDER BY cartridge, name
        """,
        cartridge,
    )

    authored = {
        (r["cartridge"], r["name"]): _serialize_row(r)
        for r in rows
    }
    result: dict[tuple[str, str], dict[str, Any]] = {}

    cartridges = [cartridge] if cartridge else [c["id"] for c in await cartridge_service.list_cartridges()]
    for cart_id in cartridges:
        manifest = await cartridge_service.get_cartridge(cart_id)
        if not manifest:
            continue
        for ent in manifest.get("entities") or []:
            name = ent.get("entity") or ent.get("name")
            if not name:
                continue
            key = (cart_id, name)
            result[key] = {
                "id": authored.get(key, {}).get("id") or name,
                "name": name,
                "entity": name,
                "cartridge": cart_id,
                "display_name": ent.get("display_name") or name,
                "mode": ent.get("mode") or "full",
                "dag_id": ent.get("dag_id") or "",
                "description": ent.get("description") or "",
                "spec": authored.get(key, {}).get("spec") or {
                    "name": name,
                    "cartridge": cart_id,
                    "fields": [{"name": ent.get("primary_key") or "id", "type": "string", "primary_key": True}],
                },
                "source": "entity_config",
            }

    for key, item in authored.items():
        current = result.get(key, {})
        if not current:
            continue
        spec = item.get("spec") or {}
        result[key] = {
            **current,
            **item,
            "entity": item["name"],
            "display_name": spec.get("display_name") or spec.get("title") or current.get("display_name") or item["name"],
            "mode": spec.get("mode") or current.get("mode") or "full",
            "dag_id": spec.get("dag_id") or current.get("dag_id") or "",
            "description": spec.get("description") or current.get("description") or "",
            "source": "studio_entities+entity_config" if current else "studio_entities",
        }
    return list(result.values())


async def create_entity(name: str, cartridge: str, spec: dict[str, Any] | None, user: dict) -> dict[str, Any]:
    entity_name = _clean_identifier(name, label="entity")
    cartridge_id = _clean_identifier(cartridge, label="cartridge")
    await _ensure_cartridge(cartridge_id)
    normalised = _normalise_spec(entity_name, cartridge_id, spec)

    pool = await auth.pool()
    conn = await pool.acquire()
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO studio_entities (name, cartridge, spec, created_by)
                VALUES ($1, $2, $3::jsonb, $4)
                RETURNING id, name, cartridge, spec, created_by, created_at, updated_at
                """,
                normalised["name"],
                normalised["cartridge"],
                json.dumps(normalised),
                user.get("id"),
            )
            await _sync_entity_config(conn, normalised["cartridge"], normalised["name"], normalised)
    except asyncpg.UniqueViolationError as exc:
        raise DuplicateEntityError(
            f"Entity '{normalised['name']}' already exists for cartridge '{normalised['cartridge']}'"
        ) from exc
    finally:
        await pool.release(conn)

    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="studio.entity.create",
        resource_type="studio_entity",
        resource_id=f"{normalised['cartridge']}:{normalised['name']}",
        status="success",
        metadata={"field_count": len(normalised["fields"])},
    )
    return _serialize_row(row)


async def upload_spec(text: str, user: dict, *, default_cartridge: str | None = None) -> dict[str, Any]:
    parsed = _parse_spec_text(text)
    raw_entities = _iter_entity_specs(parsed, default_cartridge=default_cartridge)
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, item in enumerate(raw_entities):
        name = item.get("name") or item.get("entity")
        cartridge = item.get("cartridge") or item.get("cartridge_id") or default_cartridge
        try:
            created.append(await create_entity(str(name or ""), str(cartridge or ""), item, user))
        except (ValueError, DuplicateEntityError, UnknownCartridgeError) as exc:
            errors.append({"idx": idx, "entity": name, "error": str(exc)})
    return {"created": created, "errors": errors}


def to_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownCartridgeError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DuplicateEntityError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
