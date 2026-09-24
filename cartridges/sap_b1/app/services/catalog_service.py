from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

BASE_DIR = Path(__file__).resolve().parents[1]
ENTITIES_PATH = BASE_DIR / "config" / "entities.yaml"
KBS_PATH = BASE_DIR / "config" / "knowledge_bits.yaml"

CARTRIDGE_ID = "sap_b1"
logger = logging.getLogger(__name__)

CARTRIDGE_META = {
    "name":        "SAP Business One",
    "version":     "0.1.0",
    "description": (
        "ERP para grupos de empresas en SAP Business One 10 (HANA): "
        "maestros, documentos de venta y compra, asientos contables, "
        "inventario, lotes y producción, una empresa por esquema."
    ),
    "pattern":     "dag-based",
    "category":    "cartridge",
    "bronze_path": "raw/sap_b1/{entity}/load_date={date}/",
}

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


def _merge_yaml_runtime_fields(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    yaml_entity = _yaml_entity_map().get(str(data.get("entity"))) or {}
    # Business One reading rules that entity_config has no column for: they
    # travel from entities.yaml onto the DB row at read time.
    for key in ("table", "parent", "parent_key", "join_key", "watermark_ts_field", "column_types"):
        if yaml_entity.get(key) and not data.get(key):
            data[key] = yaml_entity[key]
    return data


# ── Seed on startup ───────────────────────────────────────────────────────────

def _dag_id_for_entity(entity: dict[str, Any]) -> str:
    return entity.get("dag_id") or f"{CARTRIDGE_ID}_extract"


def _seed_if_empty() -> None:
    """If entity_config has no rows for this cartridge, import from YAML.
    Also upserts the cartridge header so Studio's dropdown picks it up."""
    try:
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
                VALUES (:cid, :name, :version, :description, :pattern, :category, :bronze_path)
                ON CONFLICT (id) DO UPDATE
                    SET name        = EXCLUDED.name,
                        version     = EXCLUDED.version,
                        description = EXCLUDED.description,
                        pattern     = EXCLUDED.pattern,
                        category    = EXCLUDED.category,
                        bronze_path = EXCLUDED.bronze_path,
                        updated_at  = NOW()
            """), {"cid": CARTRIDGE_ID, **CARTRIDGE_META})

            count = conn.execute(
                text("SELECT COUNT(*) FROM entity_config WHERE cartridge_id = :cid"),
                {"cid": CARTRIDGE_ID},
            ).scalar()
            if count == 0:
                for e in _yaml_entities():
                    conn.execute(text("""
                        INSERT INTO entity_config (
                            cartridge_id, entity, display_name, mode, watermark_field, watermark_format,
                            page_size, select_fields, protection,
                            effective_dated, date_field, future_window_days, primary_key,
                            dag_id, trigger_type, connection_id, description, enabled
                        ) VALUES (
                            :cid, :entity, :display, :mode, :wf, :wfmt,
                            :ps, CAST(:sel AS JSONB), CAST(:prot AS JSONB),
                            :ed, :df, :fwd, :pk,
                            :dag, 'manual', :conn, :desc, TRUE
                        )
                        ON CONFLICT (cartridge_id, entity) DO NOTHING
                    """), {
                        "cid": CARTRIDGE_ID,
                        "entity": e.get("entity"),
                        "display": e.get("display_name") or e.get("entity"),
                        "mode": e.get("mode", "full"),
                        "wf": e.get("watermark_field"),
                        "wfmt": e.get("watermark_format"),
                        "ps": e.get("page_size", 1000),
                        "sel": json.dumps(e.get("select_fields") or []),
                        "prot": json.dumps(e.get("protection", {})),
                        "ed": bool(e.get("effective_dated", False)),
                        "df": e.get("date_field"),
                        "fwd": e.get("future_window_days"),
                        "pk": e.get("primary_key"),
                        "dag": _dag_id_for_entity(e),
                        "conn": CARTRIDGE_ID,
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
        logger.exception("Failed to seed SAP Business One catalog from YAML")
        raise


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
        return [_merge_yaml_runtime_fields(dict(r)) for r in rows]
    except Exception:
        return _yaml_entities()


def get_entity_config(entity_name: str) -> dict[str, Any] | None:
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM entity_config
                WHERE cartridge_id = :cid AND entity = :e
            """), {"cid": CARTRIDGE_ID, "e": entity_name}).mappings().first()
        if row:
            return _merge_yaml_runtime_fields(dict(row))
        for e in _yaml_entities():
            if e.get("entity") == entity_name:
                return e
        return None
    except Exception:
        for e in _yaml_entities():
            if e.get("entity") == entity_name:
                return e
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
