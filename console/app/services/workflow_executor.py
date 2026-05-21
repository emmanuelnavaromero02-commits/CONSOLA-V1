"""Durable workflow executor for Copilot planned workflows.

The planner stores its plan in ``workflow_runs.plan`` and materialises
one row per step in ``workflow_steps``. This executor owns the runtime
state machine: sequential execution, approval pauses, retries, audit
trail, cancellation checks, and fail-fast semantics.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth, mcp_registry, permissions, tool_manifest, tool_policy

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.0
DEFAULT_STEP_TIMEOUT_SECONDS = 60

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STEP_STATUSES = {"pending", "waiting_approval"}


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _split_tool(tool: str, args: dict[str, Any]) -> tuple[str, str]:
    """Return ``(server_id, bare_tool_name)`` from a planned tool.

    The planner is prompted with ``server.tool`` names. Older/manual
    rows may store the server in args; support that shape without
    silently guessing a server for ambiguous bare tool names.
    """
    if "." in tool:
        server_id, bare_name = tool.split(".", 1)
        return server_id, bare_name
    if "___" in tool:
        server_id, bare_name = tool.split("___", 1)
        return server_id, bare_name
    server_id = args.get("server_id") or args.get("server")
    if server_id:
        return str(server_id), tool
    raise ValueError("workflow step tool must be server.tool")


def _step_timeout(args: dict[str, Any]) -> int:
    raw = args.get("timeout_seconds") or args.get("timeout_s")
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_STEP_TIMEOUT_SECONDS
    return max(1, min(timeout, 300))


def _is_approved(step: dict[str, Any]) -> bool:
    result = _json_load(step.get("result"), {})
    # Approval must be server-side state written by approve_step().
    # Never trust args.approved / args._approved: args come from the
    # LLM-authored plan and would let the plan approve itself.
    return result.get("approved") is True


def _scrub_args(args: Any) -> Any:
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for key, value in args.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("password", "secret", "token", "api_key", "apikey")):
                out[key] = "***"
            else:
                out[key] = _scrub_args(value)
        return out
    if isinstance(args, list):
        return [_scrub_args(item) for item in args]
    return args


def _safe_error(error: Any) -> str:
    text = str(error or "tool invocation failed")[:500]
    lowered = text.lower()
    if any(token in lowered for token in ("password", "secret", "token", "api_key", "apikey", "authorization")):
        return "upstream error redacted"
    return text


async def _live_tool_schema(server_id: str, tool: str) -> dict[str, Any] | None:
    try:
        tools = await mcp_registry.list_tools(server_id)
    except Exception:
        return None
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name == tool or name.endswith(f"__{tool}") or name.endswith(f".{tool}"):
            schema = item.get("input_schema")
            return schema if isinstance(schema, dict) else {}
    return None


async def _record_step_audit(
    *,
    user: dict[str, Any],
    workflow_id: str,
    step_idx: int,
    tool_name: str | None,
    args: dict[str, Any] | None,
    risk_level: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="copilot.workflow.step",
        resource_type="workflow_run",
        resource_id=str(workflow_id),
        status=status,
        metadata={"step_index": step_idx, **(metadata or {})},
        tool_name=tool_name,
        tool_args=_scrub_args(args or {}),
        tool_result_status=status,
        risk_level=risk_level,
        critical=True,
    )


async def _load_workflow(pool: Any, workflow_id: str, user: dict[str, Any]) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT id, user_id, intent, plan, status, current_step, error,
               created_at, finished_at, started_at, completed_at, step_results
          FROM workflow_runs
         WHERE id = $1 AND user_id = $2
        """,
        workflow_id,
        user["id"],
    )
    if row is None:
        raise HTTPException(404, "Workflow not found")
    return _row_to_dict(row)


async def _load_steps(pool: Any, workflow_id: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, workflow_id, step_idx, description, tool, args,
               result, status, started_at, finished_at
          FROM workflow_steps
         WHERE workflow_id = $1
         ORDER BY step_idx
        """,
        workflow_id,
    )
    steps = [_row_to_dict(row) for row in rows]
    for step in steps:
        step["args"] = _json_load(step.get("args"), {})
        step["result"] = _json_load(step.get("result"), {})
    return steps


async def _materialise_steps_if_needed(pool: Any, workflow: dict[str, Any]) -> None:
    workflow_id = str(workflow["id"])
    existing = await pool.fetchval(
        "SELECT COUNT(*) FROM workflow_steps WHERE workflow_id = $1",
        workflow_id,
    )
    if existing:
        return
    plan = _json_load(workflow.get("plan"), [])
    if not isinstance(plan, list):
        return
    for idx, item in enumerate(plan):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or f"Step {idx + 1}")[:500]
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        await pool.execute(
            """
            INSERT INTO workflow_steps
                (workflow_id, step_idx, description, tool, args, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, 'pending')
            ON CONFLICT (workflow_id, step_idx) DO NOTHING
            """,
            workflow_id,
            idx,
            description,
            item.get("tool"),
            json.dumps(args),
        )


async def _refresh_step_results(pool: Any, workflow_id: str) -> list[dict[str, Any]]:
    steps = await _load_steps(pool, workflow_id)
    results = [
        {
            "step_idx": step["step_idx"],
            "tool": step.get("tool"),
            "status": step.get("status"),
            "result": _scrub_args(step.get("result")),
            "started_at": str(step.get("started_at")) if step.get("started_at") else None,
            "finished_at": str(step.get("finished_at")) if step.get("finished_at") else None,
        }
        for step in steps
    ]
    await pool.execute(
        "UPDATE workflow_runs SET step_results = $2::jsonb WHERE id = $1",
        workflow_id,
        json.dumps(results, default=str),
    )
    return results


async def _mark_remaining_skipped(pool: Any, workflow_id: str, after_idx: int) -> None:
    await pool.execute(
        """
        UPDATE workflow_steps
           SET status = 'skipped', finished_at = COALESCE(finished_at, NOW())
         WHERE workflow_id = $1
           AND step_idx > $2
           AND status IN ('pending', 'waiting_approval')
        """,
        workflow_id,
        after_idx,
    )


async def _claim_step(pool: Any, workflow_id: str, step_idx: int) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """
        UPDATE workflow_steps
           SET status = 'running', started_at = COALESCE(started_at, NOW())
         WHERE workflow_id = $1
           AND step_idx = $2
           AND status = 'pending'
           AND NOT EXISTS (
               SELECT 1
                 FROM workflow_steps previous
                WHERE previous.workflow_id = workflow_steps.workflow_id
                  AND previous.step_idx < workflow_steps.step_idx
                  AND previous.status NOT IN ('completed', 'skipped')
           )
           AND EXISTS (
               SELECT 1
                 FROM workflow_runs run
                WHERE run.id = workflow_steps.workflow_id
                  AND run.status <> 'cancelled'
           )
        RETURNING id, workflow_id, step_idx, description, tool, args,
                  result, status, started_at, finished_at
        """,
        workflow_id,
        step_idx,
    )
    if row is None:
        return None
    claimed = _row_to_dict(row)
    claimed["args"] = _json_load(claimed.get("args"), {})
    claimed["result"] = _json_load(claimed.get("result"), {})
    return claimed


async def _complete_human_step(pool: Any, workflow_id: str, step_idx: int) -> bool:
    row = await pool.fetchrow(
        """
        UPDATE workflow_steps
           SET status = 'completed',
               result = jsonb_set(
                   COALESCE(result, '{}'::jsonb),
                   '{human_review_completed}',
                   'true'::jsonb,
                   true
               ),
               finished_at = NOW()
         WHERE workflow_id = $1
           AND step_idx = $2
           AND status = 'pending'
           AND COALESCE(result, '{}'::jsonb) @> '{"approved": true}'::jsonb
        RETURNING id
        """,
        workflow_id,
        step_idx,
    )
    return row is not None


async def _invoke_with_retry(
    server_id: str,
    tool: str,
    args: dict[str, Any],
    timeout_seconds: int,
    *,
    user: dict[str, Any],
) -> tuple[bool, Any, str | None]:
    meta = tool_manifest.classify_tool(tool)
    risk = str(meta.get("risk_level") or "write")
    input_schema = await _live_tool_schema(server_id, tool)
    if input_schema is None:
        return False, None, "live tool schema unavailable"
    try:
        args = tool_policy.validate_tool_args(tool, args, input_schema, risk_level=risk)
    except tool_policy.ToolPolicyError as exc:
        return False, None, _safe_error(exc)
    last_error: str | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = await asyncio.wait_for(
                mcp_registry.invoke(server_id, tool, args, user=user),
                timeout=timeout_seconds,
            )
            if isinstance(result, dict) and (result.get("_error") or result.get("error")):
                # Tool-level error envelopes are semantic failures, not
                # transport failures. Retrying them can duplicate side
                # effects for write tools that partially applied.
                last_error = _safe_error(result.get("error_message") or result.get("error"))
                return False, None, last_error
            return True, result, None
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else f"HTTP {exc.status_code}"
            return False, None, _safe_error(detail)
        except Exception as exc:  # noqa: BLE001
            last_error = _safe_error(exc)
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
                continue
    return False, None, last_error or "tool invocation failed"


async def execute_workflow(workflow_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """Execute a planned workflow sequentially.

    Returns the workflow status and current step results. The function
    stops at the first approval gate or terminal failure.
    """
    pool = await auth.pool()
    workflow = await _load_workflow(pool, workflow_id, user)
    status = workflow.get("status")
    if status in TERMINAL_RUN_STATUSES:
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "status": status,
            "step_results": _json_load(workflow.get("step_results"), []),
        }

    await _materialise_steps_if_needed(pool, workflow)
    row = await pool.fetchrow(
        """
        UPDATE workflow_runs
           SET status = 'running', started_at = COALESCE(started_at, NOW())
         WHERE id = $1 AND status IN ('planning', 'running', 'waiting_approval')
        RETURNING status
        """,
        workflow_id,
    )
    if row is None:
        results = await _refresh_step_results(pool, workflow_id)
        latest_status = await pool.fetchval("SELECT status FROM workflow_runs WHERE id = $1", workflow_id)
        return {"ok": True, "workflow_id": workflow_id, "status": latest_status or "cancelled", "step_results": results}

    steps = await _load_steps(pool, workflow_id)
    for step in steps:
        latest_status = await pool.fetchval(
            "SELECT status FROM workflow_runs WHERE id = $1",
            workflow_id,
        )
        if latest_status == "cancelled":
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}

        step_status = step.get("status")
        if step_status == "completed":
            continue
        if step_status not in ACTIVE_STEP_STATUSES:
            continue

        step_idx = int(step["step_idx"])
        args = _json_load(step.get("args"), {})
        tool_full = step.get("tool")

        if not tool_full:
            if _is_approved(step):
                completed = await _complete_human_step(pool, workflow_id, step_idx)
                if completed:
                    await _record_step_audit(
                        user=user,
                        workflow_id=workflow_id,
                        step_idx=step_idx,
                        tool_name=None,
                        args=args,
                        risk_level="write",
                        status="success",
                        metadata={"human_review_step": True},
                    )
                continue
            await pool.execute(
                """
                UPDATE workflow_steps
                   SET status = 'waiting_approval',
                       result = $3::jsonb,
                       started_at = COALESCE(started_at, NOW())
                 WHERE workflow_id = $1 AND step_idx = $2
                """,
                workflow_id,
                step_idx,
                json.dumps({"approval_required": True, "reason": "human_review_step"}),
            )
            await pool.execute(
                "UPDATE workflow_runs SET status = 'waiting_approval', current_step = $2 WHERE id = $1",
                workflow_id,
                step_idx,
            )
            await _record_step_audit(
                user=user,
                workflow_id=workflow_id,
                step_idx=step_idx,
                tool_name=None,
                args=args,
                risk_level="write",
                status="pending_approval",
            )
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": "waiting_approval", "step_results": results}

        try:
            server_id, bare_tool = _split_tool(str(tool_full), args)
        except ValueError as exc:
            await pool.execute(
                """
                UPDATE workflow_steps
                   SET status = 'failed', result = $3::jsonb, finished_at = NOW()
                 WHERE workflow_id = $1 AND step_idx = $2
                """,
                workflow_id,
                step_idx,
                json.dumps({"error": str(exc)}),
            )
            await _mark_remaining_skipped(pool, workflow_id, step_idx)
            await pool.execute(
                """
                UPDATE workflow_runs
                   SET status = 'failed', error = $2,
                       completed_at = NOW(), finished_at = NOW()
                 WHERE id = $1
                """,
                workflow_id,
                str(exc),
            )
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": False, "workflow_id": workflow_id, "status": "failed", "step_results": results}

        risk = tool_manifest.classify_tool(bare_tool)["risk_level"]
        if tool_manifest.requires_approval(bare_tool) and not _is_approved(step):
            await pool.execute(
                """
                UPDATE workflow_steps
                   SET status = 'waiting_approval',
                       result = $3::jsonb,
                       started_at = COALESCE(started_at, NOW())
                 WHERE workflow_id = $1 AND step_idx = $2
                """,
                workflow_id,
                step_idx,
                json.dumps({
                    "approval_required": True,
                    "server": server_id,
                    "tool": bare_tool,
                    "args": _scrub_args(args),
                }),
            )
            await pool.execute(
                "UPDATE workflow_runs SET status = 'waiting_approval', current_step = $2 WHERE id = $1",
                workflow_id,
                step_idx,
            )
            await _record_step_audit(
                user=user,
                workflow_id=workflow_id,
                step_idx=step_idx,
                tool_name=bare_tool,
                args=args,
                risk_level=risk,
                status="pending_approval",
            )
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": "waiting_approval", "step_results": results}

        claimed = await _claim_step(pool, workflow_id, step_idx)
        if claimed is None:
            latest_status = await pool.fetchval(
                "SELECT status FROM workflow_runs WHERE id = $1",
                workflow_id,
            )
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": latest_status or "running", "step_results": results}
        args = claimed["args"]
        run_row = await pool.fetchrow(
            """
            UPDATE workflow_runs
               SET status = 'running', current_step = $2
             WHERE id = $1 AND status <> 'cancelled'
            RETURNING status
            """,
            workflow_id,
            step_idx,
        )
        if run_row is None:
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}

        ok, result, error = await _invoke_with_retry(
            server_id,
            bare_tool,
            args,
            _step_timeout(args),
            user=user,
        )
        if ok:
            row = await pool.fetchrow(
                """
                UPDATE workflow_steps
                   SET status = 'completed', result = $3::jsonb, finished_at = NOW()
                 WHERE workflow_id = $1 AND step_idx = $2 AND status = 'running'
                   AND EXISTS (
                       SELECT 1
                         FROM workflow_runs run
                        WHERE run.id = workflow_steps.workflow_id
                          AND run.status <> 'cancelled'
                   )
                RETURNING id
                """,
                workflow_id,
                step_idx,
                json.dumps(result, default=str),
            )
            if row is None:
                results = await _refresh_step_results(pool, workflow_id)
                return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}
            await _record_step_audit(
                user=user,
                workflow_id=workflow_id,
                step_idx=step_idx,
                tool_name=bare_tool,
                args=args,
                risk_level=risk,
                status="success",
                metadata={"server": server_id},
            )
            continue

        latest_status = await pool.fetchval(
            "SELECT status FROM workflow_runs WHERE id = $1",
            workflow_id,
        )
        if latest_status == "cancelled":
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}
        row = await pool.fetchrow(
            """
            UPDATE workflow_steps
               SET status = 'failed', result = $3::jsonb, finished_at = NOW()
             WHERE workflow_id = $1 AND step_idx = $2 AND status = 'running'
               AND EXISTS (
                   SELECT 1
                     FROM workflow_runs run
                    WHERE run.id = workflow_steps.workflow_id
                      AND run.status <> 'cancelled'
               )
            RETURNING id
            """,
            workflow_id,
            step_idx,
            json.dumps({"error": error}),
        )
        if row is None:
            results = await _refresh_step_results(pool, workflow_id)
            return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}
        await _mark_remaining_skipped(pool, workflow_id, step_idx)
        await pool.fetchrow(
            """
            UPDATE workflow_runs
               SET status = 'failed', error = $2,
                   completed_at = NOW(), finished_at = NOW()
             WHERE id = $1 AND status <> 'cancelled'
            RETURNING status
            """,
            workflow_id,
            error,
        )
        await _record_step_audit(
            user=user,
            workflow_id=workflow_id,
            step_idx=step_idx,
            tool_name=bare_tool,
            args=args,
            risk_level=risk,
            status="failed",
            metadata={"server": server_id, "error": error},
        )
        results = await _refresh_step_results(pool, workflow_id)
        return {"ok": False, "workflow_id": workflow_id, "status": "failed", "step_results": results}

    results = await _refresh_step_results(pool, workflow_id)
    row = await pool.fetchrow(
        """
        UPDATE workflow_runs
           SET status = 'completed', completed_at = NOW(), finished_at = NOW(),
               current_step = GREATEST(current_step, $2)
         WHERE id = $1 AND status <> 'cancelled'
        RETURNING status
        """,
        workflow_id,
        len(steps) - 1 if steps else 0,
    )
    if row is None:
        return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}
    return {"ok": True, "workflow_id": workflow_id, "status": "completed", "step_results": results}


async def workflow_status(workflow_id: str, user: dict[str, Any]) -> dict[str, Any]:
    pool = await auth.pool()
    workflow = await _load_workflow(pool, workflow_id, user)
    results = await _refresh_step_results(pool, workflow_id)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "status": workflow.get("status"),
        "current_step": workflow.get("current_step"),
        "error": workflow.get("error"),
        "step_results": results,
    }


async def cancel_workflow(workflow_id: str, user: dict[str, Any]) -> dict[str, Any]:
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        UPDATE workflow_runs
           SET status = 'cancelled', completed_at = NOW(), finished_at = NOW()
         WHERE id = $1 AND user_id = $2
           AND status IN ('planning', 'running', 'waiting_approval')
        RETURNING id, status
        """,
        workflow_id,
        user["id"],
    )
    if row is None:
        raise HTTPException(404, "Workflow not found")
    await _mark_remaining_skipped(pool, workflow_id, -1)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="copilot.workflow.cancel",
        resource_type="workflow_run",
        resource_id=str(workflow_id),
        status="success",
    )
    results = await _refresh_step_results(pool, workflow_id)
    return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}


async def approve_step(workflow_id: str, step_idx: int, user: dict[str, Any]) -> dict[str, Any]:
    if not permissions.has_permission(user, "copilot.execute"):
        raise HTTPException(403, "permission required: copilot.execute")
    pool = await auth.pool()
    await _load_workflow(pool, workflow_id, user)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="copilot.workflow.step.approve",
        resource_type="workflow_run",
        resource_id=str(workflow_id),
        status="approved",
        metadata={
            "step_index": step_idx,
            "state_transition": "waiting_approval_to_pending",
        },
        critical=True,
    )
    row = await pool.fetchrow(
        """
        UPDATE workflow_steps
           SET status = 'pending',
               result = jsonb_set(
                   COALESCE(result, '{}'::jsonb),
                   '{approved}',
                   'true'::jsonb,
                   true
               )
         WHERE workflow_id = $1
           AND step_idx = $2
           AND status = 'waiting_approval'
        RETURNING workflow_id, step_idx, status
        """,
        workflow_id,
        step_idx,
    )
    if row is None:
        raise HTTPException(404, "Workflow step not waiting approval")
    run_row = await pool.fetchrow(
        """
        UPDATE workflow_runs
           SET status = 'running', current_step = $2
         WHERE id = $1 AND status <> 'cancelled'
        RETURNING status
        """,
        workflow_id,
        step_idx,
    )
    if run_row is None:
        results = await _refresh_step_results(pool, workflow_id)
        return {"ok": True, "workflow_id": workflow_id, "status": "cancelled", "step_results": results}
    return await execute_workflow(workflow_id, user)
