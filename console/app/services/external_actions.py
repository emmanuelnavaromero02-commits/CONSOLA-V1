from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth
from app.services.db_scope import scoped_db_for_user
from app.services.permissions import has_permission, user_role


ACTION_STATUSES = {
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "dry_run_ready",
    "executing",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
}
TERMINAL_STATUSES = {"rejected", "succeeded", "failed", "cancelled", "expired"}
MUTATION_OPERATIONS = {"propose", "dry_run", "approve", "reject", "execute", "cancel"}
SENSITIVE_KEY_RE = re.compile(
    r"(password|token|api[_-]?key|secret|credential|private[_-]?key)",
    re.IGNORECASE,
)
BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")
MAX_PAYLOAD_BYTES = int(os.environ.get("EXTERNAL_ACTION_MAX_PAYLOAD_BYTES", "16384"))
MAX_PAYLOAD_DEPTH = int(os.environ.get("EXTERNAL_ACTION_MAX_PAYLOAD_DEPTH", "6"))
MAX_LIST_ITEMS = int(os.environ.get("EXTERNAL_ACTION_MAX_LIST_ITEMS", "100"))
MAX_STRING_BYTES = int(os.environ.get("EXTERNAL_ACTION_MAX_STRING_BYTES", "4096"))
DEFAULT_TTL_SECONDS = int(
    os.environ.get("EXTERNAL_ACTION_PENDING_TTL_SECONDS", "86400")
)
GLOBAL_WRITEBACK_FLAG = "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK"
SANDBOX_FLAG = "EXTERNAL_ACTION_SANDBOX_ENABLED"
SANDBOX_ADAPTER = "sandbox"
ADMIN_SELF_APPROVAL_ROLES = {"admin", "owner", "super_admin"}


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def sandbox_enabled() -> bool:
    return _truthy(os.environ.get(SANDBOX_FLAG), default=True)


def external_writeback_enabled() -> bool:
    return _truthy(os.environ.get(GLOBAL_WRITEBACK_FLAG), default=False)


def workspace_kill_switch_available() -> bool:
    return bool(os.environ.get("EXTERNAL_ACTION_WORKSPACE_KILL_SWITCH_SOURCE"))


def _user_id(user: dict | None) -> int | None:
    raw = (user or {}).get("id")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(400, "payload must be an object")
    return value


def _json_size(value: Any) -> int:
    return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    return _row_dict(row).get(key, default)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _looks_like_large_base64(value: str) -> bool:
    compact = "".join(value.split())
    if len(compact) < 512 or len(compact) % 4 != 0 or not BASE64_RE.fullmatch(compact):
        return False
    try:
        base64.b64decode(compact, validate=True)
    except Exception:
        return False
    return True


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if SENSITIVE_KEY_RE.search(text_key):
                redacted[text_key] = "<redacted>"
            else:
                redacted[text_key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, str) and _looks_like_large_base64(value):
        return "<redacted-binary>"
    return value


def validate_payload(value: Any, *, field: str = "payload") -> dict[str, Any]:
    payload = _as_dict(value)
    if _json_size(payload) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, f"{field} exceeds maximum size")

    def walk(node: Any, depth: int, path: str) -> None:
        if depth > MAX_PAYLOAD_DEPTH:
            raise HTTPException(400, f"{field} exceeds maximum depth")
        if isinstance(node, dict):
            for key, item in node.items():
                text_key = str(key)
                if SENSITIVE_KEY_RE.search(text_key):
                    raise HTTPException(400, f"{field} contains sensitive key")
                walk(item, depth + 1, f"{path}.{text_key}")
            return
        if isinstance(node, list):
            if len(node) > MAX_LIST_ITEMS:
                raise HTTPException(400, f"{field} contains too many items")
            for index, item in enumerate(node):
                walk(item, depth + 1, f"{path}[{index}]")
            return
        if isinstance(node, bytes):
            raise HTTPException(400, f"{field} cannot contain binary values")
        if isinstance(node, str):
            if len(node.encode("utf-8")) > MAX_STRING_BYTES:
                raise HTTPException(413, f"{field} string value exceeds maximum size")
            if _looks_like_large_base64(node):
                raise HTTPException(400, f"{field} cannot contain encoded binary data")

    walk(payload, 0, field)
    return payload


def _bounded_text(value: Any, *, field: str, max_length: int = 128) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(400, f"{field} is required")
    if len(text) > max_length:
        raise HTTPException(400, f"{field} is too long")
    return text


def _idempotency_key(value: Any | None) -> str:
    text = str(value or "").strip()
    if not text:
        return f"generated:{uuid.uuid4()}"
    if len(text) > 160:
        raise HTTPException(400, "idempotency_key is too long")
    return text


def _serialize_action(row: Any) -> dict[str, Any]:
    data = _row_dict(row)
    for key in (
        "id",
        "tenant_id",
        "workspace_id",
        "created_at",
        "updated_at",
        "approved_at",
        "rejected_at",
        "cancelled_at",
        "completed_at",
        "expires_at",
    ):
        if data.get(key) is not None:
            data[key] = str(data[key])
    for key in (
        "payload",
        "dry_run_payload",
        "dry_run_result",
        "execution_result",
        "metadata",
    ):
        data[key] = _json_object(data.get(key))
    return data


async def _record_event(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    action_id: str,
    event_type: str,
    status: str,
    user: dict,
    metadata: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO external_action_events (
            tenant_id, workspace_id, action_id, event_type, status,
            actor_id, actor_email, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        """,
        tenant_id,
        workspace_id,
        action_id,
        event_type,
        status,
        _user_id(user),
        user.get("email"),
        json.dumps(redact_sensitive(metadata or {}), default=str),
    )


async def _audit(
    *,
    user: dict,
    action: str,
    action_id: str | None,
    status: str,
    metadata: dict[str, Any] | None = None,
    connection: Any | None = None,
) -> None:
    await audit_service.record_event(
        user_id=_user_id(user),
        email=user.get("email"),
        action=action,
        resource_type="external_action",
        resource_id=action_id,
        status=status,
        metadata=redact_sensitive(metadata or {}),
        critical=True,
        connection=connection,
    )


async def _idempotency_response(
    conn: Any,
    *,
    workspace_id: str,
    action_id: str | None,
    operation: str,
    key: str,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT action_id, operation, response, status
          FROM external_action_idempotency_keys
         WHERE workspace_id = $1
           AND idempotency_key = $2
        """,
        workspace_id,
        key,
    )
    if not row:
        return None
    data = _row_dict(row)
    existing_action_id = (
        str(data["action_id"]) if data.get("action_id") is not None else None
    )
    if existing_action_id == action_id and str(row["operation"]) == operation:
        return _json_object(data.get("response"))
    raise HTTPException(
        409, "idempotency key is already used for another action or operation"
    )


async def _store_idempotency(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    action_id: str | None,
    operation: str,
    key: str,
    response: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO external_action_idempotency_keys (
            tenant_id, workspace_id, action_id, operation, idempotency_key, response, status
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'completed')
        ON CONFLICT (workspace_id, idempotency_key) DO UPDATE
        SET response = CASE
                WHEN external_action_idempotency_keys.action_id = EXCLUDED.action_id
                 AND external_action_idempotency_keys.operation = EXCLUDED.operation
                THEN EXCLUDED.response
                ELSE external_action_idempotency_keys.response
            END,
            updated_at = NOW()
        """,
        tenant_id,
        workspace_id,
        action_id,
        operation,
        key,
        json.dumps(redact_sensitive(response), default=str),
    )


async def _fetch_action_for_update(
    conn: Any, *, workspace_id: str, action_id: str
) -> Any:
    row = await conn.fetchrow(
        """
        SELECT *
          FROM external_actions
         WHERE id = $1
           AND workspace_id = $2
         FOR UPDATE
        """,
        action_id,
        workspace_id,
    )
    if not row:
        raise HTTPException(404, "external action not found")
    return row


def _ensure_adapter_allowed(adapter_name: str, *, execute: bool = False) -> None:
    adapter = adapter_name.strip().lower()
    if adapter == SANDBOX_ADAPTER:
        if execute and not sandbox_enabled():
            raise HTTPException(409, "sandbox external actions are disabled")
        return
    if not external_writeback_enabled():
        raise HTTPException(409, "real external write-back adapters are disabled")
    raise HTTPException(403, "adapter is not allowlisted for Prompt 18A")


def _expires_at(ttl_seconds: Any | None) -> datetime:
    try:
        ttl = int(ttl_seconds or DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_SECONDS
    ttl = max(60, min(ttl, 604800))
    return datetime.now(UTC) + timedelta(seconds=ttl)


def _is_expired(row: Any) -> bool:
    expires_at = _row_get(row, "expires_at")
    if not expires_at:
        return False
    if isinstance(expires_at, str):
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        parsed = expires_at
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


async def _mark_expired(
    conn: Any,
    *,
    row: Any,
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
) -> None:
    if row["status"] == "expired":
        return
    await conn.execute(
        """
        UPDATE external_actions
           SET status = 'expired',
               updated_at = NOW(),
               completed_at = COALESCE(completed_at, NOW())
         WHERE id = $1
           AND workspace_id = $2
        """,
        row["id"],
        workspace_id,
    )
    await _record_event(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action_id=str(row["id"]),
        event_type="expired",
        status="expired",
        user=user,
    )


def _sandbox_dry_run(action: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "adapter": SANDBOX_ADAPTER,
        "mode": "dry_run",
        "external_write": False,
        "validated": True,
        "action_id": str(action["id"]),
        "action_type": action["action_type"],
        "payload_echo": redact_sensitive(payload),
    }


def _sandbox_execute(action: dict[str, Any]) -> dict[str, Any]:
    payload = action.get("payload") or {}
    outcome = str(
        payload.get("sandbox_outcome") or payload.get("simulate") or "success"
    ).lower()
    if outcome in {"failure", "fail", "error"}:
        return {
            "ok": False,
            "adapter": SANDBOX_ADAPTER,
            "mode": "execute",
            "external_write": False,
            "error_code": "sandbox_failure",
            "message": "Sandbox adapter simulated failure.",
        }
    return {
        "ok": True,
        "adapter": SANDBOX_ADAPTER,
        "mode": "execute",
        "external_write": False,
        "message": "Sandbox adapter simulated success.",
    }


async def propose(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload or {})
    source_type = _bounded_text(body.get("source_type"), field="source_type")
    source_id = _bounded_text(body.get("source_id"), field="source_id", max_length=256)
    action_type = _bounded_text(body.get("action_type"), field="action_type")
    adapter_name = _bounded_text(
        body.get("adapter_name") or SANDBOX_ADAPTER, field="adapter_name"
    ).lower()
    action_payload = validate_payload(body.get("payload") or {})
    dry_run_payload = validate_payload(
        body.get("dry_run_payload") or {}, field="dry_run_payload"
    )
    metadata = validate_payload(body.get("metadata") or {}, field="metadata")
    key = _idempotency_key(body.get("idempotency_key"))
    _ensure_adapter_allowed(adapter_name)
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        existing = await _idempotency_response(
            conn,
            workspace_id=workspace_id,
            action_id=None,
            operation="propose",
            key=key,
        )
        if existing:
            return existing
        row = await conn.fetchrow(
            """
            INSERT INTO external_actions (
                tenant_id, workspace_id, source_type, source_id, action_type,
                adapter_name, payload, dry_run_payload, idempotency_key,
                status, created_by, expires_at, metadata
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7::jsonb, $8::jsonb, $9,
                'pending_approval', $10, $11, $12::jsonb
            )
            RETURNING *
            """,
            tenant_id,
            workspace_id,
            source_type,
            source_id,
            action_type,
            adapter_name,
            json.dumps(redact_sensitive(action_payload), default=str),
            json.dumps(redact_sensitive(dry_run_payload), default=str),
            key,
            _user_id(user),
            _expires_at(body.get("expires_in_seconds")),
            json.dumps(redact_sensitive(metadata), default=str),
        )
        response = {"action": _serialize_action(row)}
        await _record_event(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=str(row["id"]),
            event_type="proposed",
            status="pending_approval",
            user=user,
            metadata={"idempotency_key": key},
        )
        await _store_idempotency(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=None,
            operation="propose",
            key=key,
            response=response,
        )
        await _audit(
            user=user,
            action="external_action.propose",
            action_id=response["action"]["id"],
            status="success",
            connection=conn,
        )
    return response


async def list_actions(user: dict, *, limit: int = 50) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        rows = await conn.fetch(
            """
            SELECT *
              FROM external_actions
             WHERE workspace_id = $1
             ORDER BY created_at DESC
             LIMIT $2
            """,
            workspace_id,
            int(max(1, min(limit, 250))),
        )
    return {"actions": [_serialize_action(row) for row in rows]}


async def get_action(user: dict, action_id: str) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        row = await conn.fetchrow(
            "SELECT * FROM external_actions WHERE id = $1 AND workspace_id = $2",
            action_id,
            workspace_id,
        )
        if not row:
            raise HTTPException(404, "external action not found")
        events = await conn.fetch(
            """
            SELECT event_type, status, actor_id, actor_email, metadata, created_at
              FROM external_action_events
             WHERE action_id = $1
               AND workspace_id = $2
             ORDER BY created_at ASC, id ASC
            """,
            action_id,
            workspace_id,
        )
    action = _serialize_action(row)
    action["events"] = [
        {
            **dict(event),
            "created_at": str(event["created_at"])
            if _row_get(event, "created_at")
            else None,
            "metadata": _json_object(_row_get(event, "metadata")),
        }
        for event in events
    ]
    return {"action": action}


async def dry_run(
    user: dict, action_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    body = dict(payload or {})
    dry_run_payload = validate_payload(
        body.get("dry_run_payload") or {}, field="dry_run_payload"
    )
    key = _idempotency_key(body.get("idempotency_key"))
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        replay = await _idempotency_response(
            conn,
            workspace_id=workspace_id,
            action_id=action_id,
            operation="dry_run",
            key=key,
        )
        if replay:
            return replay
        row = await _fetch_action_for_update(
            conn, workspace_id=workspace_id, action_id=action_id
        )
        if _is_expired(row):
            await _mark_expired(
                conn, row=row, tenant_id=tenant_id, workspace_id=workspace_id, user=user
            )
            raise HTTPException(409, "external action is expired")
        if row["status"] in TERMINAL_STATUSES:
            raise HTTPException(409, "terminal external action cannot dry-run")
        _ensure_adapter_allowed(str(row["adapter_name"]))
        action = _serialize_action(row)
        effective_payload = (
            dry_run_payload
            or action.get("dry_run_payload")
            or action.get("payload")
            or {}
        )
        result = _sandbox_dry_run(action, effective_payload)
        updated = await conn.fetchrow(
            """
            UPDATE external_actions
               SET status = 'dry_run_ready',
                   dry_run_payload = $3::jsonb,
                   dry_run_result = $4::jsonb,
                   updated_at = NOW()
             WHERE id = $1
               AND workspace_id = $2
             RETURNING *
            """,
            action_id,
            workspace_id,
            json.dumps(redact_sensitive(effective_payload), default=str),
            json.dumps(result, default=str),
        )
        response = {"action": _serialize_action(updated), "dry_run_result": result}
        await _record_event(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            event_type="dry_run_executed",
            status="dry_run_ready",
            user=user,
            metadata={"idempotency_key": key, "result": result},
        )
        await _store_idempotency(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            operation="dry_run",
            key=key,
            response=response,
        )
    await _audit(
        user=user,
        action="external_action.dry_run",
        action_id=action_id,
        status="success",
    )
    return response


async def approve(
    user: dict, action_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    key = _idempotency_key((payload or {}).get("idempotency_key"))
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        replay = await _idempotency_response(
            conn,
            workspace_id=workspace_id,
            action_id=action_id,
            operation="approve",
            key=key,
        )
        if replay:
            return replay
        row = await _fetch_action_for_update(
            conn, workspace_id=workspace_id, action_id=action_id
        )
        if _is_expired(row):
            await _mark_expired(
                conn, row=row, tenant_id=tenant_id, workspace_id=workspace_id, user=user
            )
            raise HTTPException(409, "external action is expired")
        if row["status"] in TERMINAL_STATUSES or row["status"] == "executing":
            raise HTTPException(409, "external action cannot be approved")
        actor_id = _user_id(user)
        if (
            _row_get(row, "created_by") == actor_id
            and user_role(user) not in ADMIN_SELF_APPROVAL_ROLES
        ):
            raise HTTPException(
                403, "maker/checker approval requires a different approver"
            )
        updated = await conn.fetchrow(
            """
            UPDATE external_actions
               SET status = 'approved',
                   approved_by = $3,
                   approved_at = NOW(),
                   updated_at = NOW()
             WHERE id = $1
               AND workspace_id = $2
             RETURNING *
            """,
            action_id,
            workspace_id,
            actor_id,
        )
        response = {"action": _serialize_action(updated)}
        await _record_event(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            event_type="approved",
            status="approved",
            user=user,
            metadata={"idempotency_key": key},
        )
        await _store_idempotency(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            operation="approve",
            key=key,
            response=response,
        )
    await _audit(
        user=user,
        action="external_action.approve",
        action_id=action_id,
        status="success",
    )
    return response


async def reject(user: dict, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await _terminal_update(
        user,
        action_id,
        payload or {},
        operation="reject",
        status="rejected",
        event_type="rejected",
        actor_column="rejected_by",
        time_column="rejected_at",
    )


async def cancel(user: dict, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await _terminal_update(
        user,
        action_id,
        payload or {},
        operation="cancel",
        status="cancelled",
        event_type="cancelled",
        actor_column="cancelled_by",
        time_column="cancelled_at",
    )


async def _terminal_update(
    user: dict,
    action_id: str,
    payload: dict[str, Any],
    *,
    operation: str,
    status: str,
    event_type: str,
    actor_column: str,
    time_column: str,
) -> dict[str, Any]:
    key = _idempotency_key(payload.get("idempotency_key"))
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        replay = await _idempotency_response(
            conn,
            workspace_id=workspace_id,
            action_id=action_id,
            operation=operation,
            key=key,
        )
        if replay:
            return replay
        row = await _fetch_action_for_update(
            conn, workspace_id=workspace_id, action_id=action_id
        )
        if row["status"] in {"succeeded", "failed", "cancelled", "expired"}:
            raise HTTPException(409, "terminal external action cannot change state")
        updated = await conn.fetchrow(
            f"""
            UPDATE external_actions
               SET status = $3,
                   {actor_column} = $4,
                   {time_column} = NOW(),
                   completed_at = NOW(),
                   updated_at = NOW()
             WHERE id = $1
               AND workspace_id = $2
             RETURNING *
            """,
            action_id,
            workspace_id,
            status,
            _user_id(user),
        )
        response = {"action": _serialize_action(updated)}
        await _record_event(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            event_type=event_type,
            status=status,
            user=user,
            metadata={"idempotency_key": key},
        )
        await _store_idempotency(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            operation=operation,
            key=key,
            response=response,
        )
    await _audit(
        user=user,
        action=f"external_action.{operation}",
        action_id=action_id,
        status="success",
    )
    return response


async def execute(
    user: dict, action_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    body = dict(payload or {})
    key = _idempotency_key(body.get("idempotency_key"))
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        replay = await _idempotency_response(
            conn,
            workspace_id=workspace_id,
            action_id=action_id,
            operation="execute",
            key=key,
        )
        if replay:
            return replay
        row = await _fetch_action_for_update(
            conn, workspace_id=workspace_id, action_id=action_id
        )

        # Last defensive line: revalidate inside this scoped transaction.
        if not has_permission(user, "control_room.execute"):
            raise HTTPException(403, "permission required: control_room.execute")
        if str(_row_get(row, "workspace_id")) != workspace_id:
            raise HTTPException(404, "external action not found")
        if row["status"] != "approved":
            if _is_expired(row):
                await _mark_expired(
                    conn,
                    row=row,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user=user,
                )
                raise HTTPException(409, "external action is expired")
            raise HTTPException(409, "external action must be approved before execute")
        if _is_expired(row):
            await _mark_expired(
                conn, row=row, tenant_id=tenant_id, workspace_id=workspace_id, user=user
            )
            raise HTTPException(409, "external action is expired")
        _ensure_adapter_allowed(str(row["adapter_name"]), execute=True)
        action_payload = validate_payload(_json_object(_row_get(row, "payload")))
        if not _json_object(_row_get(row, "dry_run_result")):
            raise HTTPException(409, "dry-run is required before execute")

        await conn.execute(
            """
            UPDATE external_actions
               SET status = 'executing',
                   updated_at = NOW()
             WHERE id = $1
               AND workspace_id = $2
            """,
            action_id,
            workspace_id,
        )
        await _record_event(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            event_type="execute_started",
            status="executing",
            user=user,
            metadata={"idempotency_key": key},
        )
        result = _sandbox_execute({**_serialize_action(row), "payload": action_payload})
        status = "succeeded" if result.get("ok") else "failed"
        updated = await conn.fetchrow(
            """
            UPDATE external_actions
               SET status = $3,
                   execution_result = $4::jsonb,
                   updated_at = NOW(),
                   completed_at = NOW()
             WHERE id = $1
               AND workspace_id = $2
             RETURNING *
            """,
            action_id,
            workspace_id,
            status,
            json.dumps(redact_sensitive(result), default=str),
        )
        response = {"action": _serialize_action(updated), "execution_result": result}
        await _record_event(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            event_type="execute_succeeded" if result.get("ok") else "execute_failed",
            status=status,
            user=user,
            metadata={"idempotency_key": key, "result": result},
        )
        await _store_idempotency(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_id=action_id,
            operation="execute",
            key=key,
            response=response,
        )
    await _audit(
        user=user, action="external_action.execute", action_id=action_id, status=status
    )
    return response
