from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

BASE_DIR = Path(__file__).resolve().parents[1]
ENTITIES_PATH = BASE_DIR / "config" / "entities.yaml"
KBS_PATH = BASE_DIR / "config" / "knowledge_bits.yaml"

CARTRIDGE_ID = "hubspot"

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, future=True)
    return _engine


# ── YAML fallbacks ────────────────────────────────────────────────────────────

def _yaml_entities() -> list[dict[str, Any]]:
    if not ENTITIES_PATH.exists():
        return []
    with ENTITIES_PATH.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("entities", [])


def _yaml_kbs() -> list[dict[str, Any]]:
    if not KBS_PATH.exists():
        return []
    with KBS_PATH.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("knowledge_bits", [])


def _yaml_entity_map() -> dict[str, dict[str, Any]]:
    return {str(e.get("entity")): e for e in _yaml_entities() if e.get("entity")}


def _merge_yaml_runtime_fields(row: Any) -> dict[str, Any]:
    """Re-inject HubSpot runtime fields that are NOT columns in entity_config
    (``api_path``, ``result_shape``) and guarantee ``properties`` is populated,
    sourcing them from the bundled entities.yaml.

    Mirrors the SAP cartridge's odata_entity re-injection: the extraction client
    always gets what it needs regardless of how the DB row was seeded. Without
    this, a DB-backed entity_config row (seeded by seed.sql, which can only set
    real columns) would reach the client without api_path/result_shape/properties
    and every object extraction would silently return only HubSpot's default
    property set."""
    data = dict(row)
    yaml_entity = _yaml_entity_map().get(str(data.get("entity"))) or {}
    for key in ("api_path", "result_shape"):
        if yaml_entity.get(key) and not data.get(key):
            data[key] = yaml_entity[key]
    if not data.get("properties"):
        sel = data.get("select_fields")
        if isinstance(sel, str):
            try:
                sel = json.loads(sel)
            except Exception:
                sel = None
        data["properties"] = sel or yaml_entity.get("properties") or []
    return data


# ── Seed on startup ───────────────────────────────────────────────────────────

def _seed_if_empty() -> None:
    """If entity_config has no rows for this cartridge, import from YAML."""
    try:
        engine = _get_engine()
        with engine.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM entity_config WHERE cartridge_id = :cid"),
                {"cid": CARTRIDGE_ID},
            ).scalar()
            if count == 0:
                for e in _yaml_entities():
                    conn.execute(text("""
                        INSERT INTO entity_config (
                            cartridge_id, entity, mode, watermark_field, watermark_format,
                            page_size, select_fields, protection,
                            effective_dated, date_field, future_window_days, description, enabled
                        ) VALUES (
                            :cid, :entity, :mode, :wf, :wfmt,
                            :ps, CAST(:sel AS JSONB), CAST(:prot AS JSONB),
                            :ed, :df, :fwd, :desc, TRUE
                        )
                        ON CONFLICT (cartridge_id, entity) DO NOTHING
                    """), {
                        "cid": CARTRIDGE_ID,
                        "entity": e.get("entity"),
                        "mode": e.get("mode", "full"),
                        "wf": e.get("watermark_field"),
                        "wfmt": e.get("watermark_format"),
                        "ps": e.get("page_size", 100),
                        "sel": json.dumps(e.get("properties") or e.get("select") or []),
                        "prot": json.dumps(e.get("protection", {})),
                        "ed": bool(e.get("effective_dated", False)),
                        "df": e.get("date_field"),
                        "fwd": e.get("future_window_days"),
                        "desc": e.get("description", ""),
                    })

            kb_count = conn.execute(
                text("SELECT COUNT(*) FROM kb_config WHERE cartridge_id = :cid"),
                {"cid": CARTRIDGE_ID},
            ).scalar()
            if kb_count == 0:
                for kb in _yaml_kbs():
                    conn.execute(text("""
                        INSERT INTO kb_config (
                            cartridge_id, kb_id, name, description, sql, pg_table, output_path, enabled
                        ) VALUES (
                            :cid, :kid, :name, :desc, :sql, :pg, :out, TRUE
                        )
                        ON CONFLICT (cartridge_id, kb_id) DO NOTHING
                    """), {
                        "cid": CARTRIDGE_ID,
                        "kid": kb.get("id"),
                        "name": kb.get("name", kb.get("id")),
                        "desc": kb.get("description", ""),
                        "sql": kb.get("sql", ""),
                        "pg": kb.get("pg_table", kb.get("id")),
                        "out": kb.get("output_path", ""),
                    })
    except Exception:
        pass  # DB unavailable — callers fall back to YAML


# ── Public API ────────────────────────────────────────────────────────────────

def get_all_entities() -> list[dict[str, Any]]:
    try:
        _seed_if_empty()
        engine = _get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM entity_config
                WHERE cartridge_id = :cid AND enabled = TRUE
                ORDER BY entity
            """), {"cid": CARTRIDGE_ID}).mappings().all()
        return [_merge_yaml_runtime_fields(r) for r in rows]
    except Exception:
        return [_merge_yaml_runtime_fields(e) for e in _yaml_entities()]


def get_entity_config(entity_name: str) -> dict[str, Any] | None:
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM entity_config
                WHERE cartridge_id = :cid AND entity = :e
            """), {"cid": CARTRIDGE_ID, "e": entity_name}).mappings().first()
        return _merge_yaml_runtime_fields(row) if row else None
    except Exception:
        for e in _yaml_entities():
            if e.get("entity") == entity_name:
                return _merge_yaml_runtime_fields(e)
        return None


def get_all_kbs() -> list[dict[str, Any]]:
    try:
        _seed_if_empty()
        engine = _get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM kb_config
                WHERE cartridge_id = :cid AND enabled = TRUE
                ORDER BY kb_id
            """), {"cid": CARTRIDGE_ID}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return _yaml_kbs()


def get_kb_config(kb_id: str) -> dict[str, Any] | None:
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM kb_config
                WHERE cartridge_id = :cid AND kb_id = :kid
            """), {"cid": CARTRIDGE_ID, "kid": kb_id}).mappings().first()
        return dict(row) if row else None
    except Exception:
        for kb in _yaml_kbs():
            if kb.get("id") == kb_id:
                return kb
        return None
