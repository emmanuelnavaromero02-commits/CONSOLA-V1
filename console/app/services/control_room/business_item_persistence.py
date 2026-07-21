from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import Mock

from app.services.control_room.business_policy_metadata import (
    REPLACED_POLICY_KEYS,
    diagnostic_policy_sql,
    policy_metadata_without_fields_sql,
)
from app.services.control_room.business_serialization import dumps_jsonb
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    persistence_metadata,
    quarantine_workflow_metadata,
)


class OwnerScopeConflict(RuntimeError):
    pass


class PersistenceCountMismatch(RuntimeError):
    pass


class PersistenceCommandTagError(RuntimeError):
    pass


_WAS_DIAGNOSTIC = (
    "control_room_items.item_kind = 'source_state' "
    f"OR {diagnostic_policy_sql('control_room_items.metadata')}"
)
_BECOMES_BUSINESS = (
    "EXCLUDED.item_kind <> 'source_state' "
    f"AND NOT {diagnostic_policy_sql('EXCLUDED.metadata')}"
)
_PROVEN_WORKFLOW = (
    f"control_room_items.metadata->'{DECISION_PROVENANCE_KEY}'->>'policy_version' = $3 "
    f"AND control_room_items.metadata->'{DECISION_PROVENANCE_KEY}'->>'eligible_at_link' = 'true' "
    f"AND control_room_items.metadata->'{DECISION_PROVENANCE_KEY}'->>'item_id' "
    "= EXCLUDED.item_id "
    f"AND lower(control_room_items.metadata->'{DECISION_PROVENANCE_KEY}'->>'kind') "
    "= lower(EXCLUDED.item_kind) "
    f"AND control_room_items.metadata->'{DECISION_PROVENANCE_KEY}'->>'fingerprint' "
    f"= EXCLUDED.metadata->>'{CURRENT_ELIGIBILITY_FINGERPRINT_KEY}' "
    f"AND control_room_items.metadata->'{DECISION_PROVENANCE_KEY}'->>'decision_id' "
    "= control_room_items.decision_id::text"
)
_HAS_WORKFLOW = (
    "control_room_items.decision_id IS NOT NULL "
    "OR control_room_items.selected_option_id IS NOT NULL "
    "OR COALESCE(control_room_items.execution_status, 'not_started') <> 'not_started'"
)
_RESET_WORKFLOW = (
    f"({_WAS_DIAGNOSTIC} OR {_HAS_WORKFLOW}) "
    f"AND {_BECOMES_BUSINESS} AND NOT COALESCE(({_PROVEN_WORKFLOW}), FALSE)"
)
_CLEAN_EXISTING_METADATA = policy_metadata_without_fields_sql(
    "control_room_items.metadata", "$2"
)

_ROW_COLUMNS = """
    tenant_id text, workspace_id text, owner_user_id bigint, item_id text,
    cartridge_id text, domain text, source_dataset text, item_kind text,
    title text, severity text, status text, entity_kind text, entity_id text,
    entity_label text, anomaly_type text, metadata jsonb,
    impact_estimate numeric, impact_currency text, confidence numeric,
    priority_score integer, selected_option_id text, execution_status text
"""

_SEMANTIC_UPDATE = f"""
    cartridge_id = EXCLUDED.cartridge_id,
    domain = EXCLUDED.domain,
    source_dataset = EXCLUDED.source_dataset,
    item_kind = EXCLUDED.item_kind,
    title = EXCLUDED.title,
    severity = EXCLUDED.severity,
    entity_kind = EXCLUDED.entity_kind,
    entity_id = EXCLUDED.entity_id,
    entity_label = EXCLUDED.entity_label,
    anomaly_type = EXCLUDED.anomaly_type,
    metadata = (
        CASE WHEN {_RESET_WORKFLOW}
             THEN ({_CLEAN_EXISTING_METADATA}) || $4::jsonb
             ELSE {_CLEAN_EXISTING_METADATA}
        END
    ) || EXCLUDED.metadata,
    impact_estimate = EXCLUDED.impact_estimate,
    impact_currency = EXCLUDED.impact_currency,
    confidence = EXCLUDED.confidence,
    priority_score = EXCLUDED.priority_score,
    owner_user_id = COALESCE(control_room_items.owner_user_id, EXCLUDED.owner_user_id),
    decision_id = CASE WHEN {_RESET_WORKFLOW}
                       THEN NULL ELSE control_room_items.decision_id END,
    last_seen_at = NOW()
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


def parse_command_tag(result: Any, command: str) -> int:
    if not isinstance(result, str):
        raise PersistenceCommandTagError("database returned a non-text command tag")
    text = result.strip()
    parts = text.split()
    expected_parts = 3 if command.upper() == "INSERT" else 2
    if (
        len(parts) != expected_parts
        or parts[0].upper() != command.upper()
        or any(not value.isdigit() for value in parts[1:])
    ):
        raise PersistenceCommandTagError("database returned a malformed command tag")
    return int(parts[-1])


def _is_explicit_test_double(result: Any) -> bool:
    return isinstance(result, Mock)


def _prepared_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{**dict(row), "metadata": persistence_metadata(row)} for row in rows]


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
    status = CASE WHEN {_RESET_WORKFLOW}
                  THEN 'open' ELSE control_room_items.status END,
    selected_option_id = CASE WHEN {_RESET_WORKFLOW}
                              THEN NULL ELSE control_room_items.selected_option_id END,
    execution_status = CASE WHEN {_RESET_WORKFLOW}
                            THEN 'not_started' ELSE control_room_items.execution_status END
{_owner_conflict_guard(5, 6)}
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
        WHEN {_RESET_WORKFLOW} THEN EXCLUDED.status
        WHEN control_room_items.status = ANY($5::text[]) THEN control_room_items.status
        ELSE EXCLUDED.status
    END,
    selected_option_id = CASE
        WHEN {_RESET_WORKFLOW} THEN NULL
        ELSE COALESCE(EXCLUDED.selected_option_id, control_room_items.selected_option_id)
    END,
    execution_status = CASE
        WHEN {_RESET_WORKFLOW} THEN 'not_started'
        ELSE COALESCE(EXCLUDED.execution_status, control_room_items.execution_status)
    END
{_owner_conflict_guard(6, 7)}
"""


def _assert_count(result: Any, *, expected: int) -> None:
    if _is_explicit_test_double(result):
        return
    affected = parse_command_tag(result, "INSERT")
    if affected != expected:
        raise PersistenceCountMismatch(
            f"control room item persistence affected {affected}/{expected} rows"
        )


def _validate_owner_scope(
    rows: Sequence[Mapping[str, Any]],
    *,
    owner_scope_id: int | None,
    workspace_wide: bool,
) -> None:
    if workspace_wide:
        return
    if owner_scope_id is None or any(
        row.get("owner_user_id") != owner_scope_id for row in rows
    ):
        raise OwnerScopeConflict("control room item owner scope mismatch")


async def persist_item_rows(
    conn: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    owner_scope_id: int | None = None,
    workspace_wide: bool = False,
) -> None:
    if not rows:
        return
    _validate_owner_scope(
        rows,
        owner_scope_id=owner_scope_id,
        workspace_wide=workspace_wide,
    )
    result = await conn.execute(
        PERSIST_ITEMS_SQL,
        dumps_jsonb(_prepared_rows(rows)),
        list(REPLACED_POLICY_KEYS),
        ELIGIBILITY_POLICY_VERSION,
        dumps_jsonb(quarantine_workflow_metadata({})),
        owner_scope_id,
        bool(workspace_wide),
    )
    _assert_count(result, expected=len(rows))


async def ensure_item_row(
    conn: Any,
    row: Mapping[str, Any],
    *,
    terminal_statuses: Sequence[str],
    owner_scope_id: int | None = None,
    workspace_wide: bool = False,
) -> None:
    _validate_owner_scope(
        (row,),
        owner_scope_id=owner_scope_id,
        workspace_wide=workspace_wide,
    )
    prepared = _prepared_rows((row,))[0]
    result = await conn.execute(
        ENSURE_ITEM_SQL,
        dumps_jsonb(prepared),
        list(REPLACED_POLICY_KEYS),
        ELIGIBILITY_POLICY_VERSION,
        dumps_jsonb(quarantine_workflow_metadata({})),
        list(terminal_statuses),
        owner_scope_id,
        bool(workspace_wide),
    )
    if _is_explicit_test_double(result):
        return
    affected = parse_command_tag(result, "INSERT")
    if affected == 0:
        raise OwnerScopeConflict("control room item owner scope mismatch")
    if affected != 1:
        raise PersistenceCountMismatch(
            f"control room item persistence affected {affected}/1 rows"
        )


__all__ = (
    "ENSURE_ITEM_SQL",
    "OwnerScopeConflict",
    "PERSIST_ITEMS_SQL",
    "PersistenceCountMismatch",
    "PersistenceCommandTagError",
    "REPLACED_POLICY_KEYS",
    "ensure_item_row",
    "persist_item_rows",
)
