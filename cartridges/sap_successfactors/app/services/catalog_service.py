from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

BASE_DIR = Path(__file__).resolve().parents[1]
ENTITIES_PATH = BASE_DIR / "config" / "entities.yaml"
KBS_PATH = BASE_DIR / "config" / "knowledge_bits.yaml"

CARTRIDGE_ID = "sap_successfactors"
logger = logging.getLogger(__name__)
DEFAULT_EXTRACT_ALL_EXCLUDE_ENTITIES = {
    "Candidate",
    "CareerInterest",
    "CareerWorksheet",
    "CompetencyEntity",
    "GoalPlan",
    "JobApplication",
    "LearningAssignment",
    "LearningHistory",
    "LearningItem",
    "PerformanceReview",
    "SkillProfile",
    "SuccessionNomination",
    "UserSkill",
}
VALID_EXTRACT_ALL_TARGETS = {"all", "foundation", "talent"}
FOUNDATION_EXTRACT_ALL_ENTITIES = {
    "User",
    "PerPerson",
    "PerPersonal",
    "EmpEmployment",
    "EmpJob",
    "FOCompany",
    "FODepartment",
    "FODivision",
    "FOLocation",
    "FOBusinessUnit",
    "FOCostCenter",
    "FOJobCode",
    "Position",
}

# Cartridge header metadata — used to UPSERT the `cartridges` row on startup so
# Studio's "Fuente de datos" dropdown lists this cartridge alongside Replicon.
CARTRIDGE_META = {
    "name":        "SAP SuccessFactors",
    "version":     "1.0.0",
    "description": (
        "Employee Central — empleados, posiciones, departamentos, "
        "compensaciones, centros de coste, tiempo y ausencias."
    ),
    "pattern":     "dag-based",
    "category":    "cartridge",
    "bronze_path": "raw/sap_successfactors/{entity}/load_date={date}/",
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
    for key in (
        "odata_entity",
        "service_path",
        "date_field",
        "effective_from_date",
        "effective_to_date",
        "primary_key",
    ):
        if yaml_entity.get(key) and not data.get(key):
            data[key] = yaml_entity[key]
    if yaml_entity.get("effective_dated") and not data.get("effective_dated"):
        data["effective_dated"] = True
    if yaml_entity.get("select_fields") and not data.get("select_fields"):
        data["select_fields"] = yaml_entity["select_fields"]
    if yaml_entity.get("protection") and not data.get("protection"):
        data["protection"] = yaml_entity["protection"]
    return data


# ── Seed on startup ───────────────────────────────────────────────────────────

def _dag_id_for_entity(entity: dict[str, Any]) -> str:
    return entity.get("dag_id") or f"{CARTRIDGE_ID}_extract"


def _seed_if_empty() -> None:
    """Top-up entity_config from YAML on every startup (entities missing from the
    DB get inserted; existing rows are preserved via ON CONFLICT DO NOTHING) so
    partial catalog states self-heal. Also upserts the cartridge header so Studio's
    dropdown picks it up, and seeds kb_config when empty."""
    try:
        engine = _get_engine()
        with engine.begin() as conn:
            # Ensure the cartridge header exists (Studio dropdown reads this).
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

            # Top-up entity_config from YAML on every startup so partial DB states
            # self-heal (entities missing from the DB get inserted). ON CONFLICT
            # DO NOTHING preserves any existing row, so admin edits and
            # migration-set fields (odata_entity, primary_key) are never clobbered.
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

    except Exception:
        logger.exception("Failed to seed SAP SuccessFactors catalog from YAML")
        raise

    try:
        with engine.begin() as conn:
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
    except Exception as exc:  # noqa: BLE001 - KB seed must not poison entity extraction.
        logger.warning("Skipping SAP SuccessFactors KB seed; kb_config unavailable: %s", exc)


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


def _extract_all_excluded_entities() -> set[str]:
    raw = os.getenv("SAP_SUCCESSFACTORS_EXTRACT_ALL_EXCLUDE_ENTITIES")
    if raw is None:
        return set(DEFAULT_EXTRACT_ALL_EXCLUDE_ENTITIES)
    return {part.strip() for part in raw.split(",") if part.strip()}


def _scope_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return str(value).strip() if value is not None else ""


def _security_scope(security_context: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(security_context, dict):
        return "", ""
    return (
        str(security_context.get("tenant_id") or "").strip(),
        str(security_context.get("workspace_id") or "").strip(),
    )


def _matches_security_scope(row: dict[str, Any], tenant_id: str, workspace_id: str) -> bool:
    row_tenant = _scope_value(row, "tenant_id")
    row_workspace = _scope_value(row, "workspace_id")
    if tenant_id and row_tenant and row_tenant != tenant_id:
        return False
    if workspace_id and row_workspace and row_workspace != workspace_id:
        return False
    return True


def _normalize_extract_all_target(target: str | None) -> str:
    normalized = str(target or "all").strip().lower()
    if normalized not in VALID_EXTRACT_ALL_TARGETS:
        return "all"
    return normalized


def _talent_extract_target_entities(
    *,
    conn_id: str | None,
    security_context: dict[str, Any] | None,
) -> tuple[set[str], list[dict[str, Any]], dict[str, set[str]]]:
    try:
        from app.services.preflight import talent_metadata_readiness

        readiness = talent_metadata_readiness(
            conn_id=conn_id,
            security_context=security_context,
            sample=True,
        )
    except Exception as exc:  # noqa: BLE001 - live metadata is a blocker, not a product crash.
        return set(), [
            {
                "entity": "__talent_metadata__",
                "status": "skipped",
                "reason": "metadata_preflight_failed",
                "error": str(exc)[:240],
            }
        ], {}

    targets = readiness.get("extraction_targets")
    if not isinstance(targets, list):
        targets = []
    ready_entities = {
        str(item.get("entity") or "").strip()
        for item in targets
        if isinstance(item, dict)
        and str(item.get("entity") or "").strip()
        and str(item.get("status") or "") in {"ready_to_extract", "metadata_ready"}
    }
    fields_by_entity: dict[str, set[str]] = {}
    for item in targets:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entity") or "").strip()
        if not entity or entity not in ready_entities:
            continue
        fields = {str(field) for field in (item.get("fields_present") or []) if field}
        if fields:
            fields_by_entity.setdefault(entity, set()).update(fields)
    skipped: list[dict[str, Any]] = []
    if not ready_entities:
        blockers = readiness.get("blockers") if isinstance(readiness, dict) else None
        skipped.append(
            {
                "entity": "__talent_cpa__",
                "status": "skipped",
                "reason": "talent_metadata_not_ready",
                "blockers": blockers if isinstance(blockers, list) else [],
            }
        )
    return ready_entities, skipped, fields_by_entity


def _target_entity_filter(
    *,
    target: str,
    conn_id: str | None,
    security_context: dict[str, Any] | None,
) -> tuple[set[str] | None, list[dict[str, Any]], dict[str, set[str]]]:
    if target == "all":
        return None, [], {}
    if target == "foundation":
        return set(FOUNDATION_EXTRACT_ALL_ENTITIES), [], {}
    return _talent_extract_target_entities(
        conn_id=conn_id,
        security_context=security_context,
    )


def get_extract_all_plan(
    *,
    conn_id: str | None = None,
    security_context: dict[str, Any] | None = None,
    target: str | None = "all",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the honest live extraction plan for extract_all.

    The catalog can contain aspirational entities from the cartridge YAML. A
    tenant-scoped live run must only extract entities explicitly bound to the
    selected Vault connection and scope; everything else is reported as skipped
    so it cannot become a false product failure or a fake PASS.
    """
    selected_conn_id = (conn_id or "").strip()
    tenant_id, workspace_id = _security_scope(security_context)
    normalized_target = _normalize_extract_all_target(target)
    target_entities, target_skipped, target_fields = _target_entity_filter(
        target=normalized_target,
        conn_id=selected_conn_id or None,
        security_context=security_context,
    )
    excluded = _extract_all_excluded_entities()
    entities: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(target_skipped)
    configured_entities: set[str] = set()

    for config in get_all_entities():
        entity = str(config.get("entity") or "").strip()
        if not entity:
            continue
        configured_entities.add(entity)

        if target_entities is not None and entity not in target_entities:
            continue

        if normalized_target == "talent" and entity in target_fields:
            configured_fields = {
                str(field)
                for field in (config.get("select_fields") or [])
                if str(field).strip()
            }
            missing_fields = sorted(configured_fields - target_fields[entity])
            if missing_fields:
                skipped.append({
                    "entity": entity,
                    "status": "skipped",
                    "reason": "metadata_fields_missing_for_config",
                    "fields_missing": missing_fields,
                })
                continue

        if selected_conn_id:
            config_conn_id = str(config.get("connection_id") or "").strip()
            if config_conn_id != selected_conn_id:
                skipped.append({
                    "entity": entity,
                    "status": "skipped",
                    "reason": "not_scoped_for_connection",
                })
                continue
            if not _matches_security_scope(config, tenant_id, workspace_id):
                skipped.append({
                    "entity": entity,
                    "status": "skipped",
                    "reason": "scope_mismatch",
                })
                continue

        if normalized_target == "all" and entity in excluded:
            skipped.append({
                "entity": entity,
                "status": "skipped",
                "reason": "external_scope_blocked",
            })
            continue

        entities.append(config)

    if target_entities is not None:
        for entity in sorted(target_entities - configured_entities):
            skipped.append(
                {
                    "entity": entity,
                    "status": "skipped",
                    "reason": "not_configured_for_extraction",
                }
            )

    return entities, skipped


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
