from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_action_markers import (
    CONTROL_ROOM_ACTION_MARKERS,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_observation_order import (
    OBSERVATION_ORDER_KEY,
    business_observation_order,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    business_observation_fingerprint,
    quarantine_workflow_metadata,
    workflow_eligibility_provenance,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_workflow_quarantine import (
    workflow_is_quarantined,
)
from app.services.control_room.business_workflow_state import (
    expected_workflow_stage,
)


_WORKFLOW_STATUSES = frozenset({"decision_created", "approved", "resolved"})


@dataclass(frozen=True)
class WorkflowReconciliation:
    patches: dict[tuple[str, str], dict[str, Any]]
    existing_orders: dict[tuple[str, str], str]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _order_has_instant(order: str) -> bool:
    parts = str(order or "").split("|", 2)
    return len(parts) > 1 and bool(parts[1])


def _item(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping(row.get("metadata"))
    item = {
        **metadata,
        **dict(row),
        "id": str(row.get("item_id") or row.get("id") or ""),
        "kind": str(row.get("item_kind") or row.get("kind") or ""),
        "metadata": metadata,
    }
    for field in ("tenant_id", "workspace_id"):
        if item.get(field) is not None:
            item[field] = str(item[field])
    return item


def _has_workflow(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("decision_id") is not None
        or row.get("selected_option_id")
        or str(row.get("execution_status") or "not_started") != "not_started"
        or str(row.get("status") or "") in _WORKFLOW_STATUSES
    )


def _legacy_is_demonstrable(existing: Mapping[str, Any]) -> bool:
    item = _item(existing)
    return bool(
        classify_business_item(item).eligible
        and existing.get("decision_id") is not None
        and str(existing.get("decision_workspace_id") or "")
        == str(existing.get("workspace_id") or "")
        and existing.get("has_control_room_action") is True
        and existing.get("has_control_room_event") is True
    )


def _quarantine_patch(
    existing: Mapping[str, Any],
    current: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    provenance = _mapping(metadata.get(DECISION_PROVENANCE_KEY))
    quarantine = _mapping(metadata.get(WORKFLOW_QUARANTINE_KEY))
    changed = bool(
        provenance.get("fingerprint")
        and str(provenance["fingerprint"]) != business_observation_fingerprint(current)
    )
    reason = str(quarantine.get("reason") or "").strip()
    if not reason:
        reason = "observation_changed" if changed else "workflow_not_business_eligible"
    stage = expected_workflow_stage(existing)
    quarantined = quarantine_workflow_metadata(
        metadata,
        item=_item(existing),
        decision_id=existing.get("decision_id"),
        stage=stage.value if stage is not None else "unknown",
        reason=reason,
    )
    return {WORKFLOW_QUARANTINE_KEY: quarantined[WORKFLOW_QUARANTINE_KEY]}


async def reconcile_workflow_metadata(
    conn: Any, rows: Sequence[Mapping[str, Any]]
) -> WorkflowReconciliation:
    if not rows or not callable(getattr(conn, "fetch", None)):
        return WorkflowReconciliation(patches={}, existing_orders={})
    keys = [
        {"workspace_id": str(row.get("workspace_id") or ""), "item_id": row["item_id"]}
        for row in rows
    ]
    existing_rows = await conn.fetch(
        """
        WITH requested AS (
            SELECT x.workspace_id::uuid AS workspace_id, x.item_id
              FROM jsonb_to_recordset($1::jsonb) AS x(workspace_id text, item_id text)
        )
        SELECT c.*, d.workspace_id::text AS decision_workspace_id,
               EXISTS(
                   SELECT 1 FROM decision_actions a
                    WHERE a.decision_id=d.id
                      AND a.action_text=ANY($2::text[])
               ) AS has_control_room_action,
               EXISTS(
                   SELECT 1 FROM control_room_item_events e
                    WHERE e.workspace_id=c.workspace_id
                      AND e.item_id=c.item_id
                      AND e.event_type='decision_created'
                      AND e.metadata->>'decision_id'=d.id::text
               ) AS has_control_room_event
          FROM requested r
          JOIN control_room_items c
            ON c.workspace_id=r.workspace_id AND c.item_id=r.item_id
          LEFT JOIN decisions d ON d.id=c.decision_id
         FOR UPDATE OF c
        """,
        json.dumps(keys),
        list(CONTROL_ROOM_ACTION_MARKERS),
    )
    incoming = {
        (str(row.get("workspace_id") or ""), str(row.get("item_id") or "")): _item(row)
        for row in rows
    }
    patches: dict[tuple[str, str], dict[str, Any]] = {}
    existing_orders: dict[tuple[str, str], str] = {}
    for raw in existing_rows:
        existing = dict(raw)
        key = (str(existing.get("workspace_id") or ""), str(existing["item_id"]))
        current = incoming.get(key)
        if current is None:
            continue
        metadata = _mapping(existing.get("metadata"))
        existing_order = business_observation_order(_item(existing))
        current_order = business_observation_order(current)
        has_modern_fingerprint = bool(metadata.get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY))
        has_observation_order = bool(metadata.get(OBSERVATION_ORDER_KEY))
        if (
            _order_has_instant(existing_order)
            or has_modern_fingerprint
            or has_observation_order
        ):
            existing_orders[key] = existing_order
        if (has_observation_order or has_modern_fingerprint) and (
            current_order < existing_order
        ):
            continue
        quarantine = _mapping(metadata.get(WORKFLOW_QUARANTINE_KEY))
        if not _has_workflow(existing):
            if quarantine:
                patches[key] = {WORKFLOW_QUARANTINE_KEY: quarantine}
            continue
        decision_id = existing.get("decision_id")
        has_modern_marker = any(
            key in metadata
            for key in (
                CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
                DECISION_PROVENANCE_KEY,
                WORKFLOW_QUARANTINE_KEY,
            )
        )
        expected_stage = expected_workflow_stage(existing)
        provenance_item = {
            **current,
            "workspace_id": key[0],
            "selected_option_id": existing.get("selected_option_id"),
        }
        if (
            expected_stage is not None
            and workflow_has_eligible_provenance(
                metadata,
                provenance_item,
                decision_id=decision_id,
                use_stored_fingerprint=not (
                    has_observation_order or has_modern_fingerprint
                ),
                allowed_stages={expected_stage},
            )
            and classify_business_item(current).eligible
            and not workflow_is_quarantined(existing)
        ):
            patches[key] = {
                **({WORKFLOW_QUARANTINE_KEY: quarantine} if quarantine else {}),
                DECISION_PROVENANCE_KEY: metadata[DECISION_PROVENANCE_KEY],
            }
            continue
        if has_modern_marker:
            patches[key] = _quarantine_patch(existing, current, metadata)
            continue
        if (
            _legacy_is_demonstrable(existing)
            and classify_business_item(current).eligible
            and business_observation_fingerprint(_item(existing))
            == business_observation_fingerprint(current)
            and (stage := expected_workflow_stage(existing)) is not None
        ):
            patches[key] = {
                DECISION_PROVENANCE_KEY: workflow_eligibility_provenance(
                    current,
                    stage=stage,
                    workspace_id=key[0],
                    decision_id=decision_id,
                    option_id=str(existing.get("selected_option_id") or "") or None,
                )
            }
            continue
        patches[key] = _quarantine_patch(existing, current, metadata)
    return WorkflowReconciliation(
        patches=patches,
        existing_orders=existing_orders,
    )


async def workflow_metadata_patches(
    conn: Any, rows: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    return (await reconcile_workflow_metadata(conn, rows)).patches


__all__ = (
    "WorkflowReconciliation",
    "reconcile_workflow_metadata",
    "workflow_metadata_patches",
)
