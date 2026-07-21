from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    quarantine_workflow_metadata,
    workflow_eligibility_provenance,
    workflow_has_eligible_provenance,
)


_WORKFLOW_STATUSES = frozenset({"decision_created", "approved", "resolved"})
_CONTROL_ROOM_ACTION_MARKERS = (
    "Decision creada desde Sala de Control",
    "Created from Control Room",
)


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


def _stage(row: Mapping[str, Any]) -> WorkflowStage:
    execution = str(row.get("execution_status") or "not_started")
    if execution not in {"", "not_started"}:
        return WorkflowStage.EXECUTED
    if str(row.get("status") or "") in {"approved", "resolved"}:
        return WorkflowStage.APPROVED
    if row.get("decision_id") is not None:
        return WorkflowStage.DECISION_CREATED
    return WorkflowStage.OPTION_SELECTED


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


async def workflow_metadata_patches(
    conn: Any, rows: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    if not rows or not callable(getattr(conn, "fetch", None)):
        return {}
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
        list(_CONTROL_ROOM_ACTION_MARKERS),
    )
    incoming = {
        (str(row.get("workspace_id") or ""), str(row.get("item_id") or "")): _item(row)
        for row in rows
    }
    patches: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in existing_rows:
        existing = dict(raw)
        key = (str(existing.get("workspace_id") or ""), str(existing["item_id"]))
        current = incoming.get(key)
        if current is None:
            continue
        metadata = _mapping(existing.get("metadata"))
        quarantine = _mapping(metadata.get(WORKFLOW_QUARANTINE_KEY))
        if quarantine:
            patches[key] = {WORKFLOW_QUARANTINE_KEY: quarantine}
            continue
        if not _has_workflow(existing):
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
        if (
            workflow_has_eligible_provenance(
                metadata,
                {**current, "workspace_id": key[0]},
                decision_id=decision_id,
            )
            and classify_business_item(current).eligible
        ):
            patches[key] = {DECISION_PROVENANCE_KEY: metadata[DECISION_PROVENANCE_KEY]}
            continue
        if has_modern_marker:
            patches[key] = {
                WORKFLOW_QUARANTINE_KEY: quarantine_workflow_metadata({})[
                    WORKFLOW_QUARANTINE_KEY
                ]
            }
            continue
        if (
            _legacy_is_demonstrable(existing)
            and classify_business_item(current).eligible
            and business_observation_fingerprint(_item(existing))
            == business_observation_fingerprint(current)
        ):
            patches[key] = {
                DECISION_PROVENANCE_KEY: workflow_eligibility_provenance(
                    current,
                    stage=_stage(existing),
                    workspace_id=key[0],
                    decision_id=decision_id,
                    option_id=str(existing.get("selected_option_id") or "") or None,
                )
            }
            continue
        patches[key] = {
            WORKFLOW_QUARANTINE_KEY: quarantine_workflow_metadata({})[
                WORKFLOW_QUARANTINE_KEY
            ]
        }
    return patches


__all__ = ("workflow_metadata_patches",)
