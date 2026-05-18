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
from app.services import workflow_executor
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

plural_router = APIRouter(
    prefix="/api/copilot/workflows",
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


@router.post(
    "/{workflow_id}/cancel",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.write"))],
)
async def cancel_workflow(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    """Flip a planning/running workflow to 'cancelled'.

    Idempotent in the sense that cancelling an already-terminal
    workflow returns 404 (no row updated) rather than 409 — the
    user-visible outcome is "it's not running anymore" either way.

    The implementation lives in workflow_executor; waiting approval is
    cancellable as an active state, terminal states still return 404.
    """
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    return await workflow_executor.cancel_workflow(workflow_id, user)


@router.post(
    "/{workflow_id}/execute",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.write"))],
)
async def execute_workflow(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    return await workflow_executor.execute_workflow(workflow_id, user)


@router.get("/{workflow_id}/status")
async def workflow_status(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    return await workflow_executor.workflow_status(workflow_id, user)


@router.post(
    "/{workflow_id}/steps/{step_idx}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.execute"))],
)
async def approve_workflow_step(
    workflow_id: str,
    step_idx: int,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    if step_idx < 0:
        raise HTTPException(400, "Invalid step index")
    return await workflow_executor.approve_step(workflow_id, step_idx, user)


@plural_router.post(
    "/{workflow_id}/execute",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.write"))],
)
async def execute_workflow_plural(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    return await workflow_executor.execute_workflow(workflow_id, user)


@plural_router.get("/{workflow_id}/status")
async def workflow_status_plural(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    return await workflow_executor.workflow_status(workflow_id, user)


@plural_router.post(
    "/{workflow_id}/cancel",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.write"))],
)
async def cancel_workflow_plural(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    return await workflow_executor.cancel_workflow(workflow_id, user)


@plural_router.post(
    "/{workflow_id}/steps/{step_idx}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.execute"))],
)
async def approve_workflow_step_plural(
    workflow_id: str,
    step_idx: int,
    user: dict = Depends(require_authenticated),
):
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    if step_idx < 0:
        raise HTTPException(400, "Invalid step index")
    return await workflow_executor.approve_step(workflow_id, step_idx, user)


# ── v1.44.3 (Tarea D): LLM-backed planning ─────────────────────────────


_PLANNING_SYSTEM_PROMPT = (
    "Eres un planificador de workflows en una plataforma de "
    "integraciones empresariales (Replicon, SAP HCM/S4/SF). "
    "El usuario te da un objetivo en lenguaje natural; tu tarea es "
    "generar un PLAN secuencial de pasos concretos.\n\n"
    "DEVUELVE UNICAMENTE un JSON array. Cada elemento tiene:\n"
    "  {\n"
    "    \"step\": <entero, empezando en 1>,\n"
    "    \"description\": \"qué hace este paso en una frase\",\n"
    "    \"tool\": \"<nombre de tool MCP o null si es revisión humana>\",\n"
    "    \"args\": {<dict de args para la tool o {} si tool=null>}\n"
    "  }\n\n"
    "Reglas inviolables:\n"
    "- Máximo 8 pasos.\n"
    "- NO inventes tools que no existan en la lista provista.\n"
    "- Si el objetivo requiere acción destructiva (delete, drop, "
    "  truncate, set_variable, create_dag), el paso debe quedar con "
    "  tool=null para que un humano lo apruebe.\n"
    "- Devuelve EXACTAMENTE el JSON array, nada más."
)


# v1.44.3 R1 Security P2 follow-up: deny-list defense-in-depth.
# The planner prompt instructs the LLM to emit ``tool=null`` for
# destructive operations so a human approves them, but a misbehaving
# model could ignore the instruction and emit e.g. ``"tool":
# "airflow_delete_dag"`` directly. _parse_plan_json normalises any
# tool name matching this regex back to None so the (next-session)
# executor loop can never reach the destructive surface without an
# approval gate. Belt + suspenders: the prompt + the parser.
_DESTRUCTIVE_TOOL_RE = __import__("re").compile(
    r"(?:^|[._])(delete|drop|truncate|set_variable|create_dag|destroy|wipe|reset)\b",
    __import__("re").IGNORECASE,
)


def _is_destructive_tool(name: str | None) -> bool:
    if not name:
        return False
    return bool(_DESTRUCTIVE_TOOL_RE.search(name))


def _parse_plan_json(raw: str) -> list[dict]:
    """Same defensive parser pattern as memory_service._parse_facts_json.
    The LLM is asked for strict JSON; we tolerate prose wrappers via
    regex but bail if the array is truly malformed.

    Defense-in-depth: any tool name matching the destructive deny-list
    is forced back to None regardless of what the LLM emitted.
    """
    import re as _re
    s = (raw or "").strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        m = _re.search(r"\[.*\]", s, _re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for i, item in enumerate(parsed[:8], start=1):
        if not isinstance(item, dict):
            continue
        description = (item.get("description") or "").strip()
        if not description:
            continue
        raw_tool = item.get("tool") or None
        # v1.44.3 R1 Security P2: deny-list filter for destructive
        # tool names regardless of LLM compliance with the prompt.
        tool = None if _is_destructive_tool(raw_tool) else raw_tool
        out.append({
            "step":        item.get("step", i),
            "description": description[:500],
            "tool":        tool,
            "args":        item.get("args") if isinstance(item.get("args"), dict) else {},
        })
    return out


async def _llm_plan(intent: str, available_tools: list[str]) -> list[dict]:
    """Call the LLM with the planning prompt + the intent.

    The available_tools list goes into the user message so the LLM
    can reference real tool names. We use the same chat() adapter as
    drafts so the request shape is consistent across the codebase.
    """
    # Local import to keep the workflow router lightweight when LLM
    # isn't reachable (e.g. unit tests that monkeypatch the function).
    from app.services import llm_client

    user_msg = (
        f"Objetivo del usuario: {intent}\n\n"
        f"Tools MCP disponibles ({len(available_tools)}):\n"
        + "\n".join(f"- {t}" for t in available_tools[:50])
    )

    async def _noop_invoke(*args: object, **kw: object) -> dict:
        return {}

    reply, _viewer_urls, _final_msgs = await llm_client.chat(
        system=_PLANNING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        tools=[],
        invoke_tool=_noop_invoke,
        tool_server_map={},
        on_event=None,
    )
    return _parse_plan_json(reply or "")


@router.post("/{workflow_id}/plan", dependencies=[Depends(require_csrf), Depends(require_permission("copilot.execute"))])
async def plan_workflow(
    workflow_id: str,
    user: dict = Depends(require_authenticated),
):
    """Run the LLM planner over a workflow_run still in 'planning'.

    Reads the intent from the row, asks the LLM for a step list,
    persists the steps + flips status to 'running'. Idempotent in
    one direction: re-running on a workflow whose status is NO LONGER
    'planning' returns 409 (the previous plan stays).
    """
    workflow_id = _validate_uuid(workflow_id, label="workflow_id")
    pool = await auth.pool()
    run = await pool.fetchrow(
        """
        SELECT id, intent, status
          FROM workflow_runs
         WHERE id = $1 AND user_id = $2
        """,
        workflow_id, user["id"],
    )
    if run is None:
        raise HTTPException(404, "Workflow not found")
    if run["status"] != "planning":
        raise HTTPException(409, "Workflow already planned or finished")

    # Pull the available tool list from the MCP registry — keeps the
    # planner grounded so it can't hallucinate tool names. We import
    # locally to avoid pulling mcp_registry at module-import time.
    try:
        from app.services import mcp_registry
        # mcp_registry exposes list_servers + list_tools; flatten to
        # a single name list so the planner can reference them.
        servers = await mcp_registry.list_servers()
        all_tools: list[str] = []
        for server in servers:
            try:
                tools = await mcp_registry.list_tools(server["id"])
            except Exception:                       # noqa: BLE001
                continue
            for t in tools:
                name = t.get("name") if isinstance(t, dict) else None
                if name:
                    all_tools.append(f"{server['id']}.{name}")
    except Exception:                              # noqa: BLE001
        all_tools = []

    try:
        plan = await _llm_plan(run["intent"], all_tools)
    except Exception as exc:                       # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("workflow planner LLM call failed")
        raise HTTPException(502, "planner LLM call failed") from exc

    if not plan:
        raise HTTPException(502, "planner returned empty plan")

    # Persist the plan + the per-step rows. Status flips to 'running'
    # so the (next-session) executor loop knows it can start.
    claimed = await pool.fetchrow(
        """
        UPDATE workflow_runs
           SET plan = $2::jsonb, status = 'running'
         WHERE id = $1 AND user_id = $3 AND status = 'planning'
        RETURNING id
        """,
        workflow_id, json.dumps(plan), user["id"],
    )
    if claimed is None:
        raise HTTPException(409, "Workflow already planned or finished")
    for idx, step in enumerate(plan):
        await pool.execute(
            """
            INSERT INTO workflow_steps
                (workflow_id, step_idx, description, tool, args, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, 'pending')
            ON CONFLICT (workflow_id, step_idx) DO NOTHING
            """,
            workflow_id, idx, step["description"],
            step["tool"], json.dumps(step["args"]),
        )

    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.workflow.plan",
        resource_type="workflow_run",
        resource_id=str(workflow_id),
        status="success",
        metadata={"steps": len(plan)},
    )

    return {"ok": True, "workflow_id": workflow_id, "steps": plan, "status": "running"}
