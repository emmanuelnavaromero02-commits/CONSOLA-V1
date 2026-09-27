from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_item_persistence_sql import (
    ENSURE_ITEM_SQL,
    LAPSE_EXCEPTIONS_SQL,
    PERSIST_ITEMS_SQL,
)
from app.services.control_room.business_observation_order import (
    OBSERVATION_ORDER_BASELINE_KEY,
    OBSERVATION_ORDER_KEY,
    business_observation_order,
)
from app.services.control_room.business_policy_metadata import REPLACED_POLICY_KEYS
from app.services.control_room.business_serialization import dumps_jsonb
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    persistence_metadata,
)
from app.services.control_room.business_workflow_reconciliation import (
    reconcile_workflow_metadata,
)


class OwnerScopeConflict(RuntimeError):
    pass


class PersistenceCountMismatch(RuntimeError):
    pass


class PersistenceCommandTagError(RuntimeError):
    pass


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
        or (command.upper() == "INSERT" and parts[1] != "0")
    ):
        raise PersistenceCommandTagError("database returned a malformed command tag")
    return int(parts[-1])


def _prepared_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for row in rows:
        metadata = persistence_metadata(row)
        metadata.pop(OBSERVATION_ORDER_BASELINE_KEY, None)
        metadata[OBSERVATION_ORDER_KEY] = business_observation_order(row)
        prepared.append({**dict(row), "metadata": metadata})
    return prepared


async def _reconciled(
    conn: Any, rows: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    prepared = _prepared_rows(rows)
    reconciliation = await reconcile_workflow_metadata(conn, prepared)
    lapse_candidates: dict[str, list[str]] = {}
    for row in prepared:
        key = (str(row.get("workspace_id") or ""), str(row.get("item_id") or ""))
        if patch := reconciliation.patches.get(key):
            row["metadata"] = {**dict(row["metadata"]), **patch}
        if baseline := reconciliation.existing_orders.get(key):
            row["metadata"][OBSERVATION_ORDER_BASELINE_KEY] = baseline
        approved = reconciliation.approved_exceptions.get(key)
        incoming = str(row["metadata"].get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or "")
        if approved is not None and approved != incoming:
            lapse_candidates.setdefault(key[0], []).append(key[1])
    return prepared, lapse_candidates


async def _reconciled_rows(
    conn: Any, rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return (await _reconciled(conn, rows))[0]


async def _lapse_exceptions(
    conn: Any,
    candidates: Mapping[str, Sequence[str]],
    *,
    owner_scope_id: int | None,
    workspace_wide: bool,
) -> None:
    for workspace_id, item_ids in sorted(candidates.items()):
        await conn.execute(
            LAPSE_EXCEPTIONS_SQL,
            workspace_id,
            sorted(set(item_ids)),
            None if workspace_wide else owner_scope_id,
        )


def _assert_count(result: Any, *, expected: int) -> None:
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
    prepared, lapse_candidates = await _reconciled(conn, rows)
    result = await conn.execute(
        PERSIST_ITEMS_SQL,
        dumps_jsonb(prepared),
        list(REPLACED_POLICY_KEYS),
        owner_scope_id,
        bool(workspace_wide),
    )
    _assert_count(result, expected=len(rows))
    await _lapse_exceptions(
        conn,
        lapse_candidates,
        owner_scope_id=owner_scope_id,
        workspace_wide=workspace_wide,
    )


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
    prepared = (await _reconciled_rows(conn, (row,)))[0]
    result = await conn.execute(
        ENSURE_ITEM_SQL,
        dumps_jsonb(prepared),
        list(REPLACED_POLICY_KEYS),
        list(terminal_statuses),
        owner_scope_id,
        bool(workspace_wide),
    )
    affected = parse_command_tag(result, "INSERT")
    if affected == 0:
        raise OwnerScopeConflict("control room item owner scope mismatch")
    if affected != 1:
        raise PersistenceCountMismatch(
            f"control room item persistence affected {affected}/1 rows"
        )


__all__ = (
    "ENSURE_ITEM_SQL",
    "LAPSE_EXCEPTIONS_SQL",
    "OwnerScopeConflict",
    "PERSIST_ITEMS_SQL",
    "PersistenceCountMismatch",
    "PersistenceCommandTagError",
    "REPLACED_POLICY_KEYS",
    "ensure_item_row",
    "persist_item_rows",
)
