from __future__ import annotations

from app.services.control_room.business_policy_metadata import (
    policy_metadata_without_fields_sql,
)
from app.services.control_room.business_observation_order import (
    OBSERVATION_ORDER_BASELINE_KEY,
    OBSERVATION_ORDER_KEY,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
)


_ROW_COLUMNS = """
    tenant_id text, workspace_id text, owner_user_id bigint, item_id text,
    cartridge_id text, domain text, source_dataset text, item_kind text,
    title text, severity text, status text, entity_kind text, entity_id text,
    entity_label text, anomaly_type text, metadata jsonb,
    impact_estimate numeric, impact_currency text, confidence numeric,
    priority_score integer, selected_option_id text, execution_status text
"""

_CLEAN_EXISTING_METADATA = policy_metadata_without_fields_sql(
    "control_room_items.metadata", "$2"
)
_CLEAN_WORKFLOW_METADATA = (
    f"(({_CLEAN_EXISTING_METADATA} - '{DECISION_PROVENANCE_KEY}') "
    f"- '{WORKFLOW_QUARANTINE_KEY}')"
)
_QUARANTINED_WORKFLOW = (
    f"(EXCLUDED.metadata ? '{WORKFLOW_QUARANTINE_KEY}' "
    f"AND NOT EXCLUDED.metadata ? '{DECISION_PROVENANCE_KEY}')"
)
_INCOMING_IS_CURRENT = (
    f"COALESCE(EXCLUDED.metadata->>'{OBSERVATION_ORDER_KEY}', '') >= "
    f"COALESCE(control_room_items.metadata->>'{OBSERVATION_ORDER_KEY}', "
    f"EXCLUDED.metadata->>'{OBSERVATION_ORDER_BASELINE_KEY}', '')"
)
_BACKFILL_EXISTING_ORDER = (
    f"CASE WHEN EXCLUDED.metadata->>'{OBSERVATION_ORDER_BASELINE_KEY}' IS NOT NULL "
    f"THEN jsonb_set(control_room_items.metadata, '{{{OBSERVATION_ORDER_KEY}}}', "
    f"to_jsonb(EXCLUDED.metadata->>'{OBSERVATION_ORDER_BASELINE_KEY}'), true) "
    "ELSE control_room_items.metadata END"
)


def _if_current(incoming: str, existing: str) -> str:
    return f"CASE WHEN {_INCOMING_IS_CURRENT} THEN {incoming} ELSE {existing} END"


_SEMANTIC_UPDATE = f"""
    cartridge_id = {_if_current("EXCLUDED.cartridge_id", "control_room_items.cartridge_id")},
    domain = {_if_current("EXCLUDED.domain", "control_room_items.domain")},
    source_dataset = {_if_current("EXCLUDED.source_dataset", "control_room_items.source_dataset")},
    item_kind = {_if_current("EXCLUDED.item_kind", "control_room_items.item_kind")},
    title = {_if_current("EXCLUDED.title", "control_room_items.title")},
    severity = {_if_current("EXCLUDED.severity", "control_room_items.severity")},
    entity_kind = {_if_current("EXCLUDED.entity_kind", "control_room_items.entity_kind")},
    entity_id = {_if_current("EXCLUDED.entity_id", "control_room_items.entity_id")},
    entity_label = {_if_current("EXCLUDED.entity_label", "control_room_items.entity_label")},
    anomaly_type = {_if_current("EXCLUDED.anomaly_type", "control_room_items.anomaly_type")},
    metadata = {_if_current(f"({_CLEAN_WORKFLOW_METADATA} || EXCLUDED.metadata) - '{OBSERVATION_ORDER_BASELINE_KEY}'", _BACKFILL_EXISTING_ORDER)},
    impact_estimate = {_if_current("EXCLUDED.impact_estimate", "control_room_items.impact_estimate")},
    impact_currency = {_if_current("EXCLUDED.impact_currency", "control_room_items.impact_currency")},
    confidence = {_if_current("EXCLUDED.confidence", "control_room_items.confidence")},
    priority_score = {_if_current("EXCLUDED.priority_score", "control_room_items.priority_score")},
    owner_user_id = control_room_items.owner_user_id,
    last_seen_at = {_if_current("NOW()", "control_room_items.last_seen_at")}
"""


def _owner_conflict_guard(owner_parameter: int, workspace_parameter: int) -> str:
    return f"""
WHERE ${workspace_parameter}::boolean
   OR (
       ${owner_parameter}::bigint IS NOT NULL
       AND control_room_items.owner_user_id = ${owner_parameter}::bigint
       AND EXCLUDED.owner_user_id = ${owner_parameter}::bigint
   )
"""


PERSIST_ITEMS_SQL = f"""
INSERT INTO control_room_items (
    tenant_id, workspace_id, owner_user_id, item_id, cartridge_id, domain,
    source_dataset, item_kind, title, severity, status, entity_kind, entity_id,
    entity_label, anomaly_type, metadata, impact_estimate, impact_currency,
    confidence, priority_score, selected_option_id, execution_status,
    first_seen_at, last_seen_at
)
SELECT NULLIF(x.tenant_id, '')::uuid, x.workspace_id::uuid, x.owner_user_id,
       x.item_id, x.cartridge_id, x.domain, x.source_dataset, x.item_kind,
       x.title, x.severity, 'open', x.entity_kind, x.entity_id, x.entity_label,
       x.anomaly_type, x.metadata, x.impact_estimate, x.impact_currency,
       x.confidence, x.priority_score, x.selected_option_id,
       x.execution_status, NOW(), NOW()
  FROM jsonb_to_recordset($1::jsonb) AS x({_ROW_COLUMNS})
ON CONFLICT (workspace_id, item_id) DO UPDATE
SET {_SEMANTIC_UPDATE},
    status = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN 'open'
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.status
        ELSE control_room_items.status
    END,
    decision_id = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN NULL
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.decision_id
        ELSE control_room_items.decision_id
    END,
    selected_option_id = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN NULL
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.selected_option_id
        ELSE control_room_items.selected_option_id
    END,
    execution_status = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN 'not_started'
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.execution_status
        ELSE control_room_items.execution_status
    END
{_owner_conflict_guard(3, 4)}
"""

ENSURE_ITEM_SQL = f"""
INSERT INTO control_room_items (
    tenant_id, workspace_id, owner_user_id, item_id, cartridge_id, domain,
    source_dataset, item_kind, title, severity, status, entity_kind, entity_id,
    entity_label, anomaly_type, metadata, impact_estimate, impact_currency,
    confidence, priority_score, selected_option_id, execution_status,
    first_seen_at, last_seen_at
)
SELECT NULLIF(x.tenant_id, '')::uuid, x.workspace_id::uuid, x.owner_user_id,
       x.item_id, x.cartridge_id, x.domain, x.source_dataset, x.item_kind,
       x.title, x.severity, x.status, x.entity_kind, x.entity_id, x.entity_label,
       x.anomaly_type, x.metadata, x.impact_estimate, x.impact_currency,
       x.confidence, x.priority_score, x.selected_option_id,
       x.execution_status, NOW(), NOW()
  FROM jsonb_to_record($1::jsonb) AS x({_ROW_COLUMNS})
ON CONFLICT (workspace_id, item_id) DO UPDATE
SET {_SEMANTIC_UPDATE},
    status = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN 'open'
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.status
        WHEN control_room_items.status = ANY($3::text[]) THEN control_room_items.status
        ELSE EXCLUDED.status
    END,
    decision_id = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN NULL
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.decision_id
        ELSE control_room_items.decision_id
    END,
    selected_option_id = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN NULL
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.selected_option_id
        ELSE control_room_items.selected_option_id
    END,
    execution_status = CASE
        WHEN {_QUARANTINED_WORKFLOW} THEN 'not_started'
        WHEN NOT ({_INCOMING_IS_CURRENT}) THEN control_room_items.execution_status
        ELSE control_room_items.execution_status
    END
{_owner_conflict_guard(4, 5)}
"""


__all__ = ("ENSURE_ITEM_SQL", "PERSIST_ITEMS_SQL")
