"""Sprint v1.44.2 (Tarea I, capability 16) — copilot workflows.

"Operar procesos completos" — multi-step LLM-planned execution
with intermediate progress reports. This module ships the durable
schema layer + CRUD + a per-workflow status endpoint; the
LLM-driven planning loop and the SSE stream (the brief mentions
GET /workflow/{id}/stream) are next-session work in
copilot_service.

Lifecycle of a workflow_run:
  planning → running → completed | failed | cancelled
Each step in workflow_steps:
  pending → running → completed | failed | skipped
"""
from __future__ import annotations

import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated
from app.services import audit_service, auth
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


def _validate_uuid(value: str, *, label: str) -> str:
    """v1.44.2 (R1 Security P2): malformed path UUIDs would surface
    as 500 (asyncpg InvalidTextRepresentation) without an early
    validation step. Convert to a clean 400 so the client gets a
    deterministic response shape."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, f"Invalid {label}")


router = APIRouter(
    prefix="/api/copilot/workflow",
    tags=["copilot-workflows"],
    dependencies=[Depends(require_permission("copilot.use"))],
)


_MAX_INTENT_LEN = 1000


def _serialize_run(row: dict) -> dict:
    out = dict(row)
    out["id"] = str(out["id"])
    if "conversation_id" in out and out["conversation_id"]:
        out["conversation_id"] = str(out["conversation_id"])
    if "plan" in out and isinstance(out["plan"], str):
        try:
            out["plan"] = json.loads(out["plan"])
        except Exception:
            out["plan"] = []
    return out


def _serialize_step(row: dict) -> dict:
    out = dict(row)
    out["workflow_id"] = str(out["workflow_id"])
    for key in ("args", "result"):
        if key in out and isinstance(out[key], str):
            try:
                out[key] = json.loads(out[key])
            except Exception:
                out[key] = {} if key == "args" else None
    return out


@router.post("", dependencies=[Depends(require_csrf)])
async def create_workflow(
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Kick off a new workflow run.

    Body: ``{intent, conversation_id?}``.

    The status is left at 'planning' — the LLM planner (next
    session) is responsible for populating ``plan`` and flipping
    to 'running'. This endpoint is the durable seam.
    """
    payload = body or {}
    intent = payload.get("intent", "").strip()
    conv_id = payload.get("conversation_id")

    if not intent:
        raise HTTPException(400, "intent is required")
    if len(intent) > _MAX_INTENT_LEN:
        raise HTTPException(400, f"intent too long (max {_MAX_INTENT_LEN})")

    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO workflow_runs (user_id, conversation_id, intent, status)
        VALUES ($1, $2, $3, 'planning')
        RETURNING id, user_id, conversation_id, intent, plan, status,
                  current_step, error, created_at, finished_at
        """,
        user["id"], conv_id, intent,
    )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.workflow.create",
        resource_type="workflow_run",
        resource_id=str(row["id"]),
        status="success",
        metadata={"intent_len": len(intent)},
    )
    return {"ok": True, "workflow": _serialize_run(row)}


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    """Return one workflow + its current step list. Returns 404 if
    the workflow isn't this user's (no enumeration leak)."""
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    pool = await auth.pool()
    run = await pool.fetchrow(
        """
        SELECT id, user_id, conversation_id, intent, plan, status,
               current_step, error, created_at, finished_at
          FROM workflow_runs
         WHERE id = $1 AND user_id = $2
        """,
        workflow_id, user["id"],
    )
    if run is None:
        raise HTTPException(404, "Workflow not found")
    steps = await pool.fetch(
        """
        SELECT id, workflow_id, step_idx, description, tool, args,
               result, status, started_at, finished_at
          FROM workflow_steps
         WHERE workflow_id = $1
         ORDER BY step_idx
        """,
        workflow_id,
    )
    return {
        "workflow": _serialize_run(run),
        "steps":    [_serialize_step(s) for s in steps],
    }


@router.get("")
async def list_workflows(user: dict = Depends(require_authenticated)):
    """List the current user's workflows, most recent first."""
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT id, user_id, conversation_id, intent, plan, status,
               current_step, error, created_at, finished_at
          FROM workflow_runs
         WHERE user_id = $1
         ORDER BY created_at DESC
         LIMIT 50
        """,
        user["id"],
    )
    return {"workflows": [_serialize_run(r) for r in rows]}


@router.post("/{workflow_id}/cancel", dependencies=[Depends(require_csrf)])
async def cancel_workflow(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    """Flip a planning/running workflow to 'cancelled'.

    Idempotent in the sense that cancelling an already-terminal
    workflow returns 404 (no row updated) rather than 409 — the
    user-visible outcome is "it's not running anymore" either way.
    """
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        UPDATE workflow_runs
           SET status = 'cancelled', finished_at = NOW()
         WHERE id = $1 AND user_id = $2
           AND status IN ('planning', 'running')
        RETURNING id, status
        """,
        workflow_id, user["id"],
    )
    if row is None:
        # v1.44.2 (R1 Security P2): IDENTICAL error string to the
        # get_workflow 404 branch above. The pre-fix message
        # ("…or already finished") leaked terminal-status info to a
        # probe — comparing the two strings let a caller distinguish
        # "this UUID is yours and terminal" from "this UUID isn't
        # yours". Collapse to one string.
        raise HTTPException(404, "Workflow not found")
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.workflow.cancel",
        resource_type="workflow_run",
        resource_id=str(workflow_id),
        status="success",
    )
    return {"ok": True, "workflow_id": workflow_id, "status": "cancelled"}
