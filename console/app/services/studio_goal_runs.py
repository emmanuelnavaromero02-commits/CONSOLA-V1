"""Durable goal runs for the Studio assistant.

This service is deliberately Studio-scoped. It gives the assistant a small
state machine for cartridge objectives without pulling in the global Copilot
workflow surface.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import json
import os
import uuid
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth, tool_manifest


GoalStepExecutor = Callable[[dict[str, Any], dict[str, Any], dict | None], Awaitable[dict[str, Any]]]

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_STEP_STATUSES = {"completed", "failed", "skipped"}
SUCCESS_STEP_STATUSES = {"completed", "skipped"}
ACTIVE_STEP_STATUSES = {"pending", "waiting_approval"}
WRITE_RISK_LEVELS = {"write", "destructive"}
APPROVAL_REQUIRED_TOOLS = {
    "airflow_create_dag",
    "dag_save_source",
    "airflow_trigger_dag",
    "cartridge_extract",
    "cartridge_extract_all",
    "update_entity",
    "rename_entity",
    "save_dataset",
    "materialize",
    "publish_app",
    "delete_app",
    "delete_dataset",
    "postgres_execute_query",
    "postgres_execute_ddl",
    "upsert_catalog_entries",
    "register_relationship",
    "ingest_document",
}
DESTRUCTIVE_APPROVAL_TOOLS = {
    "airflow_delete_dag",
    "delete_app",
    "delete_dataset",
    "delete_entity",
    "postgres_execute_query",
    "postgres_execute_ddl",
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _safe_uuid(value: str, *, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid {label}") from exc


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    out = dict(row)
    for key in ("id", "goal_run_id"):
        if key in out and isinstance(out[key], uuid.UUID):
            out[key] = str(out[key])
    for key in ("plan", "result", "args"):
        if key in out:
            out[key] = _json_load(out[key], [] if key == "plan" else {})
    return out


def _step_public(row: Any) -> dict[str, Any]:
    out = _row_dict(row)
    out["step_id"] = out.get("id")
    return out


def _run_public(row: Any) -> dict[str, Any]:
    out = _row_dict(row)
    out["goal_run_id"] = out.get("id")
    return out


def _user_id(user: dict | None) -> int | None:
    if not user:
        return None
    try:
        return int(user.get("id"))
    except (TypeError, ValueError):
        return None


def default_steps(cartridge_id: str) -> list[dict[str, Any]]:
    return [
        {
            "step_key": "load_manifest",
            "title": "Cargar manifest",
            "description": "Carga el manifest registrado del cartucho.",
            "tool": "cartridge_get_manifest",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
        {
            "step_key": "connector_schema",
            "title": "Validar connector schema",
            "description": "Verifica que el cartucho exponga connector_schema.",
            "tool": "cartridge_self_check",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
        {
            "step_key": "vault_credentials",
            "title": "Validar Vault",
            "description": "Comprueba si hay credenciales guardadas para la conexión default.",
            "tool": "cartridge_self_check",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id, "conn_id": "default"},
        },
        {
            "step_key": "introspect_source",
            "title": "Introspectar fuente",
            "description": "Ejecuta introspección live/fallback para descubrir entidades y campos.",
            "tool": "introspect_source",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
        {
            "step_key": "compare_entities",
            "title": "Comparar entidades",
            "description": "Detecta entidades incompletas, sin campos, sin PK o sin DAG.",
            "tool": "cartridge_self_check",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
        {
            "step_key": "validate_dags",
            "title": "Validar DAGs",
            "description": "Contrasta DAGs esperados con Airflow cuando está disponible.",
            "tool": "airflow_list_dags",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
        {
            "step_key": "extraction_smoke",
            "title": "Smoke de extracción",
            "description": "Dispara una extracción incremental smoke del cartucho.",
            "tool": "cartridge_extract_all",
            "risk_level": "write",
            "args": {"cartridge_id": cartridge_id, "mode": "incremental"},
        },
        {
            "step_key": "medallion_layers",
            "title": "Validar Silver/Gold",
            "description": "Revisa datasets Silver/Gold asociados al cartucho.",
            "tool": "list_datasets",
            "risk_level": "read",
            "args": {"cartridge": cartridge_id},
        },
        {
            "step_key": "marketplace_visibility",
            "title": "Validar visibilidad",
            "description": "Comprueba visibilidad en registry/Marketplace/Studio.",
            "tool": "cartridge_self_check",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
        {
            "step_key": "final_report",
            "title": "Reporte final",
            "description": "Emite resultado final con bloqueadores, warnings y próximas acciones.",
            "tool": "cartridge_self_check",
            "risk_level": "read",
            "args": {"cartridge_id": cartridge_id},
        },
    ]


def _is_studio_admin(user: dict | None) -> bool:
    values = {
        str((user or {}).get("role") or "").strip().lower(),
        str((user or {}).get("workspace_role") or "").strip().lower(),
    }
    return bool(values & {"admin", "super_admin", "super-admin", "tenant_admin", "workspace_admin", "owner"})


def _admin_direct_step_allowed(step: dict[str, Any], user: dict | None) -> bool:
    if not _is_studio_admin(user):
        return False
    tool = str(step.get("tool") or "")
    if tool in DESTRUCTIVE_APPROVAL_TOOLS:
        return False
    risk = str(step.get("risk_level") or "write").lower()
    classified = tool_manifest.classify_tool(tool)
    if classified.get("risk_level") == "destructive":
        return False
    return risk == "write" or tool in APPROVAL_REQUIRED_TOOLS


def _approval_required(step: dict[str, Any], user: dict | None = None) -> bool:
    if _admin_direct_step_allowed(step, user):
        return False
    tool = str(step.get("tool") or "")
    risk = str(step.get("risk_level") or "write").lower()
    return risk in WRITE_RISK_LEVELS or tool in APPROVAL_REQUIRED_TOOLS or (_is_production() and tool in APPROVAL_REQUIRED_TOOLS)


def _approval_payload(run: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    approval_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"studio-goal:{run['id']}:{step['id']}:{step.get('tool')}"))
    return {
        "approval_required": True,
        "goal_run_id": str(run["id"]),
        "step_id": step.get("id"),
        "approval_key": approval_key,
        "tool": step.get("tool"),
        "risk_level": step.get("risk_level") or "write",
        "reason": step.get("description") or step.get("title") or "Studio goal step requires approval",
        "args_preview": step.get("args") or {},
    }


async def _audit(
    *,
    action: str,
    user: dict | None,
    goal_run_id: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    tool_name: str | None = None,
    risk_level: str = "read",
) -> None:
    await audit_service.record_event(
        user_id=(user or {}).get("id"),
        email=(user or {}).get("email"),
        action=action,
        resource_type="studio_goal_run",
        resource_id=str(goal_run_id),
        status=status,
        metadata=metadata or {},
        tool_name=tool_name,
        tool_args=((metadata or {}).get("args") or (metadata or {}).get("args_preview")) if isinstance(metadata, dict) else None,
        tool_result_status=status,
        risk_level=risk_level,
    )


async def create_goal_run(cartridge_id: str, intent: str, user: dict | None, *, auto_plan: bool = True) -> dict[str, Any]:
    uid = _user_id(user)
    if uid is None:
        raise HTTPException(401, "Authentication required")
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO studio_goal_runs (user_id, cartridge_id, intent, status)
        VALUES ($1, $2, $3, 'planning')
        RETURNING id, user_id, cartridge_id, intent, plan, status, current_step,
                  result, error, created_at, updated_at, finished_at
        """,
        uid,
        cartridge_id,
        intent[:2000],
    )
    run = _run_public(row)
    await _audit(action="studio.goal.create", user=user, goal_run_id=run["id"], status="success", metadata={"cartridge_id": cartridge_id})
    if auto_plan:
        return await plan_goal_run(run["id"], user)
    return {"goal_run": run, "steps": []}


async def _load_run(pool: Any, goal_run_id: str, user: dict | None) -> dict[str, Any]:
    goal_run_id = _safe_uuid(goal_run_id, label="goal_run_id")
    uid = _user_id(user)
    if uid is None:
        raise HTTPException(401, "Authentication required")
    row = await pool.fetchrow(
        """
        SELECT id, user_id, cartridge_id, intent, plan, status, current_step,
               result, error, created_at, updated_at, finished_at
          FROM studio_goal_runs
         WHERE id = $1 AND user_id = $2
        """,
        goal_run_id,
        uid,
    )
    if row is None:
        raise HTTPException(404, "Studio goal run not found")
    return _run_public(row)


async def _load_steps(pool: Any, goal_run_id: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, goal_run_id, step_idx, step_key, title, description, tool,
               args, risk_level, status, result, approval_key, started_at, finished_at
          FROM studio_goal_steps
         WHERE goal_run_id = $1
         ORDER BY step_idx
        """,
        goal_run_id,
    )
    return [_step_public(row) for row in rows]


async def plan_goal_run(goal_run_id: str, user: dict | None) -> dict[str, Any]:
    pool = await auth.pool()
    run = await _load_run(pool, goal_run_id, user)
    steps = default_steps(run["cartridge_id"])
    plan = [{**step, "step_idx": idx} for idx, step in enumerate(steps, start=1)]
    existing = await _load_steps(pool, run["id"])
    existing_keys = {str(step.get("step_key")) for step in existing}
    row = await pool.fetchrow(
        """
        UPDATE studio_goal_runs
           SET plan = $2::jsonb, status = 'running', updated_at = NOW()
         WHERE id = $1
         RETURNING id, user_id, cartridge_id, intent, plan, status, current_step,
                   result, error, created_at, updated_at, finished_at
        """,
        run["id"],
        json.dumps(plan),
    )
    run = _run_public(row)
    for idx, step in enumerate(steps, start=1):
        if step["step_key"] in existing_keys:
            continue
        await pool.execute(
            """
            INSERT INTO studio_goal_steps
                (goal_run_id, step_idx, step_key, title, description, tool, args, risk_level, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, 'pending')
            ON CONFLICT (goal_run_id, step_idx) DO NOTHING
            """,
            run["id"],
            idx,
            step["step_key"],
            step["title"],
            step["description"],
            step.get("tool"),
            json.dumps(step.get("args") or {}),
            step.get("risk_level") or "read",
        )
    planned = await _load_steps(pool, run["id"])
    await _audit(action="studio.goal.plan", user=user, goal_run_id=run["id"], status="success", metadata={"steps": len(planned)})
    return {"goal_run": run, "steps": planned}


async def get_goal_run_status(goal_run_id: str, user: dict | None) -> dict[str, Any]:
    pool = await auth.pool()
    run = await _load_run(pool, goal_run_id, user)
    steps = await _load_steps(pool, run["id"])
    return {"goal_run": run, "steps": steps}


def _validate_approval_key(value: str | None) -> str:
    key = str(value or "").strip()
    if not key:
        raise HTTPException(400, "approval_key is required")
    try:
        uuid.UUID(key)
    except ValueError as exc:
        raise HTTPException(400, "Invalid approval_key") from exc
    return key


async def approve_goal_step(goal_run_id: str, step_id: int, user: dict | None, *, approval_key: str | None = None) -> dict[str, Any]:
    pool = await auth.pool()
    run = await _load_run(pool, goal_run_id, user)
    if run.get("status") in TERMINAL_RUN_STATUSES:
        raise HTTPException(409, "Studio goal run is already terminal")
    approval_key = _validate_approval_key(approval_key)
    result = {
        "approved": True,
        "approved_by": (user or {}).get("id"),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await pool.fetchrow(
        """
        UPDATE studio_goal_steps
           SET status = 'pending', result = $3::jsonb, finished_at = NULL
         WHERE goal_run_id = $1 AND id = $2 AND status = 'waiting_approval' AND approval_key = $4
         RETURNING id, goal_run_id, step_idx, step_key, title, description, tool,
                   args, risk_level, status, result, approval_key, started_at, finished_at
        """,
        run["id"],
        int(step_id),
        json.dumps(result),
        approval_key,
    )
    if row is None:
        raise HTTPException(409, "Studio goal step is not waiting for approval or approval_key does not match")
    await _audit(action="studio.goal.approve_step", user=user, goal_run_id=run["id"], status="success", metadata={"step_id": step_id, "approval_key": approval_key})
    await pool.execute(
        """
        UPDATE studio_goal_runs
           SET status = 'running', updated_at = NOW()
         WHERE id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
        """,
        run["id"],
    )
    return {"goal_run_id": run["id"], "step": _step_public(row)}


async def reject_goal_step(goal_run_id: str, step_id: int, user: dict | None, *, reason: str = "", approval_key: str | None = None) -> dict[str, Any]:
    pool = await auth.pool()
    run = await _load_run(pool, goal_run_id, user)
    if run.get("status") in TERMINAL_RUN_STATUSES:
        raise HTTPException(409, "Studio goal run is already terminal")
    approval_key = _validate_approval_key(approval_key)
    result = {
        "rejected": True,
        "rejected_by": (user or {}).get("id"),
        "rejected_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason[:500],
    }
    row = await pool.fetchrow(
        """
        UPDATE studio_goal_steps
           SET status = 'skipped', result = $3::jsonb, finished_at = NOW()
         WHERE goal_run_id = $1 AND id = $2 AND status = 'waiting_approval' AND approval_key = $4
         RETURNING id, goal_run_id, step_idx, step_key, title, description, tool,
                   args, risk_level, status, result, approval_key, started_at, finished_at
        """,
        run["id"],
        int(step_id),
        json.dumps(result),
        approval_key,
    )
    if row is None:
        raise HTTPException(409, "Studio goal step is not waiting for approval or approval_key does not match")
    await _audit(action="studio.goal.reject_step", user=user, goal_run_id=run["id"], status="success", metadata={"step_id": step_id, "approval_key": approval_key})
    await pool.execute(
        """
        UPDATE studio_goal_runs
           SET status = 'running', updated_at = NOW()
         WHERE id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
        """,
        run["id"],
    )
    return {"goal_run_id": run["id"], "step": _step_public(row)}


async def _mark_step_waiting(pool: Any, run: dict[str, Any], step: dict[str, Any]) -> dict[str, Any] | None:
    payload = _approval_payload(run, step)
    row = await pool.fetchrow(
        """
        UPDATE studio_goal_steps
           SET status = 'waiting_approval',
               result = $3::jsonb,
               approval_key = $4,
               started_at = COALESCE(started_at, NOW())
         WHERE goal_run_id = $1 AND id = $2 AND status = 'pending'
           AND NOT EXISTS (
               SELECT 1
                 FROM studio_goal_steps prev
                WHERE prev.goal_run_id = studio_goal_steps.goal_run_id
                  AND prev.step_idx < studio_goal_steps.step_idx
                  AND prev.status NOT IN ('completed', 'skipped')
           )
         RETURNING id, goal_run_id, step_idx, step_key, title, description, tool,
                   args, risk_level, status, result, approval_key, started_at, finished_at
        """,
        run["id"],
        int(step["id"]),
        json.dumps(payload),
        payload["approval_key"],
    )
    if row is None:
        return None
    await pool.execute(
        """
        UPDATE studio_goal_runs
           SET status = 'waiting_approval', current_step = $2, updated_at = NOW()
         WHERE id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
        """,
        run["id"],
        int(step["step_idx"]),
    )
    return {"goal_run_id": run["id"], "approval": payload, "step": _step_public(row)}


async def _claim_step(pool: Any, run: dict[str, Any], step: dict[str, Any]) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """
        UPDATE studio_goal_steps
           SET status = 'running', started_at = COALESCE(started_at, NOW())
         WHERE goal_run_id = $1 AND id = $2 AND status = 'pending'
           AND NOT EXISTS (
               SELECT 1
                 FROM studio_goal_steps prev
                WHERE prev.goal_run_id = studio_goal_steps.goal_run_id
                  AND prev.step_idx < studio_goal_steps.step_idx
                  AND prev.status NOT IN ('completed', 'skipped')
           )
         RETURNING id, goal_run_id, step_idx, step_key, title, description, tool,
                   args, risk_level, status, result, approval_key, started_at, finished_at
        """,
        run["id"],
        int(step["id"]),
    )
    if row is None:
        return None
    await pool.execute(
        """
        UPDATE studio_goal_runs
           SET status = 'running', current_step = $2, updated_at = NOW()
         WHERE id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
        """,
        run["id"],
        int(step["step_idx"]),
    )
    return _step_public(row)


async def _reset_stale_running_steps(pool: Any, goal_run_id: str) -> None:
    await pool.execute(
        """
        UPDATE studio_goal_steps
           SET status = 'pending',
               result = jsonb_build_object('requeued_from', 'running_stale'),
               started_at = NULL
         WHERE goal_run_id = $1
           AND status = 'running'
           AND started_at < NOW() - INTERVAL '15 minutes'
        """,
        goal_run_id,
    )


async def _default_executor(_run: dict[str, Any], step: dict[str, Any], _user: dict | None) -> dict[str, Any]:
    return {
        "ok": True,
        "source": "default_executor",
        "message": f"Step {step.get('step_key')} completed without external executor",
    }


async def execute_goal_run(
    goal_run_id: str,
    user: dict | None,
    *,
    executor: GoalStepExecutor | None = None,
) -> dict[str, Any]:
    pool = await auth.pool()
    run = await _load_run(pool, goal_run_id, user)
    if run.get("status") in TERMINAL_RUN_STATUSES:
        return await get_goal_run_status(run["id"], user)

    await _reset_stale_running_steps(pool, run["id"])
    steps = await _load_steps(pool, run["id"])
    if not steps:
        planned = await plan_goal_run(run["id"], user)
        run = planned["goal_run"]
        steps = planned["steps"]

    executor = executor or _default_executor
    completed_this_call: list[dict[str, Any]] = []
    for step in steps:
        if step.get("status") in TERMINAL_STEP_STATUSES:
            continue
        if step.get("status") == "waiting_approval":
            return {
                "goal_run": run,
                "steps": await _load_steps(pool, run["id"]),
                "approval_required": True,
                "approval": step.get("result") or _approval_payload(run, step),
            }
        if step.get("status") != "pending":
            continue
        if _approval_required(step, user) and not (_json_load(step.get("result"), {}) or {}).get("approved"):
            waiting = await _mark_step_waiting(pool, run, step)
            if waiting is None:
                continue
            await _audit(
                action="studio.goal.step",
                user=user,
                goal_run_id=run["id"],
                status="pending_approval",
                metadata=waiting["approval"],
                tool_name=step.get("tool"),
                risk_level=str(step.get("risk_level") or "write"),
            )
            return {
                "goal_run": (await get_goal_run_status(run["id"], user))["goal_run"],
                "steps": (await get_goal_run_status(run["id"], user))["steps"],
                "approval_required": True,
                "approval": waiting["approval"],
            }

        claimed = await _claim_step(pool, run, step)
        if claimed is None:
            continue
        try:
            result = await executor(run, claimed, user)
        except Exception as exc:
            safe_error = str(exc)[:500]
            row = await pool.fetchrow(
                """
                UPDATE studio_goal_steps
                   SET status = 'failed', result = $3::jsonb, finished_at = NOW()
                 WHERE goal_run_id = $1 AND id = $2
                 RETURNING id, goal_run_id, step_idx, step_key, title, description, tool,
                           args, risk_level, status, result, approval_key, started_at, finished_at
                """,
                run["id"],
                int(claimed["id"]),
                json.dumps({"error": safe_error}),
            )
            await pool.execute(
                """
                UPDATE studio_goal_runs
                   SET status = 'failed', error = $2, updated_at = NOW(), finished_at = NOW()
                 WHERE id = $1 AND status NOT IN ('completed', 'cancelled')
                """,
                run["id"],
                safe_error,
            )
            await _audit(
                action="studio.goal.step",
                user=user,
                goal_run_id=run["id"],
                status="failed",
                metadata={"step_id": claimed["id"], "error": safe_error},
                tool_name=claimed.get("tool"),
                risk_level=str(claimed.get("risk_level") or "write"),
            )
            return {"goal_run": (await get_goal_run_status(run["id"], user))["goal_run"], "steps": await _load_steps(pool, run["id"]), "failed_step": _step_public(row)}

        row = await pool.fetchrow(
            """
            UPDATE studio_goal_steps
               SET status = 'completed', result = $3::jsonb, finished_at = NOW()
             WHERE goal_run_id = $1 AND id = $2
             RETURNING id, goal_run_id, step_idx, step_key, title, description, tool,
                       args, risk_level, status, result, approval_key, started_at, finished_at
            """,
            run["id"],
            int(claimed["id"]),
            json.dumps(result, default=str),
        )
        public_step = _step_public(row)
        completed_this_call.append(public_step)
        await _audit(
            action="studio.goal.step",
            user=user,
            goal_run_id=run["id"],
            status="success",
            metadata={"step_id": claimed["id"]},
            tool_name=claimed.get("tool"),
            risk_level=str(claimed.get("risk_level") or "read"),
        )

    steps = await _load_steps(pool, run["id"])
    failed_steps = [step for step in steps if step.get("status") == "failed"]
    if failed_steps:
        safe_error = f"{len(failed_steps)} Studio goal step(s) failed"
        await pool.execute(
            """
            UPDATE studio_goal_runs
               SET status = 'failed',
                   error = COALESCE(error, $2),
                   updated_at = NOW(),
                   finished_at = COALESCE(finished_at, NOW())
             WHERE id = $1 AND status NOT IN ('completed', 'cancelled')
            """,
            run["id"],
            safe_error,
        )
        status = await get_goal_run_status(run["id"], user)
        status["completed_this_call"] = completed_this_call
        return status
    blockers = [
        step
        for step in steps
        if step.get("status") not in SUCCESS_STEP_STATUSES
    ]
    if not blockers:
        summary = {
            "completed_steps": len([step for step in steps if step.get("status") == "completed"]),
            "skipped_steps": len([step for step in steps if step.get("status") == "skipped"]),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        await pool.execute(
            """
            UPDATE studio_goal_runs
               SET status = 'completed', result = $2::jsonb, updated_at = NOW(), finished_at = NOW()
             WHERE id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
            """,
            run["id"],
            json.dumps(summary),
        )
        await _audit(action="studio.goal.complete", user=user, goal_run_id=run["id"], status="success", metadata=summary)

    status = await get_goal_run_status(run["id"], user)
    status["completed_this_call"] = completed_this_call
    return status
