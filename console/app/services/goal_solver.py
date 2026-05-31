"""Sprint v1.45 — copilot goal solver (Nivel 1).

The copilot today answers questions. The goal solver lifts that into
"resolve a business objective end-to-end":

  user: "fix the margin this quarter"
    → create_goal(...) writes a copilot_goals row
    → diagnose_goal() asks the LLM to (a) classify the objective,
      (b) propose 1-N specific sub-objectives, (c) estimate $ impact
    → for each sub-objective the solver picks the right watchdog
      (via watchdog_registry.relevant_watchdogs) and either invokes
      its agent or hands the catalog to the workflow planner
      (copilot_workflows.plan + execute_workflow), preserving the
      existing approval gate.
    → conclude_goal() collects the workflow outcomes and asks the LLM
      for a one-paragraph executive summary that lands in
      copilot_goals.outcome_summary, then records any actionable
      decisions as lessons via lessons_service.

The solver never bypasses the approval gate: destructive steps land
in workflow_steps with status=pending and require human approval
before execution. The goal stays in status=awaiting_approval until
the user resolves the step.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services import auth, lessons_service, watchdog_registry
from app.services._copilot_helpers import (
    coerce_uuid_list as _coerce_uuid_list,
    coerce_uuid_or_none as _coerce_uuid_or_none,
    has_table_cached,
)


logger = logging.getLogger(__name__)


_MAX_GOAL_TEXT          = 2000
_MAX_PLAN_SUMMARY       = 600
_MAX_OUTCOME_SUMMARY    = 2000
_MAX_LIST_LIMIT         = 100


DIAGNOSE_SYSTEM_PROMPT = (
    "Eres el planificador del copiloto OMEGA. Tu trabajo es convertir "
    "un objetivo de negocio del usuario en un PLAN de diagnóstico y "
    "acción, no responder al objetivo. NO inventes datos: el plan se "
    "ejecutará después con herramientas reales que sí tienen acceso a "
    "los datos.\n\n"
    "Devuelve ÚNICAMENTE un JSON con esta forma:\n"
    "{\n"
    '  "classification": "diagnosis|action|monitoring|exploration",\n'
    '  "plan_summary": "una frase: estrategia general en lenguaje claro",\n'
    '  "intent_keywords": ["palabra1","palabra2",...],\n'
    '  "subgoals": [\n'
    '    {"description":"...", "expected_cartridges":["replicon",...], '
    '"watchdog_hint":"slug-opcional"}\n'
    "  ],\n"
    '  "impact_estimate": {"currency":"MXN","amount":0,"direction":"save|recover|avoid|unknown"}\n'
    "}\n\n"
    "REGLAS:\n"
    "- 1 a 4 subgoals como máximo. Si el objetivo es trivial, devuelve "
    "1 subgoal y classification=exploration.\n"
    "- intent_keywords: 3-8 palabras clave en minúscula sin acentos para "
    "matching de watchdogs.\n"
    "- impact_estimate.amount = 0 cuando no tengas evidencia para "
    "estimarlo. NUNCA fabriques cifras.\n"
    "- NO incluyas texto fuera del JSON."
)


# ── Existence guards ──────────────────────────────────────────────────


async def _has_table() -> bool:
    pool = await auth.pool()
    return await has_table_cached(pool, "copilot_goals")


# ── Goal CRUD ─────────────────────────────────────────────────────────


async def create_goal(
    *,
    user_id: int,
    workspace_id: str | None,
    goal_text: str,
    conversation_id: str | None = None,
) -> dict[str, Any] | None:
    if not await _has_table():
        return None
    goal_text = (goal_text or "").strip()
    if not goal_text:
        return None
    goal_text = goal_text[:_MAX_GOAL_TEXT]
    # Defensive: an empty string or a non-UUID would crash $3::uuid
    # at insert time. Coerce here so the bad input becomes NULL
    # instead of a 500 from asyncpg.
    conversation_id_uuid = _coerce_uuid_or_none(conversation_id)
    workspace_id_uuid = _coerce_uuid_or_none(workspace_id)
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO copilot_goals
            (user_id, workspace_id, conversation_id, goal_text, status)
        VALUES ($1, $2::uuid, $3::uuid, $4, 'planning')
        RETURNING id::text AS id, status, created_at
        """,
        user_id, workspace_id_uuid, conversation_id_uuid, goal_text,
    )
    return dict(row) if row else None


async def get_goal(*, goal_id: str, user_id: int) -> dict[str, Any] | None:
    if not await _has_table():
        return None
    goal_uuid = _coerce_uuid_or_none(goal_id)
    if goal_uuid is None:
        return None
    pool = await auth.pool()
    # Cast the parameter to uuid so we hit the PK index instead of
    # forcing a sequential scan with a per-row id::text cast.
    row = await pool.fetchrow(
        """
        SELECT id::text          AS id,
               user_id,
               workspace_id::text AS workspace_id,
               conversation_id::text AS conversation_id,
               goal_text,
               plan_summary,
               status,
               impact_estimate,
               outcome_summary,
               workflow_ids,
               metadata,
               created_at,
               finished_at
          FROM copilot_goals
         WHERE id       = $1::uuid
           AND user_id  = $2
        """,
        goal_uuid, user_id,
    )
    return dict(row) if row else None


async def list_goals(
    *, user_id: int, limit: int = _MAX_LIST_LIMIT,
) -> list[dict[str, Any]]:
    if not await _has_table():
        return []
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT id::text          AS id,
               goal_text,
               plan_summary,
               status,
               impact_estimate,
               outcome_summary,
               workflow_ids,
               created_at,
               finished_at
          FROM copilot_goals
         WHERE user_id = $1
         ORDER BY created_at DESC
         LIMIT $2
        """,
        user_id, min(_MAX_LIST_LIMIT, max(1, limit)),
    )
    return [dict(r) for r in rows]


async def update_goal_status(
    *,
    goal_id: str,
    user_id: int,
    status: str,
    plan_summary: str | None = None,
    impact_estimate: dict[str, Any] | None = None,
    outcome_summary: str | None = None,
    workflow_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    if not await _has_table():
        return False
    if status not in (
        "planning", "running", "awaiting_approval",
        "completed", "failed", "cancelled",
    ):
        return False
    goal_uuid = _coerce_uuid_or_none(goal_id)
    if goal_uuid is None:
        return False
    # Drop garbage UUIDs in the workflow_ids list before we hit
    # ``$5::uuid[]`` — one bad string would crash the whole UPDATE.
    safe_workflow_ids = (
        _coerce_uuid_list(workflow_ids) if workflow_ids is not None else None
    )
    pool = await auth.pool()
    finished = (
        ", finished_at = NOW()"
        if status in ("completed", "failed", "cancelled")
        else ""
    )
    res = await pool.execute(
        f"""
        UPDATE copilot_goals
           SET status           = $1,
               plan_summary     = COALESCE($2, plan_summary),
               impact_estimate  = COALESCE($3::jsonb, impact_estimate),
               outcome_summary  = COALESCE($4, outcome_summary),
               workflow_ids     = COALESCE($5::uuid[], workflow_ids),
               metadata         = COALESCE($6::jsonb, metadata)
               {finished}
         WHERE id       = $7::uuid
           AND user_id  = $8
        """,
        status,
        plan_summary[:_MAX_PLAN_SUMMARY] if plan_summary else None,
        json.dumps(impact_estimate) if impact_estimate is not None else None,
        outcome_summary[:_MAX_OUTCOME_SUMMARY] if outcome_summary else None,
        safe_workflow_ids,
        json.dumps(metadata) if metadata is not None else None,
        goal_uuid, user_id,
    )
    return res.endswith("UPDATE 1")


# ── Diagnosis ─────────────────────────────────────────────────────────


_DIAGNOSIS_MAX_LEN = 16 * 1024  # accept up to 16 KB of LLM output


def _extract_json_object(raw: str) -> str | None:
    """Bracket-matching scan instead of a greedy ``r"\\{.*\\}"`` regex:
    finds the first balanced ``{...}`` substring without the catastrophic-
    backtracking risk of running ``re.search(..., DOTALL)`` against a
    multi-kilobyte LLM response. Returns the substring or None.
    """
    if not raw:
        return None
    in_string = False
    escape = False
    depth = 0
    start = -1
    for i, ch in enumerate(raw):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return raw[start:i + 1]
    return None


def parse_diagnosis(raw: str) -> dict[str, Any]:
    """Tolerant JSON parser. Strips prose preamble, fixes the common
    trailing-comma corruption, then validates the shape. Raises
    ``ValueError`` if the result is not a usable diagnosis.

    Uses a bracket-matching scan rather than a greedy DOTALL regex so a
    malformed multi-kilobyte LLM reply can't cause catastrophic backtrack-
    ing in the request hot path.
    """
    if not raw:
        raise ValueError("empty diagnosis payload")
    if len(raw) > _DIAGNOSIS_MAX_LEN:
        # Cap the input we scan: any usable plan is well under 16 KB.
        raw = raw[:_DIAGNOSIS_MAX_LEN]
    txt = _extract_json_object(raw)
    if txt is None:
        raise ValueError("no JSON object found in diagnosis")
    txt = txt.strip()
    # Trailing comma before } or ] — this regex is linear (no `.*`).
    txt = re.sub(r",\s*([\]}])", r"\1", txt)
    try:
        data = json.loads(txt)
    except json.JSONDecodeError as exc:
        raise ValueError(f"diagnosis JSON parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("diagnosis must be a JSON object")
    if "subgoals" not in data or not isinstance(data["subgoals"], list):
        raise ValueError("diagnosis.subgoals must be a list")
    # Trim & normalise.
    data["classification"] = str(
        data.get("classification") or "exploration"
    )[:32].lower()
    data["plan_summary"] = str(
        data.get("plan_summary") or ""
    )[:_MAX_PLAN_SUMMARY]
    kw = data.get("intent_keywords") or []
    data["intent_keywords"] = [
        str(k).strip().lower()[:64]
        for k in kw if isinstance(k, (str, int, float)) and str(k).strip()
    ][:16]
    subgoals = []
    for sg in data["subgoals"][:4]:
        if not isinstance(sg, dict):
            continue
        desc = str(sg.get("description") or "").strip()[:600]
        if not desc:
            continue
        carts = sg.get("expected_cartridges") or []
        if not isinstance(carts, list):
            carts = []
        carts = [
            str(c).strip().lower()[:80]
            for c in carts if isinstance(c, (str, int)) and str(c).strip()
        ][:6]
        subgoals.append({
            "description": desc,
            "expected_cartridges": carts,
            "watchdog_hint": str(sg.get("watchdog_hint") or "")[:120] or None,
        })
    if not subgoals:
        raise ValueError("diagnosis.subgoals was empty after sanitisation")
    data["subgoals"] = subgoals
    ie = data.get("impact_estimate") or {}
    if not isinstance(ie, dict):
        ie = {}
    data["impact_estimate"] = {
        "currency": str(ie.get("currency") or "MXN")[:8],
        "amount": _safe_amount(ie.get("amount")),
        "direction": str(ie.get("direction") or "unknown")[:16],
    }
    return data


def _safe_amount(value: Any) -> float:
    try:
        amt = float(value)
    except (TypeError, ValueError):
        return 0.0
    if amt < 0:
        amt = 0.0
    if amt > 1e12:
        amt = 1e12
    return amt


async def diagnose_goal(
    *,
    goal_id: str,
    user_id: int,
    llm_call,
) -> dict[str, Any]:
    """Ask the LLM to decompose the goal. ``llm_call`` matches
    ``memory_service.LLMTextCall`` — ``(system, messages) -> str``.

    Persists the resulting plan_summary and impact_estimate. Returns
    the parsed diagnosis dict (which the caller can use to pick
    watchdogs / plan workflows).
    """
    goal = await get_goal(goal_id=goal_id, user_id=user_id)
    if not goal:
        raise ValueError("goal not found")
    if goal["status"] not in ("planning", "running"):
        # Already moved on; return the persisted plan_summary as a
        # degenerate diagnosis so the caller doesn't re-plan.
        return {
            "classification": "exploration",
            "plan_summary": goal.get("plan_summary") or "",
            "intent_keywords": [],
            "subgoals": [],
            "impact_estimate": goal.get("impact_estimate") or {},
            "already_planned": True,
        }
    messages = [{
        "role": "user",
        "content": (
            f"Objetivo del usuario:\n{goal['goal_text']}\n\n"
            "Devuelve el plan JSON exigido por las instrucciones."
        ),
    }]
    raw = await llm_call(DIAGNOSE_SYSTEM_PROMPT, messages)
    diagnosis = parse_diagnosis(raw)
    await update_goal_status(
        goal_id=goal_id,
        user_id=user_id,
        status="running",
        plan_summary=diagnosis["plan_summary"] or None,
        impact_estimate=diagnosis["impact_estimate"],
        metadata={
            "classification": diagnosis["classification"],
            "intent_keywords": diagnosis["intent_keywords"],
            "subgoals": diagnosis["subgoals"],
        },
    )
    return diagnosis


# ── Watchdog routing ──────────────────────────────────────────────────


async def pick_watchdogs_for_diagnosis(
    diagnosis: dict[str, Any], *, limit_per_subgoal: int = 2,
) -> list[dict[str, Any]]:
    """For each subgoal, find the most relevant watchdog(s) by
    combining the subgoal description with the intent_keywords.
    Returns a flat list of {subgoal, watchdog} pairs.
    """
    pairs: list[dict[str, Any]] = []
    keywords = " ".join(diagnosis.get("intent_keywords") or [])
    for sg in diagnosis.get("subgoals", []):
        intent_text = f"{sg['description']} {keywords}"
        for cart in sg.get("expected_cartridges") or [None]:
            wds = await watchdog_registry.relevant_watchdogs(
                intent_text, cartridge_id=cart,
                min_score=0.05, limit=limit_per_subgoal,
            )
            for wd in wds:
                pairs.append({"subgoal": sg, "watchdog": wd})
    return pairs


# ── Conclusion ────────────────────────────────────────────────────────


CONCLUDE_SYSTEM_PROMPT = (
    "Eres el copiloto OMEGA cerrando un objetivo. Genera un resumen "
    "ejecutivo de 4-6 frases del resultado, en castellano claro, "
    "incluyendo: (1) qué se descubrió, (2) qué se ejecutó, (3) qué "
    "impacto numérico se midió o se estimó (sin inventar cifras), "
    "(4) qué queda pendiente o requiere aprobación. NO uses bullets, "
    "responde en prosa. NO inventes datos: si una sección no tiene "
    "evidencia en el contexto, dilo explícitamente."
)


async def conclude_goal(
    *,
    goal_id: str,
    user_id: int,
    workflow_outcomes: list[dict[str, Any]],
    llm_call,
) -> dict[str, Any] | None:
    """Aggregate workflow outcomes into a single executive summary,
    persist it, and record one lesson per approved destructive step
    so the next turn benefits from this history.
    """
    goal = await get_goal(goal_id=goal_id, user_id=user_id)
    if not goal:
        return None

    payload = json.dumps({
        "goal": goal["goal_text"],
        "plan_summary": goal.get("plan_summary"),
        "workflows": workflow_outcomes,
    }, ensure_ascii=False, default=str)[:8000]

    messages = [{
        "role": "user",
        "content": f"Contexto del resultado:\n{payload}",
    }]
    try:
        summary = await llm_call(CONCLUDE_SYSTEM_PROMPT, messages)
    except Exception as exc:
        logger.warning("conclude_goal LLM call failed: %s", exc)
        summary = (
            "No se pudo generar el resumen ejecutivo (el modelo "
            "respondió con error). Revisa los workflows asociados "
            "para los detalles."
        )

    summary = (summary or "").strip()[:_MAX_OUTCOME_SUMMARY]

    # Decide the terminal status: any failed workflow → goal failed;
    # any awaiting → goal awaiting_approval; else completed.
    statuses = [
        (w.get("status") or "").lower() for w in workflow_outcomes
    ]
    if any(s == "failed" for s in statuses):
        terminal = "failed"
    elif any(s in ("awaiting_approval", "pending") for s in statuses):
        terminal = "awaiting_approval"
    else:
        terminal = "completed"

    await update_goal_status(
        goal_id=goal_id,
        user_id=user_id,
        status=terminal,
        outcome_summary=summary,
    )

    # Promote each approved destructive step into a lesson so the
    # next turn knows the user has already greenlit this pattern.
    for w in workflow_outcomes:
        for step in w.get("steps") or []:
            if step.get("status") != "completed":
                continue
            if not step.get("approved"):
                continue
            try:
                await lessons_service.record_lesson_from_approval(
                    user_id=user_id,
                    workspace_id=goal.get("workspace_id"),
                    tool_name=step.get("tool") or "",
                    tool_args=step.get("args") or {},
                    workflow_id=w.get("id"),
                )
            except Exception:
                logger.debug("lesson recording failed", exc_info=True)

    return {
        "goal_id": goal_id,
        "status": terminal,
        "outcome_summary": summary,
    }
