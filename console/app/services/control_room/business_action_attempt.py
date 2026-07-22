from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


REMOTE_ATTEMPT_KEY = "remote_attempt"


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def remote_attempt_status(row: Mapping[str, Any]) -> str:
    metadata = _json(row.get("metadata"))
    attempt = metadata.get(REMOTE_ATTEMPT_KEY)
    if not isinstance(attempt, Mapping):
        return ""
    return str(attempt.get("status") or "").strip().lower()


def has_remote_attempt(row: Mapping[str, Any]) -> bool:
    return remote_attempt_status(row) in {"started", "ambiguous"}


async def mark_remote_attempt_started(
    conn: Any,
    *,
    workspace_id: str,
    reservation_id: int,
    effective_key: str,
    adapter: str,
    target: str,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        UPDATE action_runs
           SET metadata = COALESCE(metadata, '{}'::jsonb)
                          || jsonb_build_object(
                              'remote_attempt',
                              jsonb_build_object(
                                  'status', 'started',
                                  'adapter', $4::text,
                                  'target', $5::text,
                                  'started_at', NOW()
                              )
                          ),
               updated_at = NOW()
         WHERE workspace_id = $1::uuid
           AND id = $2
           AND idempotency_key = $3
           AND status = 'pending'
         RETURNING *
        """,
        workspace_id,
        reservation_id,
        effective_key,
        adapter,
        target,
    )
    if not row:
        raise RuntimeError("remote attempt reservation was not pending")
    return dict(row)


async def mark_remote_attempt_ambiguous(
    conn: Any,
    *,
    workspace_id: str,
    reservation_id: int,
    effective_key: str,
    error_code: str,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        UPDATE action_runs
           SET metadata = COALESCE(metadata, '{}'::jsonb)
                          || jsonb_build_object(
                              'remote_attempt',
                              COALESCE(metadata -> 'remote_attempt', '{}'::jsonb)
                              || jsonb_build_object(
                                  'status', 'ambiguous',
                                  'error_code', $4::text,
                                  'ambiguous_at', NOW()
                              )
                          ),
               updated_at = NOW()
         WHERE workspace_id = $1::uuid
           AND id = $2
           AND idempotency_key = $3
           AND status = 'pending'
         RETURNING *
        """,
        workspace_id,
        reservation_id,
        effective_key,
        error_code[:120],
    )
    if not row:
        raise RuntimeError("ambiguous remote attempt reservation was not pending")
    return dict(row)


__all__ = (
    "has_remote_attempt",
    "mark_remote_attempt_ambiguous",
    "mark_remote_attempt_started",
    "remote_attempt_status",
)
