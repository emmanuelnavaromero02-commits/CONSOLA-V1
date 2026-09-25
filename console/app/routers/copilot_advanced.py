from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.dependencies import require_authenticated
from app.domains.copilot.context_payloads import (
    json_prompt_snapshot as _json_prompt_snapshot,
    looks_like_control_room_page as _looks_like_control_room_page,
    render_page_context as _render_page_context,
    sanitise_page_context as _sanitise_page_context,
    scrub_value as _scrub_value,
)
from app.domains.copilot.admin_scope import (
    COPILOT_ADMIN_ROLE_ALLOWLIST,
    has_admin as _has_admin_impl,
)
from app.domains.copilot.router_helpers import (
    require_uuid_path as _require_uuid_path_impl,
    tenant_id as _tenant_id_impl,
    user_id as _user_id_impl,
    workspace_id as _workspace_id_impl,
)
from app.services import (
    audit_service,
    briefing_v2,
    control_room_service,
    copilot_context_service,
    copilot_service,
    goal_solver,
    lessons_service,
    llm_client,
    memory_service,
    watchdog_registry,
)
from app.services.csrf import require_csrf
from app.services import permissions
from app.services.permissions import require_permission


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/copilot",
    tags=["copilot-advanced"],
    dependencies=[Depends(require_permission("copilot.use"))],
)


def _user_id(user: dict[str, Any]) -> int:
    return _user_id_impl(user)


def _workspace_id(user: dict[str, Any]) -> str | None:
    return _workspace_id_impl(user)


def _tenant_id(user: dict[str, Any]) -> str | None:
    return _tenant_id_impl(user)


async def _noop_invoke_tool(*_args, **_kwargs) -> dict:
    return {"error": "tools_disabled_in_this_context"}


def _require_uuid_path(value: str, *, label: str) -> str:
    return _require_uuid_path_impl(value, label=label)


async def _conversation_belongs_to_user(
    conversation_id: str, user_id: int,
) -> bool:
    from app.services import auth
    pool = await auth.pool()
    has_table = await pool.fetchval(
        "SELECT to_regclass('public.conversations')"
    )
    if not has_table:
        return True
    coerced = conversation_id
    try:
        owner = await pool.fetchval(
            """
            SELECT user_id
              FROM conversations
             WHERE id = $1::uuid
            """,
            coerced,
        )
    except Exception:
        return False
    if owner is None:
        return False
    return int(owner) == int(user_id)


_LLM_HARD_TIMEOUT_SECONDS = 60.0


async def _llm_text_call(system: str, messages: list[dict], user_context: dict | None = None) -> str:
    import asyncio

    async def _do_call() -> str:
        try:
            reply, _viewer_urls, _full_msgs = await llm_client.chat(
                system=system,
                messages=messages,
                tools=[],
                invoke_tool=_noop_invoke_tool,
                tool_server_map={},
                on_event=None,
                user_context=user_context,
            )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("llm adapter call failed: %s", exc)
            raise RuntimeError("llm_call_failed") from exc
        return reply or ""

    try:
        return await asyncio.wait_for(_do_call(), timeout=_LLM_HARD_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("llm adapter call timed out after %ss", _LLM_HARD_TIMEOUT_SECONDS)
        raise RuntimeError("llm_call_timeout")


@router.post(
    "/goals",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("copilot.write")),
    ],
)
async def create_goal_endpoint(
    body: dict = Body(...),
    user: dict = Depends(require_authenticated),
):
    goal_text = (body or {}).get("goal_text") or (body or {}).get("text")
    if not goal_text or not str(goal_text).strip():
        raise HTTPException(400, "goal_text is required")
    conversation_id_raw = (body or {}).get("conversation_id")
    conversation_id: str | None = None
    if conversation_id_raw:
        conversation_id = str(conversation_id_raw)
        if not await _conversation_belongs_to_user(
            conversation_id, _user_id(user),
        ):
            raise HTTPException(
                403, "conversation_id does not belong to caller",
            )
    goal = await goal_solver.create_goal(
        user_id=_user_id(user),
        tenant_id=_tenant_id(user),
        workspace_id=_workspace_id(user),
        goal_text=str(goal_text),
        conversation_id=conversation_id,
    )
    if goal is None:
        raise HTTPException(503, "copilot_goals table not provisioned")
    try:
        await audit_service.record_event(
            user_id=_user_id(user),
            email=str(user.get("email") or ""),
            action="copilot.goal.created",
            resource_type="copilot_goal",
            resource_id=str(goal.get("id") or ""),
            status="completed",
            metadata={
                "goal_preview": str(goal_text)[:200],
                "conversation_id": conversation_id,
            },
        )
    except Exception:
        logger.debug("audit copilot.goal.created failed", exc_info=True)
    return goal


@router.get("/goals")
async def list_goals_endpoint(
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(require_authenticated),
):
    return await goal_solver.list_goals(
        user_id=_user_id(user),
        tenant_id=_tenant_id(user),
        workspace_id=_workspace_id(user),
        limit=limit,
    )


@router.get("/goals/{goal_id}")
async def get_goal_endpoint(
    goal_id: str,
    user: dict = Depends(require_authenticated),
):
    goal_id = _require_uuid_path(goal_id, label="goal_id")
    goal = await goal_solver.get_goal(
        goal_id=goal_id,
        user_id=_user_id(user),
        tenant_id=_tenant_id(user),
        workspace_id=_workspace_id(user),
    )
    if not goal:
        raise HTTPException(404, "goal not found")
    return goal


@router.post(
    "/goals/{goal_id}/diagnose",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("copilot.write")),
    ],
)
async def diagnose_goal_endpoint(
    goal_id: str,
    user: dict = Depends(require_authenticated),
):
    goal_id = _require_uuid_path(goal_id, label="goal_id")
    try:
        diagnosis = await goal_solver.diagnose_goal(
            goal_id=goal_id,
            user_id=_user_id(user),
            tenant_id=_tenant_id(user),
            workspace_id=_workspace_id(user),
            llm_call=_llm_text_call,
        )
    except ValueError as exc:
        logger.warning("diagnose_goal failed for %s: %.200s", goal_id, exc)
        raise HTTPException(422, "diagnosis failed: invalid plan returned by LLM")
    except RuntimeError as exc:
        msg = str(exc)
        if msg == "llm_call_timeout":
            raise HTTPException(504, "llm call timed out")
        raise HTTPException(502, "llm call failed")
    try:
        watchdog_pairs = await goal_solver.pick_watchdogs_for_diagnosis(diagnosis)
    except Exception:
        logger.warning("watchdog matching failed for diagnosed goal %s", goal_id, exc_info=True)
        watchdog_pairs = []
    return {"diagnosis": diagnosis, "watchdogs": watchdog_pairs}


@router.post(
    "/goals/{goal_id}/conclude",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("copilot.write")),
    ],
)
async def conclude_goal_endpoint(
    goal_id: str,
    body: dict = Body(default={}),
    user: dict = Depends(require_authenticated),
):
    goal_id = _require_uuid_path(goal_id, label="goal_id")
    workflow_outcomes = (body or {}).get("workflow_outcomes") or []
    if not isinstance(workflow_outcomes, list):
        raise HTTPException(400, "workflow_outcomes must be a list")
    result = await goal_solver.conclude_goal(
        goal_id=goal_id,
        user_id=_user_id(user),
        tenant_id=_tenant_id(user),
        workspace_id=_workspace_id(user),
        workflow_outcomes=workflow_outcomes,
        llm_call=_llm_text_call,
    )
    if result is None:
        raise HTTPException(404, "goal not found")
    return result


@router.get("/lessons")
async def list_lessons_endpoint(
    enabled_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=200),
    user: dict = Depends(require_authenticated),
):
    return await lessons_service.list_lessons(
        user_id=_user_id(user),
        workspace_id=_workspace_id(user),
        enabled_only=enabled_only,
        limit=limit,
    )


@router.post(
    "/lessons",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("copilot.write")),
    ],
)
async def create_lesson_endpoint(
    body: dict = Body(...),
    user: dict = Depends(require_authenticated),
):
    trigger, lesson, scope = _lesson_creation_payload(body)
    scope = _validate_lesson_scope(scope, user)
    await _reject_jailbreak_lesson_if_needed(user, trigger, lesson, scope)
    new_id = await lessons_service.record_manual_lesson(
        user_id=_user_id(user) if scope == "user" else None,
        workspace_id=_workspace_id(user),
        scope=scope,
        trigger_pattern=str(trigger),
        lesson_text=str(lesson),
    )
    if not new_id:
        raise HTTPException(503, "copilot_lessons table not provisioned")
    await _audit_lesson_event(
        user,
        action="copilot.lesson.created",
        status="completed",
        resource_id=str(new_id),
        scope=scope,
        trigger=trigger,
        lesson=lesson,
    )
    return {"id": new_id, "scope": scope}


def _lesson_creation_payload(body: dict | None) -> tuple[Any, Any, str]:
    trigger = (body or {}).get("trigger_pattern")
    lesson = (body or {}).get("lesson_text")
    scope = (body or {}).get("scope", "user")
    if not trigger or not lesson:
        raise HTTPException(400, "trigger_pattern and lesson_text required")
    return trigger, lesson, str(scope)


def _validate_lesson_scope(scope: str, user: dict[str, Any]) -> str:
    if scope == "global":
        scope = "workspace_global"
    if scope not in (
        "user",
        "workspace",
        "workspace_global",
        "tenant_global",
        "platform_global",
    ):
        raise HTTPException(
            400,
            "scope must be one of: user, workspace, workspace_global, tenant_global, platform_global",
        )
    if scope in ("workspace", "workspace_global", "tenant_global") and not _has_admin(user):
        raise HTTPException(403, "workspace lessons require admin")
    if scope == "platform_global" and str(user.get("role") or "").lower() not in {
        "owner",
        "super_admin",
        "admin",
    }:
        raise HTTPException(403, "platform_global lessons require platform admin")
    return scope


async def _audit_lesson_event(
    user: dict[str, Any],
    *,
    action: str,
    status: str,
    resource_id: str,
    scope: str,
    trigger: Any,
    lesson: Any,
) -> None:
    try:
        await audit_service.record_event(
            user_id=_user_id(user),
            email=str(user.get("email") or ""),
            action=action,
            resource_type="copilot_lesson",
            resource_id=resource_id,
            status=status,
            metadata={
                "scope": scope,
                "trigger_preview": str(trigger)[:120],
                "lesson_preview": str(lesson)[:200],
            },
        )
    except Exception:
        logger.debug("audit %s failed", action, exc_info=True)


async def _reject_jailbreak_lesson_if_needed(
    user: dict[str, Any],
    trigger: Any,
    lesson: Any,
    scope: str,
) -> None:
    if not lessons_service._looks_like_jailbreak(str(lesson)):
        return
    await _audit_lesson_event(
        user,
        action="copilot.lesson.rejected_jailbreak",
        status="failed",
        resource_id="",
        scope=scope,
        trigger=trigger,
        lesson=lesson,
    )
    raise HTTPException(
        400, "lesson_text matches the jailbreak guard and was rejected",
    )


_ADMIN_ROLE_ALLOWLIST = COPILOT_ADMIN_ROLE_ALLOWLIST


def _has_admin(user: dict[str, Any]) -> bool:
    return _has_admin_impl(user)


@router.post(
    "/lessons/{lesson_id}/disable",
    dependencies=[Depends(require_csrf)],
)
async def disable_lesson_endpoint(
    lesson_id: str,
    user: dict = Depends(require_authenticated),
):
    lesson_id = _require_uuid_path(lesson_id, label="lesson_id")
    ok = await lessons_service.disable_lesson(
        lesson_id=lesson_id, user_id=_user_id(user),
    )
    if not ok:
        raise HTTPException(404, "lesson not found or not yours")
    return {"ok": True, "id": lesson_id, "enabled": False}


@router.post(
    "/lessons/{lesson_id}/enable",
    dependencies=[Depends(require_csrf)],
)
async def enable_lesson_endpoint(
    lesson_id: str,
    user: dict = Depends(require_authenticated),
):
    lesson_id = _require_uuid_path(lesson_id, label="lesson_id")
    ok = await lessons_service.enable_lesson(
        lesson_id=lesson_id, user_id=_user_id(user),
    )
    if not ok:
        raise HTTPException(404, "lesson not found or not yours")
    return {"ok": True, "id": lesson_id, "enabled": True}


@router.get("/watchdogs")
async def list_watchdogs_endpoint(
    cartridge_id: str | None = Query(None),
    enabled_only: bool = Query(True),
    limit: int = Query(100, ge=1, le=200),
    user: dict = Depends(require_authenticated),
):
    return await watchdog_registry.list_watchdogs(
        cartridge_id=cartridge_id, enabled_only=enabled_only, limit=limit,
    )


@router.get("/watchdogs/match")
async def match_watchdogs_endpoint(
    intent: str = Query(..., min_length=1, max_length=400),
    cartridge_id: str | None = Query(None),
    limit: int = Query(5, ge=1, le=20),
    user: dict = Depends(require_authenticated),
):
    return await watchdog_registry.relevant_watchdogs(
        intent, cartridge_id=cartridge_id, limit=limit,
    )


@router.post(
    "/watchdogs/{cartridge_id}/{slug}/invoke",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("copilot.write")),
    ],
)
async def invoke_watchdog_endpoint(
    cartridge_id: str,
    slug: str,
    body: dict = Body(default={}),
    user: dict = Depends(require_authenticated),
):
    input_text = (body or {}).get("input_text") or (body or {}).get("text") or ""
    if not str(input_text).strip():
        raise HTTPException(400, "input_text is required")
    result = await watchdog_registry.invoke_watchdog(
        cartridge_id=cartridge_id,
        slug=slug,
        user=user,
        input_text=str(input_text)[:4000],
    )
    if result.get("error") == "watchdog_not_found":
        raise HTTPException(404, "watchdog not found")
    try:
        await audit_service.record_event(
            user_id=_user_id(user),
            email=str(user.get("email") or ""),
            action="copilot.watchdog.invoked",
            resource_type="copilot_watchdog",
            resource_id=f"{cartridge_id}/{slug}",
            status="completed" if not result.get("error") else "failed",
            metadata={
                "mode": result.get("mode") or "unknown",
                "error": result.get("error"),
                "input_preview": str(input_text)[:200],
            },
        )
    except Exception:
        logger.debug("audit copilot.watchdog.invoked failed", exc_info=True)
    return result


@router.get("/briefing/v2")
async def briefing_v2_endpoint(
    limit: int = Query(6, ge=1, le=20),
    user: dict = Depends(require_authenticated),
):
    highlights = await briefing_v2.briefing_v2_for_user(
        _user_id(user), limit=limit, user_context=user,
    )
    try:
        live = copilot_context_service.project_operator_recommendations(
            await copilot_context_service.list_recommendations(user, limit=limit)
        )
        live_items = []
        severity_score = {"critical": 95, "warning": 75, "info": 45, "success": 15}
        for item in live.get("recommendations", []):
            if not isinstance(item, dict) or item.get("status") == "dismissed":
                continue
            live_items.append({
                "id": item.get("id"),
                "severity": item.get("severity") or "info",
                "title": item.get("title") or "Recomendación",
                "body": item.get("body") or "",
                "category": item.get("category") or "console",
                "action_label": item.get("action_label"),
                "action_href": item.get("action_href"),
                "priority_score": severity_score.get(str(item.get("severity") or "info"), 45),
                "next_action": item.get("action_label") or "Revisar",
                "watchdogs": [],
                "source": "copilot_live_context",
            })
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for item in [*live_items, *highlights]:
            key = str(item.get("id") or item.get("title") or "")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        merged.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        return merged[:limit]
    except Exception:
        logger.debug("briefing v2 live context merge failed", exc_info=True)
        return highlights


_QUESTION_MAX_LEN = 2000


async def _control_room_live_context_for_prompt(
    page_context: dict[str, Any],
    user: dict[str, Any],
) -> str | None:
    if not _looks_like_control_room_page(page_context):
        return None
    if not permissions.has_permission(user, "datasets.read"):
        return None
    has_operations_read = permissions.has_permission(user, "operations.read")

    snapshot: dict[str, Any] = {"available": True}

    async def _safe(name: str, loader) -> None:
        try:
            raw = await loader()
            projected = copilot_context_service.project_control_room_diagnostic(
                name, raw
            )
            snapshot[name] = projected or {"available": False}
        except HTTPException:
            snapshot[name] = {"available": False}
        except Exception:
            logger.debug("control room live context %s failed", name, exc_info=True)
            snapshot[name] = {"available": False}

    await _safe(
        "sap_successfactors_talent_kpis",
        lambda: control_room_service.sap_successfactors_talent_kpis(user),
    )
    await _safe(
        "sap_successfactors_talent_overview",
        lambda: control_room_service.sap_successfactors_talent_overview(user),
    )
    if has_operations_read:
        await _safe("ops_summary", lambda: control_room_service.ops_summary(user))
        await _safe(
            "sap_successfactors_talent_metadata_readiness",
            lambda: control_room_service.sap_successfactors_talent_metadata_readiness(
                user
            ),
        )
        await _safe(
            "agents_ops",
            lambda: control_room_service.agents_ops(user, limit=8),
        )

    return _json_prompt_snapshot(snapshot)


@router.post(
    "/ask-with-context",
    dependencies=[Depends(require_csrf)],
)
async def ask_with_context_endpoint(
    body: dict = Body(...),
    user: dict = Depends(require_authenticated),
):
    """Single-shot copilot ask — no conversation persistence, no tool
    use, no streaming. Designed for the inline "Pregúntale a OMEGA"
    widget that lives inside dashboards / control room / studio.

    The page_context is injected into the system prompt so the LLM
    knows which dashboard / item / row the question is about.
    """
    question, page_context = _ask_request_payload(body)
    prompt_context = await _ask_prompt_context(page_context, user)
    base_prompt = copilot_service.SYSTEM_PROMPT + _render_page_context(prompt_context)
    uid = _user_id(user)
    ws = _workspace_id(user)
    final_prompt = await _ask_final_prompt(
        user=user,
        user_id=uid,
        workspace_id=ws,
        base_prompt=base_prompt,
        intent_hint=_ask_intent_hint(page_context, question),
    )
    answer = await _ask_llm_answer(final_prompt, question, user)
    return {
        "answer": (answer or "").strip(),
        "context_used": page_context,
    }


def _ask_request_payload(body: dict | None) -> tuple[str, dict[str, Any]]:
    question = (body or {}).get("question") or (body or {}).get("text")
    if not question or not str(question).strip():
        raise HTTPException(400, "question is required")
    page_context = _sanitise_page_context((body or {}).get("page_context"))
    return str(question), page_context


async def _ask_prompt_context(
    page_context: dict[str, Any],
    user: dict[str, Any],
) -> dict[str, Any]:
    prompt_context = dict(page_context)
    live_context = await _control_room_live_context_for_prompt(page_context, user)
    if live_context:
        prompt_context["live_control_room_snapshot"] = live_context
    console_context = await copilot_context_service.prompt_context_for_user(user)
    if console_context:
        prompt_context["live_console_snapshot"] = console_context
    return prompt_context


def _ask_intent_hint(page_context: dict[str, Any], question: str) -> str:
    intent_hint = page_context.get("route") or page_context.get("title") or ""
    if intent_hint:
        return f"{intent_hint} {question}"
    return str(question)


async def _ask_final_prompt(
    *,
    user: dict[str, Any],
    user_id: str,
    workspace_id: str,
    base_prompt: str,
    intent_hint: str,
) -> str:
    try:
        with_memory = await memory_service.build_system_prompt_with_memory(
            user_id,
            base_prompt,
            user_context=user,
        )
    except Exception:
        logger.warning("memory injection failed; continuing", exc_info=True)
        with_memory = base_prompt

    try:
        return await lessons_service.build_system_prompt_with_lessons(
            user_id=user_id,
            workspace_id=workspace_id,
            base_prompt=with_memory,
            intent_hint=intent_hint,
        )
    except Exception:
        logger.warning("lessons injection failed; continuing", exc_info=True)
        return with_memory


async def _ask_llm_answer(
    final_prompt: str,
    question: str,
    user: dict[str, Any],
) -> str:
    messages = [{"role": "user", "content": str(question)[:_QUESTION_MAX_LEN]}]
    try:
        return await _llm_text_call(final_prompt, messages, user_context=user)
    except RuntimeError as exc:
        if str(exc) == "llm_call_timeout":
            raise HTTPException(504, "llm call timed out")
        raise HTTPException(502, "llm call failed")
    except Exception as exc:
        logger.warning("ask-with-context unexpected failure: %.200s", exc)
        raise HTTPException(502, "llm call failed")
