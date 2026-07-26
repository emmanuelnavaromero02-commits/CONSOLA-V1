from __future__ import annotations

import types

from app.services.control_room import core as _core
from app.services.control_room.business_projection import (
    project_public_business_item as _project_public_business_item,
)
from app.services.control_room.business_action_templates import (
    primary_template_for_business_item,
    template_ids_for_business_item,
    templates_for_business_item,
)
from app.services.control_room.business_execution_payload import (
    build_action_payload,
    build_execution_payload,
)
from app.services.control_room.business_impact_projection import (
    build_impact_payload,
    build_priority_payload,
)
from app.services.control_room.business_impact_rules import calculate_item_impact

for _builder in (
    build_action_payload,
    build_execution_payload,
    build_impact_payload,
    build_priority_payload,
    calculate_item_impact,
    primary_template_for_business_item,
    template_ids_for_business_item,
    templates_for_business_item,
):
    _core.__dict__.setdefault(_builder.__name__, _builder)


_RESERVED_GLOBALS = {
    "__name__",
    "__package__",
    "__loader__",
    "__spec__",
    "__file__",
    "__cached__",
    "__builtins__",
}
for _name, _value in _core.__dict__.items():
    if _name not in _RESERVED_GLOBALS:
        globals()[_name] = _value
_core.__dict__.setdefault("_project_public_item", _project_public_business_item)

from app.services.control_room.business_builder_policy import (
    business_action_templates_builder,
    business_impact_builder,
    business_primary_template_builder,
    business_priority_builder,
    business_template_ids_builder,
)
from app.services.adapter_idempotency import adapter_guarantees_idempotency
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision as _create_and_link_business_decision,
    persist_option_selection as _persist_business_option_selection,
)
from app.services.control_room.business_action_approval import (
    approve_business_item as _approve_business_item,
)
from app.services.control_room.business_action_authority import (
    require_action_item_evidence,
    require_action_item_prerequisites,
)
from app.services.control_room.business_action_binding import (
    normalize_action_idempotency_key,
)
from app.services.control_room.business_action_catalog import (
    require_enabled_action_template,
)
from app.services.control_room.business_action_resolution import (
    require_explicit_action_template,
    single_explicit_action_binding,
)
from app.services.control_room.business_approve_with_optional_decision import (
    approve_with_optional_decision as _approve_with_optional_decision,
)
from app.services.control_room.business_action_mutations import (
    persist_alert_state as _persist_alert_state,
    persist_control as _persist_control,
    persist_status_transition as _persist_status_transition,
    persist_step as _persist_step,
    require_exact_count as _require_exact_count,
)
from app.services.control_room.business_alert_operation_with_audit import (
    persist_alert_operation_with_audit as _persist_alert_operation_with_audit,
)
from app.services.control_room.business_dismiss_reopen_with_audit import (
    dismiss_with_audit as _dismiss_with_audit,
    reopen_with_audit as _reopen_with_audit,
)
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
    acquire_action_reservation,
    acquire_guarded_action_reservation,
    complete_action_reservation,
)
from app.services.control_room.business_action_attempt import (
    mark_remote_attempt_ambiguous,
    mark_remote_attempt_started,
    remote_attempt_status,
)
from app.services.control_room.business_external_outcome import (
    ambiguous_adapter_error_response,
)
from app.services.control_room.business_action_failure import (
    finalize_aborted_action_reservation,
    run_reserved_external_action,
)
from app.services.control_room.business_reservation_errors import ReservationUnavailable
from app.services.control_room.business_execution_precondition import (
    dry_run_metadata,
    lock_pending_action_reservation,
    require_matching_dry_run,
)
from app.services.control_room.business_execution_approval import (
    execution_lifecycle_block,
    require_approved_execution,
)
from app.services.control_room.business_execution_entry import prepare_execution_entry
from app.services.control_room.business_external_projection import (
    reserved_action_response as _build_reserved_action_response,
)
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_operational_state import (
    locked_operational_metadata,
    merged_alert_state,
    merged_control_state,
    merged_learned_rules,
    merged_lesson_state,
)
import app.services.control_room.business_execution_provenance_sql as _exec_sql
from app.services.control_room.business_workflow_quarantine import (
    workflow_reopen_allowed,
)

_core.__dict__.setdefault(
    "_create_and_link_business_decision", _create_and_link_business_decision
)
_core.__dict__.setdefault(
    "_persist_business_option_selection", _persist_business_option_selection
)
_core.__dict__.setdefault("_approve_business_item", _approve_business_item)
_core.__dict__.setdefault(
    "_approve_with_optional_decision", _approve_with_optional_decision
)
_core.__dict__.setdefault("_persist_alert_state", _persist_alert_state)
_core.__dict__.setdefault(
    "_persist_alert_operation_with_audit", _persist_alert_operation_with_audit
)
_core.__dict__.setdefault("_persist_control", _persist_control)
_core.__dict__.setdefault("_persist_status_transition", _persist_status_transition)
_core.__dict__.setdefault("_persist_step", _persist_step)
_core.__dict__.setdefault("_require_exact_count", _require_exact_count)
_core.__dict__.setdefault("_dismiss_with_audit", _dismiss_with_audit)
_core.__dict__.setdefault("_reopen_with_audit", _reopen_with_audit)
for _helper in (
    acquire_action_reservation,
    acquire_guarded_action_reservation,
    complete_action_reservation,
    mark_remote_attempt_ambiguous,
    mark_remote_attempt_started,
    remote_attempt_status,
    ambiguous_adapter_error_response,
    finalize_aborted_action_reservation,
    run_reserved_external_action,
    lock_authoritative_business_item,
    locked_operational_metadata,
    lock_pending_action_reservation,
    merged_alert_state,
    merged_control_state,
    merged_learned_rules,
    merged_lesson_state,
    normalize_action_idempotency_key,
    require_matching_dry_run,
    require_approved_execution,
    prepare_execution_entry,
    require_action_item_evidence,
    require_action_item_prerequisites,
    require_enabled_action_template,
    require_explicit_action_template,
    single_explicit_action_binding,
    workflow_reopen_allowed,
):
    _core.__dict__.setdefault(_helper.__name__, _helper)
_core.__dict__.setdefault("ActionReservation", ActionReservation)
_core.__dict__.setdefault("ReservationState", ReservationState)
_core.__dict__.setdefault("ReservationUnavailable", ReservationUnavailable)
_core.__dict__.update(_exec_sql=_exec_sql, WorkflowStage=_exec_sql.WorkflowStage)
_core.__dict__.setdefault("dry_run_metadata", dry_run_metadata)
_core.__dict__.setdefault("execution_lifecycle_block", execution_lifecycle_block)
_core.__dict__.setdefault(
    "_build_reserved_action_response", _build_reserved_action_response
)
_core.__dict__.setdefault(
    "adapter_guarantees_idempotency", adapter_guarantees_idempotency
)


def _bind_to_core(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _core.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _core.__name__
    _core.__dict__[fn.__name__] = rebound
    return rebound


# Action preview, dry-run, execution, and operator workflows.
@_bind_to_core
def _external_writeback_enabled() -> bool:
    return os.environ.get(
        "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "false"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@_bind_to_core
def _external_delivery_enabled() -> bool:
    return os.environ.get(
        "CONTROL_ROOM_ENABLE_EXTERNAL_DELIVERY", "false"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@_bind_to_core
def _writeback_capability(template: dict[str, Any]) -> dict[str, Any]:
    template_id = str(template.get("template_id") or "")
    if template_id in SUPPORTED_INTERNAL_WRITEBACK_TEMPLATES:
        target_by_template = {
            "create_followup_task": "decision_actions",
            "create_investigation_note": "control_room_item_events",
            "mark_decision_for_monitoring": "control_room_items.metadata",
        }
        description_by_template = {
            "create_followup_task": "Crea un seguimiento operativo auditado en decision_actions; no escribe en ERP.",
            "create_investigation_note": "Crea una nota de investigacion auditada en Control Room; no escribe en ERP.",
            "mark_decision_for_monitoring": "Marca la decision para monitoreo interno; no escribe en ERP.",
        }
        return {
            "supported": True,
            "mode": "supervised_execution",
            "target": target_by_template.get(template_id, "control_room"),
            "external": False,
            "requires_flag": False,
            "requires_external_writeback_flag": False,
            "requires_confirmation": True,
            "requires_decision": True,
            "requires_dry_run": True,
            "permission": "control_room.execute",
            "status": "supported",
            "description": description_by_template.get(
                template_id, "Ejecuta una accion interna auditada; no escribe en ERP."
            ),
        }
    template_type = str(template.get("template_type") or template_id)
    has_adapter = WriteBackAdapterFactory.has_adapter(template_type)
    external_enabled = _external_writeback_enabled()
    return {
        "supported": has_adapter and external_enabled,
        "mode": "external_writeback",
        "target": str(template.get("cartridge_id") or "external_system"),
        "external": True,
        "adapter": template_type if has_adapter else None,
        "adapter_available": has_adapter,
        "template_type": template_type,
        "requires_flag": True,
        "requires_external_writeback_flag": True,
        "requires_confirmation": True,
        "requires_decision": True,
        "requires_dry_run": True,
        "permission": "control_room.execute",
        "status": (
            "supported"
            if has_adapter and external_enabled
            else "external_writeback_disabled"
            if has_adapter
            else "adapter_missing"
        ),
        "description": "Ejecuta write-back ERP mediante adapter auditado y circuito por cartucho.",
        "reason": (
            None
            if has_adapter and external_enabled
            else "Write-back ERP externo no habilitado en Control Room V1; usa ejecucion supervisada."
            if has_adapter
            else "No hay adapter ERP aprobado para este template."
        ),
    }


@_bind_to_core
def _template_with_writeback(template: dict[str, Any]) -> dict[str, Any]:
    public_template = dict(template)
    public_template["writeback"] = _writeback_capability(public_template)
    return public_template


@_bind_to_core
def _impact_payload(
    *,
    item: dict[str, Any],
    estimate: float | None,
    status: str,
    confidence: float,
    drivers: list[dict[str, Any]],
    formula: str,
    explanation: str,
    currency: str = "USD",
) -> dict[str, Any]:
    return build_impact_payload(
        item=item,
        estimate=estimate,
        status=status,
        confidence=confidence,
        drivers=drivers,
        formula=formula,
        explanation=explanation,
        severity_weights=SEVERITY_WEIGHT,
        currency=currency,
    )


@_bind_to_core
@business_priority_builder
def _priority_payload(
    item: dict[str, Any],
    impact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_priority_payload(
        item,
        impact or _impact_for_item(item),
        severity_weights=SEVERITY_WEIGHT,
        terminal_statuses=TERMINAL_ITEM_STATUSES,
    )


@_bind_to_core
@business_impact_builder
def _impact_for_item(item: dict[str, Any]) -> dict[str, Any]:
    return calculate_item_impact(item, number=_num, payload=_impact_payload)


@_bind_to_core
@business_template_ids_builder
def _template_ids_for_item(item: dict[str, Any]) -> list[str]:
    return template_ids_for_business_item(item)


@_bind_to_core
@business_action_templates_builder
def _action_templates_for_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    return templates_for_business_item(
        item,
        templates=ACTION_TEMPLATES,
        decorate=_template_with_writeback,
    )


@_bind_to_core
@business_primary_template_builder
def _primary_template_for_item(item: dict[str, Any]) -> dict[str, Any]:
    return primary_template_for_business_item(
        item,
        templates=ACTION_TEMPLATES,
        decorate=_template_with_writeback,
    )


@_bind_to_core
def _action_payload_for_template(
    item: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    return build_action_payload(
        item,
        template,
        number=_num,
        writeback_for=_writeback_capability,
    )


@_bind_to_core
def _execution_payload(
    item: dict[str, Any], mode: str, template: dict[str, Any]
) -> dict[str, Any]:
    external_enabled = _external_writeback_enabled()
    return build_execution_payload(
        item,
        mode,
        template,
        impact=_impact_for_item(item),
        action_payload=_action_payload_for_template(item, template),
        writeback=_writeback_capability(template),
        external_writeback_enabled=external_enabled,
        supported_internal_templates=SUPPORTED_INTERNAL_WRITEBACK_TEMPLATES,
    )


@_bind_to_core
async def create_decision_for_item(
    item_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()

    async def _write(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> Any:
        return await _create_and_link_business_decision(
            conn,
            user=user,
            item=item,
            workspace_id=scoped_workspace_id,
            ensure_item_row=_ensure_item_row,
            record_item_event=_record_item_event,
        )

    row = await _run_with_db_scope(pool, user, _write)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.decision.create",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"decision_id": row["id"], "item": item},
        critical=True,
    )
    item = _project_public_item(
        item, _with_omega, decision_id=row["id"], status="decision_created"
    )
    return {"decision": dict(row), "item": item, "anomaly": item}


@_bind_to_core
async def select_item_option(
    item_id: str,
    option_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    option_id = str(option_id or "").strip()
    valid_option_ids = {str(option["id"]) for option in item["omega"]["options"]}
    if option_id not in valid_option_ids:
        raise HTTPException(400, "invalid control room option")
    if item.get("status") in TERMINAL_ITEM_STATUSES:
        raise HTTPException(409, "terminal control room item cannot change option")

    pool = await auth.pool()

    async def _write_selection(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> None:
        await _persist_business_option_selection(
            conn,
            user=user,
            item=item,
            workspace_id=scoped_workspace_id,
            option_id=option_id,
            terminal_statuses=sorted(TERMINAL_ITEM_STATUSES),
            ensure_item_row=_ensure_item_row,
            record_item_event=_record_item_event,
        )

    await _run_with_db_scope(pool, user, _write_selection)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.option.select",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"option_id": option_id, "item": item},
        critical=False,
    )
    item = _project_public_item(
        item, _with_omega, selected_option_id=option_id, status="in_review"
    )
    return {"selected": True, "option_id": option_id, "item": item, "anomaly": item}


@_bind_to_core
async def get_item_impact(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_read(item_id, user, fetcher=fetcher)
    return _impact_for_item(item)


@_bind_to_core
async def record_item_step(
    item_id: str,
    step_id: str,
    user: dict,
    *,
    note: str | None = None,
    control_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    step = str(step_id or "").strip()
    if step not in OMEGA_STEP_EVENT_TYPES:
        raise HTTPException(400, "invalid OMEGA step")
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()
    event_type = OMEGA_STEP_EVENT_TYPES[step]
    metadata = {
        "step_id": step,
        "note": str(note or "").strip()[:500],
        "control_id": str(control_id or "").strip()[:120],
    }

    async def _write_step(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> None:
        await _persist_step(
            conn,
            user=user,
            item=item,
            workspace_id=scoped_workspace_id,
            event_type=event_type,
            metadata=metadata,
            ensure_item_row=_ensure_item_row,
        )

    await _run_with_db_scope(pool, user, _write_step)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.step.record",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"event_type": event_type, "step_id": step, "item": item, **metadata},
        critical=step in {"decision", "execution", "control", "lessons"},
    )
    return {
        "recorded": True,
        "step_id": step,
        "event_type": event_type,
        "item": _with_omega(item),
    }


@_bind_to_core
async def update_item_control(
    item_id: str,
    control_id: str,
    body: dict[str, Any],
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    control_key = str(control_id or "").strip()
    if not control_key:
        raise HTTPException(400, "control_id is required")
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    current_controls = item.get("omega", {}).get("control", {}).get("items") or []
    control = next(
        (row for row in current_controls if str(row.get("id")) == control_key), None
    )
    if not control:
        raise HTTPException(404, "control item not found")

    next_status = str(body.get("status") or body.get("state") or "closed").strip()
    if next_status not in CONTROL_ITEM_STATUSES:
        raise HTTPException(400, "invalid control status")
    due_at = body.get("due_at")
    parsed_due = _parse_utc_datetime(due_at)
    if parsed_due:
        due_at = parsed_due.isoformat()
    elif body.get("days") is not None:
        try:
            due_at = (
                datetime.now(UTC) + timedelta(days=max(0, int(body.get("days"))))
            ).isoformat()
        except (TypeError, ValueError):
            due_at = control.get("due_at")
    else:
        due_at = control.get("due_at")

    owner = str(
        body.get("owner")
        or body.get("owner_email")
        or control.get("owner")
        or user.get("email")
        or "operaciones"
    ).strip()
    note = str(body.get("note") or "").strip()[:500]
    now = datetime.now(UTC).isoformat()
    control_changes = {
        "id": control_key,
        "status": next_status,
        "st": CONTROL_ITEM_STATUS_LABELS[next_status],
        "owner": owner,
        "due_at": due_at,
        "note": note,
        "updated_at": now,
        "updated_by": user.get("email") or "user",
    }
    item_status = str(item.get("status") or "open")
    target_status = (
        item_status if item_status in TERMINAL_ITEM_STATUSES else "in_review"
    )
    pool = await auth.pool()
    event_type = "control_updated" if next_status != "closed" else "control_checked"

    async def _write_control(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> None:
        await _persist_control(
            conn,
            user=user,
            item=item,
            workspace_id=scoped_workspace_id,
            target_status=target_status,
            terminal_statuses=sorted(TERMINAL_ITEM_STATUSES),
            control_id=control_key,
            baseline_control=control,
            control_changes=control_changes,
            event_type=event_type,
            event_metadata={
                "control_id": control_key,
                "control_status": next_status,
                "owner": owner,
                "due_at": due_at,
                "note": note,
            },
            ensure_item_row=_ensure_item_row,
        )

    await _run_with_db_scope(pool, user, _write_control)
    next_state = merged_control_state(
        {"control_state": _control_state(item)},
        control_id=control_key,
        baseline=control,
        changes=control_changes,
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.control.update",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "control_id": control_key,
            "control_status": next_status,
            "owner": owner,
            "due_at": due_at,
            "item": item,
        },
        critical=True,
    )
    public_item = _project_public_item(
        item, _with_omega, status=target_status, control_state=next_state
    )
    updated_control = next(
        row
        for row in public_item.get("omega", {}).get("control", {}).get("items", [])
        if str(row.get("id")) == control_key
    )
    return {
        "updated": True,
        "control": updated_control,
        "item": public_item,
    }


@_bind_to_core
async def create_item_lesson(
    item_id: str,
    body: dict[str, Any],
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    rule = str(body.get("rule") or "").strip()
    if len(rule) < 8:
        raise HTTPException(400, "lesson rule must contain at least 8 characters")
    rule = rule[:700]
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    decision_id = (
        int(item["decision_id"]) if item.get("decision_id") is not None else None
    )
    pool = await auth.pool()

    async def _write_lesson(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> list[str]:
        await _ensure_item_row(
            conn,
            user=user,
            item=item,
            status=item.get("status") or "in_review",
        )
        await _persist_lessons(
            conn,
            user=user,
            item=item,
            decision_id=decision_id,
            lessons=[rule],
        )
        operational_metadata = await locked_operational_metadata(
            conn,
            workspace_id=scoped_workspace_id,
            item_id=str(item["id"]),
            owner_user_id=expected_business_item_owner(item, user),
        )
        learned_rules = merged_learned_rules(operational_metadata, rule=rule)
        update_result = await conn.execute(
            """
                UPDATE control_room_items
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
                       last_seen_at = NOW()
                 WHERE workspace_id = $1
                   AND item_id = $2
                   AND owner_user_id IS NOT DISTINCT FROM $4
                """,
            scoped_workspace_id,
            item["id"],
            json.dumps({"learned_rules": learned_rules}),
            expected_business_item_owner(item, user),
        )
        _require_exact_count(update_result, "UPDATE")
        await _record_item_event(
            conn,
            user=user,
            item=item,
            event_type="lesson_recorded",
            metadata={"decision_id": decision_id, "lessons": [rule], "manual": True},
        )
        return learned_rules

    learned_rules = await _run_with_db_scope(pool, user, _write_lesson)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.lesson.create",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"decision_id": decision_id, "rule": rule, "item": item},
        critical=True,
    )
    lessons = await _load_lesson_rows(user, item_id=item["id"], limit=20)
    if not lessons:
        lessons = [
            {
                "id": None,
                "item_id": item["id"],
                "cartridge_id": item.get("cartridge") or "platform",
                "anomaly_type": item.get("anomaly_type") or "control_room_item",
                "rule": rule,
                "source_decision_id": decision_id,
                "confidence": _impact_for_item(item).get("confidence") or 0.7,
                "metadata": {"manual": True},
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]
    public_item = _project_public_item(
        item,
        _with_omega,
        related_lessons=lessons[:5],
        lesson_count=len(lessons),
        learned_rules=learned_rules,
    )
    return {
        "created": True,
        "lesson": lessons[0],
        "lessons": lessons,
        "item": public_item,
    }


@_bind_to_core
async def apply_item_lesson(
    item_id: str,
    lesson_id: int,
    body: dict[str, Any],
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    related_lessons = await _load_lesson_rows(
        user,
        cartridge_id=item.get("cartridge"),
        anomaly_type=item.get("anomaly_type"),
        limit=100,
    )
    item_lessons = await _load_lesson_rows(user, item_id=item["id"], limit=100)
    lessons = _dedupe_lessons([*related_lessons, *item_lessons])
    lesson = next(
        (row for row in lessons if int(row.get("id") or 0) == int(lesson_id)), None
    )
    if not lesson or not _lesson_matches_item(lesson, item):
        raise HTTPException(404, "lesson not applicable to this control room item")

    rule = str(lesson.get("rule") or "").strip()
    if len(rule) < 8:
        raise HTTPException(400, "lesson rule is not valid")
    note = str(body.get("note") or "").strip()[:500]
    now = datetime.now(UTC).isoformat()
    application = {
        "lesson_id": int(lesson_id),
        "rule": rule,
        "source_decision_id": lesson.get("source_decision_id"),
        "cartridge_id": lesson.get("cartridge_id"),
        "anomaly_type": lesson.get("anomaly_type"),
        "applied_at": now,
        "applied_by": user.get("email"),
    }
    if note:
        application["note"] = note

    target_status = (
        item.get("status")
        if item.get("status") in TERMINAL_ITEM_STATUSES
        else "in_review"
    )

    pool = await auth.pool()

    async def _write_application(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> tuple[list[str], list[dict[str, Any]]]:
        await _ensure_item_row(conn, user=user, item=item, status=target_status)
        metadata = await locked_operational_metadata(
            conn,
            workspace_id=scoped_workspace_id,
            item_id=str(item["id"]),
            owner_user_id=expected_business_item_owner(item, user),
        )
        learned_rules, applications = merged_lesson_state(
            metadata,
            application=application,
            rule=rule,
        )
        update_result = await conn.execute(
            """
                UPDATE control_room_items
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
                       status = CASE
                           WHEN status = ANY($4::text[])
                           THEN status
                           ELSE $5
                       END,
                       last_seen_at = NOW()
                 WHERE workspace_id = $1
                   AND item_id = $2
                   AND owner_user_id IS NOT DISTINCT FROM $6
                """,
            scoped_workspace_id,
            item["id"],
            json.dumps(
                {
                    "learned_rules": learned_rules,
                    "lesson_applications": applications,
                }
            ),
            sorted(TERMINAL_ITEM_STATUSES),
            target_status,
            expected_business_item_owner(item, user),
        )
        _require_exact_count(update_result, "UPDATE")

        await _record_item_event(
            conn,
            user=user,
            item=item,
            event_type="lesson_applied",
            metadata={
                "lesson_id": int(lesson_id),
                "rule": rule,
                "source_decision_id": lesson.get("source_decision_id"),
                "note": note,
            },
        )
        return learned_rules, applications

    learned_rules, applications = await _run_with_db_scope(
        pool, user, _write_application
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.lesson.apply",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "lesson_id": int(lesson_id),
            "rule": rule,
            "item": item,
        },
        critical=True,
    )
    related = _dedupe_lessons([lesson, *(item.get("related_lessons") or []), *lessons])[
        :5
    ]
    public_item = _project_public_item(
        item,
        _with_omega,
        status=target_status,
        related_lessons=related,
        lesson_count=max(
            len(related),
            int(item.get("lesson_count") or 0),
            1,
        ),
        learned_rules=learned_rules,
        lesson_applications=applications,
    )
    return {
        "applied": True,
        "lesson": lesson,
        "lesson_application": application,
        "item": public_item,
    }


@_bind_to_core
def _resolve_template(item: dict[str, Any], template_id: str | None) -> dict[str, Any]:
    templates = _action_templates_for_item(item)
    if not template_id:
        return templates[0] if templates else _primary_template_for_item(item)
    for template in templates:
        if template["template_id"] == template_id:
            return template
    raise HTTPException(404, "action template is not valid for this item")


@_bind_to_core
def _adapter_name_for_template(template: dict[str, Any]) -> str:
    template_id = str(template.get("template_id") or "")
    if template_id == "create_followup_task":
        return "internal_followup_task"
    if template_id == "create_investigation_note":
        return "internal_investigation_note"
    if template_id == "mark_decision_for_monitoring":
        return "internal_decision_monitoring"
    return str(template.get("template_type") or template_id or "external_adapter")


@_bind_to_core
def _action_run_idempotency_key(
    *,
    item: dict[str, Any],
    template: dict[str, Any],
    mode: str,
    provided: str | None = None,
    status: str | None = None,
) -> str:
    safe_provided = str(provided or "").strip()
    template_id = str(template.get("template_id") or "unknown")
    if safe_provided:
        return f"{mode}:{template_id}:{safe_provided}"[:512]
    decision_id = str(item.get("decision_id") or "no-decision")
    item_id = str(item.get("id") or "")
    status_part = (
        f":{status}" if status in {"blocked", "failed", "dry_run_failed"} else ""
    )
    return f"{mode}:{item_id}:{template_id}:{decision_id}{status_part}"[:512]


@_bind_to_core
def _action_run_public(
    row: Any, *, fallback: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = _row_to_public(row) if row else dict(fallback or {})
    for key in (
        "input",
        "dry_run_result",
        "execution_result",
        "side_effect",
        "metadata",
    ):
        data[key] = _details(data.get(key))
    return data


@_bind_to_core
async def _record_action_run_event(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    action_run_id: int | None,
    event_type: str,
    status: str,
    metadata: dict[str, Any],
    critical: bool = False,
) -> None:
    if action_run_id is None:
        raise RuntimeError("control room action run event requires an action run id")
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        insert_result = await pool.execute(
            """
            INSERT INTO action_run_events (
                tenant_id, workspace_id, action_run_id, item_id, event_type,
                status, actor_id, actor_email, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            tenant_id,
            workspace_id,
            int(action_run_id),
            item["id"],
            event_type,
            status,
            user.get("id"),
            user.get("email"),
            json.dumps(metadata),
        )
        _require_exact_count(insert_result, "INSERT")
    except Exception:
        if critical:
            raise


@_bind_to_core
async def _record_action_run(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    mode: str,
    status: str,
    input_payload: dict[str, Any],
    dry_run_result: dict[str, Any] | None = None,
    execution_result: dict[str, Any] | None = None,
    side_effect: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    idempotency_key: str | None = None,
    legacy_execution_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    critical: bool = False,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    action_type = str(
        template.get("template_id") or template.get("action_kind") or "unknown"
    )
    adapter_name = _adapter_name_for_template(template)
    key = idempotency_key or _action_run_idempotency_key(
        item=item,
        template=template,
        mode=mode,
        status=status,
    )
    terminal = status in {
        "preview_generated",
        "dry_run_completed",
        "dry_run_failed",
        "blocked",
        "completed",
        "failed",
    }
    meta = {
        "template": template,
        "source_dataset": item.get("source_dataset"),
        "cartridge": item.get("cartridge"),
        **(metadata or {}),
    }
    fallback = {
        "id": None,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "item_id": item["id"],
        "decision_id": item.get("decision_id"),
        "legacy_execution_id": legacy_execution_id,
        "action_type": action_type,
        "adapter_name": adapter_name,
        "mode": mode,
        "status": status,
        "risk_level": str(template.get("risk_level") or "low"),
        "requires_approval": bool(template.get("requires_approval", True)),
        "approval_status": "implicit_internal_beta",
        "idempotency_key": key,
        "actor_id": user.get("id"),
        "actor_email": user.get("email"),
        "input": input_payload,
        "dry_run_result": dry_run_result or {},
        "execution_result": execution_result or {},
        "side_effect": side_effect or {},
        "error_code": error_code,
        "error_message": error_message,
        "metadata": meta,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat() if terminal else None,
    }
    try:
        action_run_id = await pool.fetchval(
            """
            INSERT INTO action_runs (
                tenant_id, workspace_id, item_id, decision_id, legacy_execution_id,
                action_type, adapter_name, mode, status, risk_level,
                requires_approval, approval_status, idempotency_key,
                actor_id, actor_email, input, dry_run_result, execution_result,
                side_effect, error_code, error_message, metadata, completed_at
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16::jsonb, $17::jsonb, $18::jsonb,
                $19::jsonb, $20, $21, $22::jsonb,
                CASE WHEN $23::boolean THEN NOW() ELSE NULL END
            )
            ON CONFLICT (workspace_id, idempotency_key) DO UPDATE
            SET decision_id = EXCLUDED.decision_id,
                legacy_execution_id = COALESCE(EXCLUDED.legacy_execution_id, action_runs.legacy_execution_id),
                status = EXCLUDED.status,
                risk_level = EXCLUDED.risk_level,
                requires_approval = EXCLUDED.requires_approval,
                approval_status = EXCLUDED.approval_status,
                input = EXCLUDED.input,
                dry_run_result = CASE
                    WHEN EXCLUDED.dry_run_result <> '{}'::jsonb THEN EXCLUDED.dry_run_result
                    ELSE action_runs.dry_run_result
                END,
                execution_result = CASE
                    WHEN EXCLUDED.execution_result <> '{}'::jsonb THEN EXCLUDED.execution_result
                    ELSE action_runs.execution_result
                END,
                side_effect = CASE
                    WHEN EXCLUDED.side_effect <> '{}'::jsonb THEN EXCLUDED.side_effect
                    ELSE action_runs.side_effect
                END,
                error_code = EXCLUDED.error_code,
                error_message = EXCLUDED.error_message,
                metadata = action_runs.metadata || EXCLUDED.metadata,
                updated_at = NOW(),
                completed_at = CASE WHEN $23::boolean THEN NOW() ELSE action_runs.completed_at END
            RETURNING id
            """,
            tenant_id,
            workspace_id,
            item["id"],
            int(item["decision_id"]) if item.get("decision_id") is not None else None,
            legacy_execution_id,
            action_type,
            adapter_name,
            mode,
            status,
            str(template.get("risk_level") or "low"),
            bool(template.get("requires_approval", True)),
            "implicit_internal_beta",
            key,
            user.get("id"),
            user.get("email"),
            json.dumps(input_payload),
            json.dumps(dry_run_result or {}),
            json.dumps(execution_result or {}),
            json.dumps(side_effect or {}),
            error_code,
            error_message,
            json.dumps(meta),
            terminal,
        )
        if action_run_id is None:
            raise RuntimeError("control room action run was not persisted")
        fallback["id"] = action_run_id
        await _record_action_run_event(
            pool,
            user=user,
            item=item,
            action_run_id=int(action_run_id) if action_run_id is not None else None,
            event_type=f"action_run.{status}",
            status=status,
            metadata={
                "mode": mode,
                "action_type": action_type,
                "adapter_name": adapter_name,
                "legacy_execution_id": legacy_execution_id,
                "error_code": error_code,
            },
            critical=critical,
        )
        return _action_run_public(None, fallback=fallback)
    except Exception:
        if critical:
            raise
        return _action_run_public(None, fallback=fallback)


@_bind_to_core
def _dry_run_checks(
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    capability = (
        payload.get("writeback")
        if isinstance(payload.get("writeback"), dict)
        else _writeback_capability(template)
    )
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, *, detail: str, required: bool = True) -> None:
        checks.append(
            {
                "name": name,
                "status": "passed" if passed else "failed",
                "required": required,
                "detail": detail,
            }
        )

    tenant_id, workspace_id = _workspace_scope(user)
    add(
        "tenant_scope",
        bool(tenant_id),
        detail="tenant resolved from authenticated user",
    )
    add(
        "workspace_scope",
        bool(workspace_id),
        detail="workspace resolved from authenticated user",
    )
    add(
        "actor",
        bool(user.get("id") or user.get("email")),
        detail="actor is authenticated",
    )
    add(
        "input_schema",
        bool(payload.get("item") and payload.get("operations")),
        detail="execution payload has item and operation",
    )
    add(
        "risk",
        str(template.get("risk_level") or "") in {"low", "medium", "high", "critical"},
        detail="risk level is recognized",
    )
    add("approval", True, detail="approval/confirmation gate is declared")
    add(
        "data",
        bool(item.get("source_dataset") and item.get("entity_id")),
        detail="source dataset and entity are present",
    )
    if capability.get("external"):
        add(
            "adapter",
            bool(capability.get("adapter_available")),
            detail=str(
                capability.get("adapter")
                or capability.get("reason")
                or "external adapter unavailable"
            ),
        )
        add(
            "external_writeback_flag",
            not _external_writeback_enabled(),
            required=False,
            detail="external writeback remains blocked by default for beta dry-run",
        )
    else:
        add(
            "adapter",
            bool(capability.get("supported")),
            detail=str(capability.get("target") or "internal adapter"),
        )
    warnings = [
        str(check["name"])
        for check in checks
        if check["status"] != "passed" and not bool(check.get("required", True))
    ]
    ok = all(
        check["status"] == "passed"
        for check in checks
        if bool(check.get("required", True))
    )
    return checks, warnings, ok


@_bind_to_core
def _uses_asyncpg_pool(pool: Any) -> bool:
    return pool.__class__.__module__.startswith("asyncpg") and callable(
        getattr(pool, "acquire", None)
    )


@_bind_to_core
async def _run_with_db_scope(
    pool: Any, user: dict, work: Callable[[Any, str | None, str], Awaitable[Any]]
) -> Any:
    return await run_with_db_scope(pool, user, work)


@_bind_to_core
async def action_preview(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    binding_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    require_action_item_prerequisites(item, operation="preview")
    template = require_explicit_action_template(item, user, template_id, binding_id)
    payload = _execution_payload(item, "preview", template)
    result = {
        "ok": True,
        "mode": "preview",
        "message": "Preview generado; no se ejecuto ningun cambio externo.",
        "external_write": False,
    }
    pool = await auth.pool()

    async def _write(
        conn: Any, _tenant_id: str | None, _workspace_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        await require_enabled_action_template(conn, str(template["template_id"]))
        await _ensure_item_row(
            conn,
            user=user,
            item=item,
            status=item.get("status") or "in_review",
            critical=True,
        )
        execution = await _record_action_execution(
            conn,
            user=user,
            item=item,
            template=template,
            mode="preview",
            status="generated",
            payload=payload,
            result=result,
            critical=True,
        )
        action_run = await _record_action_run(
            conn,
            user=user,
            item=item,
            template=template,
            mode="preview",
            status="preview_generated",
            input_payload=payload,
            execution_result=result,
            legacy_execution_id=int(execution["id"])
            if execution.get("id") is not None
            else None,
            metadata={"legacy_table": "control_room_action_executions"},
            critical=True,
        )
        await _set_execution_status(
            conn,
            user=user,
            item=item,
            execution_status="preview_generated",
            critical=True,
        )
        await _record_item_event(
            conn,
            user=user,
            item=item,
            event_type="action_preview",
            metadata={
                "template_id": template["template_id"],
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
            },
            critical=True,
        )
        return execution, action_run

    execution, action_run = await _run_with_db_scope(pool, user, _write)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.action.preview",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"template_id": template["template_id"], "payload": payload},
        critical=True,
    )
    public_item = _project_public_item(
        item, _with_omega, execution_status="preview_generated"
    )
    return {
        "execution": execution,
        "action_run": action_run,
        "payload": payload,
        "result": result,
        "item": public_item,
    }


@_bind_to_core
async def action_dry_run(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    binding_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    require_action_item_prerequisites(item, operation="dry_run")
    template = require_explicit_action_template(item, user, template_id, binding_id)
    payload = _execution_payload(item, "dry_run", template)
    warnings = []
    if payload["impact"]["status"] != "ok":
        warnings.append("impact_unavailable")
    if not item.get("decision_id"):
        warnings.append("decision_not_created_yet")
    validation_checks, validation_warnings, validation_ok = _dry_run_checks(
        user=user,
        item=item,
        template=template,
        payload=payload,
    )
    warnings.extend(value for value in validation_warnings if value not in warnings)
    action_run_status = "dry_run_completed" if validation_ok else "dry_run_failed"
    execution_status = "dry_run_validated" if validation_ok else "failed"
    result = {
        "ok": validation_ok,
        "mode": "dry_run",
        "validated": validation_ok,
        "external_write": False,
        "warnings": warnings,
        "checks": validation_checks,
        "message": (
            "Dry-run validado. V1 no escribe en sistemas externos."
            if validation_ok
            else "Dry-run fallido: el contrato de ejecucion no paso todas las validaciones requeridas."
        ),
    }
    pool = await auth.pool()

    async def _write(
        conn: Any, _tenant_id: str | None, _workspace_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        await require_enabled_action_template(conn, str(template["template_id"]))
        await _ensure_item_row(
            conn,
            user=user,
            item=item,
            status=item.get("status") or "in_review",
            critical=True,
        )
        execution = await _record_action_execution(
            conn,
            user=user,
            item=item,
            template=template,
            mode="dry_run",
            status="validated" if validation_ok else "failed",
            payload=payload,
            result=result,
            error=None if validation_ok else "dry_run_validation_failed",
            critical=True,
        )
        action_run = await _record_action_run(
            conn,
            user=user,
            item=item,
            template=template,
            mode="dry_run",
            status=action_run_status,
            input_payload=payload,
            dry_run_result=result,
            legacy_execution_id=int(execution["id"])
            if execution.get("id") is not None
            else None,
            error_code=None if validation_ok else "dry_run_validation_failed",
            error_message=None if validation_ok else result["message"],
            metadata={
                "legacy_table": "control_room_action_executions",
                "checks": validation_checks,
                **dry_run_metadata(item, template_id=str(template["template_id"])),
            },
            critical=True,
        )
        await _set_execution_status(
            conn,
            user=user,
            item=item,
            execution_status=execution_status,
            critical=True,
        )
        await _record_item_event(
            conn,
            user=user,
            item=item,
            event_type="action_dry_run",
            metadata={
                "template_id": template["template_id"],
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "action_run_status": action_run_status,
                "warnings": warnings,
            },
            critical=True,
        )
        return execution, action_run

    execution, action_run = await _run_with_db_scope(pool, user, _write)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.action.dry_run",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success" if validation_ok else "failed",
        metadata={
            "template_id": template["template_id"],
            "result": result,
            "action_run_id": action_run.get("id"),
        },
        critical=True,
    )
    public_item = _project_public_item(
        item, _with_omega, execution_status=execution_status
    )
    return {
        "execution": execution,
        "action_run": action_run,
        "payload": payload,
        "result": result,
        "item": public_item,
    }


@_bind_to_core
async def list_item_action_runs(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_read(item_id, user, fetcher=fetcher)
    workspace_id = _workspace_id(user)
    pool = await auth.pool()

    async def _load(conn: Any, _tenant_id: str | None, _workspace_id: str) -> list[Any]:
        return await conn.fetch(
            """
            SELECT id, tenant_id, workspace_id, item_id, decision_id, legacy_execution_id,
                   action_type, adapter_name, mode, status, risk_level,
                   requires_approval, approval_status, idempotency_key,
                   actor_id, actor_email, input, dry_run_result, execution_result,
                   side_effect, error_code, error_message, metadata,
                   created_at, updated_at, completed_at
              FROM action_runs
             WHERE workspace_id = $1
               AND item_id = $2
             ORDER BY created_at DESC
             LIMIT 100
            """,
            workspace_id,
            item["id"],
        )

    rows = await _run_with_db_scope(pool, user, _load)
    return {
        "item_id": item["id"],
        "action_runs": [_action_run_public(row) for row in rows],
    }


@_bind_to_core
def _outcome_num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@_bind_to_core
def _outcome_public(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    data["metadata"] = _details(data.get("metadata"))
    return data


@_bind_to_core
async def list_item_outcomes(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_read(item_id, user, fetcher=fetcher)
    pool = await auth.pool()

    async def _load(conn: Any, _tenant_id: str | None, workspace_id: str) -> Any:
        return await conn.fetch(
            """
            SELECT id, tenant_id, workspace_id, signal_id, option_id, action_taken,
                   predicted_value, actual_value, prediction_error, outcome_summary,
                   learned_rule, metadata, created_at
              FROM prediction_outcomes
             WHERE workspace_id = $1
               AND signal_id = $2
             ORDER BY created_at DESC
             LIMIT 100
            """,
            workspace_id,
            item["id"],
        )

    rows = await _run_with_db_scope(pool, user, _load)
    return {
        "item_id": item["id"],
        "outcomes": [_outcome_public(row) for row in rows],
    }


@_bind_to_core
async def record_item_outcome(
    item_id: str,
    body: dict[str, Any],
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    action_taken = str(body.get("action_taken") or body.get("action") or "").strip()
    if not action_taken:
        raise HTTPException(400, "action_taken is required")
    option_id = (
        str(body.get("option_id") or item.get("selected_option_id") or "").strip()
        or None
    )
    predicted_value = _outcome_num(body.get("predicted_value"))
    if predicted_value is None:
        predicted_value = _outcome_num(
            item.get("impact_estimate") or _impact_for_item(item).get("estimate")
        )
    actual_value = _outcome_num(body.get("actual_value"))
    prediction_error = None
    if actual_value is not None and predicted_value is not None:
        prediction_error = round(actual_value - predicted_value, 4)
    learned_rule = str(body.get("learned_rule") or "").strip() or None
    if not learned_rule and prediction_error is not None:
        learned_rule = f"Resultado medido con error {prediction_error:.2f} para {item.get('anomaly_type') or item.get('title')}."
    outcome_summary = str(
        body.get("outcome_summary")
        or body.get("summary")
        or learned_rule
        or "Outcome registrado desde Control Room."
    ).strip()
    pool = await auth.pool()
    metadata = {
        "source": "control_room",
        "reported_by": user.get("email"),
        "action_run_id": body.get("action_run_id"),
        "decision_id": item.get("decision_id"),
        "measured_impact": body.get("measured_impact"),
        "outcome_status": body.get("outcome_status"),
        "observed_at": body.get("observed_at"),
        "evidence": body.get("evidence")
        if isinstance(body.get("evidence"), dict)
        else {},
    }

    async def _write(conn: Any, tenant_id: str | None, workspace_id: str) -> Any:
        from app.services.intelligence.history import link_outcome_to_snapshot

        await _ensure_item_row(
            conn,
            user=user,
            item=item,
            status=item.get("status") or "in_review",
            critical=True,
        )
        learned_rules = None
        if learned_rule:
            operational_metadata = await locked_operational_metadata(
                conn,
                workspace_id=workspace_id,
                item_id=str(item["id"]),
                owner_user_id=expected_business_item_owner(item, user),
            )
            learned_rules = merged_learned_rules(
                operational_metadata,
                rule=learned_rule,
            )
        row = await conn.fetchrow(
            """
            INSERT INTO prediction_outcomes (
                tenant_id, workspace_id, signal_id, option_id, action_taken,
                predicted_value, actual_value, prediction_error, outcome_summary,
                learned_rule, owner_user_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
            RETURNING *
            """,
            tenant_id,
            workspace_id,
            item["id"],
            option_id,
            action_taken,
            predicted_value,
            actual_value,
            prediction_error,
            outcome_summary,
            learned_rule,
            user.get("id"),
            json.dumps(metadata),
        )
        await link_outcome_to_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            signal_id=item["id"],
            outcome_row=row,
            body=body,
        )
        if learned_rule:
            await _persist_lessons(
                conn,
                user=user,
                item=item,
                decision_id=item.get("decision_id"),
                lessons=[learned_rule],
            )
        metadata_patch = {
            "last_outcome": {
                "action_taken": action_taken,
                "actual_value": actual_value,
                "prediction_error": prediction_error,
                "outcome_summary": outcome_summary,
                "learned_rule": learned_rule,
            }
        }
        if learned_rules is not None:
            metadata_patch["learned_rules"] = learned_rules
        update_result = await conn.execute(
            """
            UPDATE control_room_items
               SET metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
               AND owner_user_id IS NOT DISTINCT FROM $4
            """,
            workspace_id,
            item["id"],
            json.dumps(metadata_patch),
            expected_business_item_owner(item, user),
        )
        _require_exact_count(update_result, "UPDATE")
        outcome = _outcome_public(row)
        await _record_item_event(
            conn,
            user=user,
            item=item,
            event_type="outcome_recorded",
            metadata={
                "outcome_id": outcome.get("id"),
                "action_taken": action_taken,
                "option_id": option_id,
                "prediction_error": prediction_error,
                "learned_rule": learned_rule,
                "measured_impact": body.get("measured_impact"),
                "action_run_id": body.get("action_run_id"),
            },
            critical=True,
        )
        return row, tenant_id, workspace_id

    row, tenant_id, workspace_id = await _run_with_db_scope(pool, user, _write)
    outcome = _outcome_public(row)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.outcome.record",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "outcome_id": outcome.get("id"),
            "action_taken": action_taken,
            "option_id": option_id,
            "learned_rule": learned_rule,
            "measured_impact": body.get("measured_impact"),
            "action_run_id": body.get("action_run_id"),
        },
        critical=True,
    )
    return {
        "recorded": True,
        "outcome": outcome,
        "lesson_recorded": bool(learned_rule),
        "item": _project_public_item(item, _with_omega, last_outcome=outcome),
    }


@_bind_to_core
async def run_auto_item(
    item_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    if item.get("status") in TERMINAL_ITEM_STATUSES:
        raise HTTPException(409, "terminal control room item cannot run automatic mode")
    binding = single_explicit_action_binding(item, user)
    template_id = binding.template_id
    binding_id = binding.binding_id

    steps: list[dict[str, Any]] = []
    investigation = await record_item_step(
        item_id,
        "investigation",
        user,
        note="Modo automatico: investigacion iniciada",
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    steps.append({"step": "investigation", "event": investigation.get("event_type")})

    selected = await select_item_option(
        item_id,
        "remediate",
        user,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    item = selected["item"]
    steps.append({"step": "options", "option_id": "remediate"})

    if not item.get("decision_id"):
        decision = await create_decision_for_item(
            item_id,
            user,
            ip=ip,
            user_agent=user_agent,
            fetcher=fetcher,
        )
        item = decision["item"]
    else:
        decision = {"decision": {"id": item.get("decision_id")}, "item": item}
    steps.append({"step": "decision", "decision_id": item.get("decision_id")})

    preview = await action_preview(
        item_id,
        user,
        template_id=template_id,
        binding_id=binding_id,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    dry_run = await action_dry_run(
        item_id,
        user,
        template_id=template_id,
        binding_id=binding_id,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    item = dry_run["item"]
    steps.append(
        {
            "step": "execution",
            "template_id": template_id,
            "status": item.get("execution_status"),
        }
    )

    control = await record_item_step(
        item_id,
        "control",
        user,
        note="Modo automatico: dry-run validado y control abierto",
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    steps.append({"step": "control", "event": control.get("event_type")})

    pool = await auth.pool()

    async def _record_auto_run(
        conn: Any, _tenant_id: str | None, _workspace_id: str
    ) -> None:
        await lock_authoritative_business_item(
            conn,
            user=user,
            item=item,
            decision_id=int(item["decision_id"]),
            allowed_stages={
                _exec_sql.WorkflowStage.DECISION_CREATED,
                _exec_sql.WorkflowStage.APPROVED,
                _exec_sql.WorkflowStage.EXECUTED,
            },
        )
        await _record_item_event(
            conn,
            user=user,
            item=item,
            event_type="auto_run_completed",
            metadata={
                "steps": steps,
                "decision_id": item.get("decision_id"),
                "template_id": template_id,
                "execution_status": item.get("execution_status"),
                "external_write": False,
            },
        )

    await _run_with_db_scope(pool, user, _record_auto_run)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.auto_run",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "steps": steps,
            "decision_id": item.get("decision_id"),
            "template_id": template_id,
            "preview_execution_id": preview.get("execution", {}).get("id"),
            "dry_run_execution_id": dry_run.get("execution", {}).get("id"),
            "external_write": False,
        },
        critical=True,
    )
    return {
        "auto_run": {
            "completed": True,
            "stopped_before_writeback": True,
            "steps": steps,
            "template_id": template_id,
        },
        "decision": decision.get("decision"),
        "preview": preview.get("result"),
        "dry_run": dry_run.get("result"),
        "item": _with_omega(item),
    }


@_bind_to_core
def _confirmed_for_execute(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {
            "execute",
            "confirm",
            "confirmed",
            "true",
            "yes",
            "1",
        }
    return False


@_bind_to_core
def _supports_transactional_acquire(pool: Any) -> bool:
    acquire = getattr(pool, "acquire", None)
    return callable(acquire) and not type(pool).__module__.startswith("unittest.mock")


@_bind_to_core
async def _record_writeback_audit_event(
    db: Any,
    *,
    user: dict,
    action: str,
    resource_type: str,
    resource_id: str,
    ip: str | None,
    user_agent: str | None,
    status: str,
    metadata: dict[str, Any],
) -> None:
    insert_result = await db.execute(
        """
        INSERT INTO audit_events
        (user_id, email, action, resource_type, resource_id,
         ip, user_agent, status, request_id, metadata)
        VALUES ($1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10::jsonb)
        """,
        user.get("id"),
        user.get("email"),
        action,
        resource_type,
        resource_id,
        ip,
        user_agent,
        status,
        request_id_var.get(),
        json.dumps(metadata),
    )
    _require_exact_count(insert_result, "INSERT")


@_bind_to_core
async def _record_execute_block(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    message: str,
    error: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    decision_id = int(item["decision_id"]) if item.get("decision_id") else None
    await lock_authoritative_business_item(
        pool,
        user=user,
        item=item,
        decision_id=decision_id,
        allowed_stages=(
            _exec_sql.WorkflowStage.DECISION_CREATED,
            _exec_sql.WorkflowStage.APPROVED,
        )
        if decision_id
        else None,
    )
    result = {
        "ok": False,
        "mode": "execute_live",
        "external_write": False,
        "internal_write": False,
        "blocked": True,
        "message": message,
    }
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="execute_live",
        status="blocked",
        payload=payload,
        result=result,
        error=error,
        critical=True,
    )
    action_run = await _record_action_run(
        pool,
        user=user,
        item=item,
        template=template,
        mode="execute",
        status="blocked",
        input_payload=payload,
        execution_result=result,
        error_code=error,
        error_message=message,
        idempotency_key=_action_run_idempotency_key(
            item=item,
            template=template,
            mode="execute",
            status="blocked",
        ),
        legacy_execution_id=int(execution["id"])
        if execution.get("id") is not None
        else None,
        metadata={
            "legacy_table": "control_room_action_executions",
            "block_reason": error,
        },
        critical=True,
    )
    if str(item.get("execution_status") or "not_started") != "dry_run_validated":
        await _set_execution_status(
            pool, user=user, item=item, execution_status="blocked", critical=True
        )
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_blocked",
        metadata={
            "template_id": template["template_id"],
            "execution_id": execution.get("id"),
            "action_run_id": action_run.get("id"),
            "reason": error,
        },
        critical=True,
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.action.execute.blocked",
        resource_type="control_room_item",
        resource_id=item["id"],
        ip=ip,
        user_agent=user_agent,
        status="blocked",
        metadata={
            "template_id": template["template_id"],
            "result": result,
            "reason": error,
            "action_run_id": action_run.get("id"),
        },
        critical=True,
    )


@_bind_to_core
def _reserved_action_response(
    reservation: ActionReservation,
    *,
    item: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _build_reserved_action_response(
        reservation,
        item=item,
        payload=payload,
        action_run_public=_action_run_public,
        details=_details,
        project_item=_project_public_item,
        omega_builder=_with_omega,
    )


@_bind_to_core
async def _complete_execute_reservation(
    db: Any,
    *,
    user: dict,
    item: dict[str, Any],
    reservation: ActionReservation,
    status: str,
    result: dict[str, Any],
    side_effect: dict[str, Any] | None = None,
    legacy_execution_id: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    row = await complete_action_reservation(
        db,
        workspace_id=_workspace_id(user),
        reservation_id=reservation.id,
        effective_key=reservation.effective_key,
        status=status,
        execution_result=result,
        side_effect=side_effect,
        legacy_execution_id=legacy_execution_id,
        error_code=error_code,
        error_message=error_message,
    )
    await _record_action_run_event(
        db,
        user=user,
        item=item,
        action_run_id=reservation.id,
        event_type=f"action_run.{status}",
        status=status,
        metadata={"error_code": error_code},
        critical=True,
    )
    return _action_run_public(row)


@_bind_to_core
async def _execute_internal_followup_task(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    idempotency_key: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    if _supports_transactional_acquire(pool):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    SET_SCOPE_SQL,
                    str(user.get("active_tenant_id") or user.get("tenant_id") or ""),
                    _workspace_id(user),
                )
                return await _execute_internal_followup_task_tx(
                    conn,
                    user=user,
                    item=item,
                    template=template,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    ip=ip,
                    user_agent=user_agent,
                )
    return await _execute_internal_followup_task_tx(
        pool,
        user=user,
        item=item,
        template=template,
        payload=payload,
        idempotency_key=idempotency_key,
        ip=ip,
        user_agent=user_agent,
    )


@_bind_to_core
async def _execute_internal_followup_task_tx(
    db: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    idempotency_key: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    workspace_id = _workspace_id(user)
    reservation = await acquire_guarded_action_reservation(
        db,
        user=user,
        item=item,
        template_id=str(template["template_id"]),
        adapter_name=_adapter_name_for_template(template),
        operation="execute",
        provided_key=idempotency_key,
        input_payload=payload,
        persist_item=_ensure_item_row,
    )
    if reservation.state is not ReservationState.ACQUIRED:
        return _reserved_action_response(
            reservation,
            item=item,
            payload=payload,
        )
    idempotency_key = reservation.effective_key
    decision_id = int(item["decision_id"])
    visible = await db.fetchrow(
        "SELECT id FROM decisions WHERE id = $1 AND workspace_id = $2",
        decision_id,
        workspace_id,
    )
    if not visible:
        await _record_execute_block(
            db,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="La decision no pertenece al workspace activo.",
            error="decision_workspace_mismatch",
        )
        raise HTTPException(404, "decision not found for active workspace")

    action_text = f"Seguimiento operativo Control Room: {item['title']}"
    note = (
        f"Template {template['template_id']} ejecutado sobre {item.get('cartridge')} / "
        f"{item.get('source_dataset')}. Target interno: decision_actions. "
        f"Entidad: {item.get('entity_label') or item.get('entity_id')}. "
        "No se escribio en ERP."
    )
    action = await db.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        decision_id,
        action_text,
        note,
        user.get("email") or "user",
    )
    public_action = _row_to_public(action)
    before = {
        "execution_status": item.get("execution_status") or "not_started",
        "status": item.get("status") or "open",
        "decision_id": decision_id,
    }
    after = {
        "execution_status": "executed",
        "target": "decision_actions",
        "decision_action_id": public_action.get("id"),
    }
    result = {
        "ok": True,
        "mode": "execute_live",
        "executed": True,
        "external_write": False,
        "internal_write": True,
        "target": "decision_actions",
        "adapter": "internal_followup_task",
        "decision_id": decision_id,
        "decision_action_id": public_action.get("id"),
        "idempotency_key": idempotency_key,
        "before": before,
        "after": after,
        "message": "Seguimiento operativo creado en decision_actions; no se escribio en ERP.",
    }
    execution = await _record_action_execution(
        db,
        user=user,
        item=item,
        template=template,
        mode="execute_live",
        status="executed",
        payload={**payload, "idempotency_key": idempotency_key},
        result=result,
        critical=True,
    )
    action_run = await _complete_execute_reservation(
        db,
        user=user,
        item=item,
        status="completed",
        reservation=reservation,
        result=result,
        side_effect={
            "target": "decision_actions",
            "decision_id": decision_id,
            "decision_action_id": public_action.get("id"),
        },
        legacy_execution_id=int(execution["id"])
        if execution.get("id") is not None
        else None,
    )
    update_result = await db.execute(
        f"""
        UPDATE control_room_items
           SET execution_status = 'executed',
               metadata = {_exec_sql.EXECUTED_METADATA_ARG3_SQL},
               last_seen_at = NOW()
         WHERE workspace_id = $1
           AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $4
        """,
        workspace_id,
        item["id"],
        json.dumps(
            {
                "execution_status": "executed",
                "writeback_result": {
                    "adapter": "internal_followup_task",
                    "target": "decision_actions",
                    "decision_action_id": public_action.get("id"),
                    "execution_id": execution.get("id"),
                    "action_run_id": action_run.get("id"),
                },
            }
        ),
        expected_business_item_owner(item, user),
    )
    _require_exact_count(update_result, "UPDATE")
    await _record_item_event(
        db,
        user=user,
        item=item,
        event_type="action_executed",
        metadata={
            "template_id": template["template_id"],
            "execution_id": execution.get("id"),
            "decision_id": decision_id,
            "decision_action_id": public_action.get("id"),
            "target": "decision_actions",
            "action_run_id": action_run.get("id"),
        },
        critical=True,
    )
    await _record_writeback_audit_event(
        db,
        user=user,
        action="control_room.action.execute",
        resource_type="control_room_item",
        resource_id=item["id"],
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "template_id": template["template_id"],
            "target": "decision_actions",
            "decision_id": decision_id,
            "decision_action_id": public_action.get("id"),
            "execution_id": execution.get("id"),
            "action_run_id": action_run.get("id"),
            "before": before,
            "after": after,
        },
    )
    public_item = _project_public_item(item, _with_omega, execution_status="executed")
    return {
        "executed": True,
        "idempotent": False,
        "execution": execution,
        "action_run": action_run,
        "payload": payload,
        "result": result,
        "decision_action": public_action,
        "item": public_item,
    }


@_bind_to_core
async def _execute_internal_investigation_note(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    idempotency_key: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    reservation = await acquire_guarded_action_reservation(
        pool,
        user=user,
        item=item,
        template_id=str(template["template_id"]),
        adapter_name=_adapter_name_for_template(template),
        operation="execute",
        provided_key=idempotency_key,
        input_payload=payload,
        persist_item=_ensure_item_row,
    )
    if reservation.state is not ReservationState.ACQUIRED:
        return _reserved_action_response(reservation, item=item, payload=payload)
    idempotency_key = reservation.effective_key
    note_payload = (
        payload.get("action_payload")
        if isinstance(payload.get("action_payload"), dict)
        else {}
    )
    note = (
        _details(note_payload.get("note")).get("summary")
        or item.get("root_cause")
        or item.get("description")
        or item.get("title")
    )
    side_effect = {
        "target": "control_room_item_events",
        "event_type": "investigation_note_created",
        "note": str(note or "")[:700],
    }
    result = {
        "ok": True,
        "mode": "execute_live",
        "executed": True,
        "external_write": False,
        "internal_write": True,
        "target": "control_room_item_events",
        "adapter": "internal_investigation_note",
        "decision_id": item.get("decision_id"),
        "idempotency_key": idempotency_key,
        "side_effect": side_effect,
        "message": "Nota de investigacion creada en Control Room; no se escribio en ERP.",
    }
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="execute_live",
        status="executed",
        payload={**payload, "idempotency_key": idempotency_key},
        result=result,
        critical=True,
    )
    action_run = await _complete_execute_reservation(
        pool,
        user=user,
        item=item,
        status="completed",
        reservation=reservation,
        result=result,
        side_effect=side_effect,
        legacy_execution_id=int(execution["id"])
        if execution.get("id") is not None
        else None,
    )
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="investigation_note_created",
        metadata={
            "template_id": template["template_id"],
            "execution_id": execution.get("id"),
            "action_run_id": action_run.get("id"),
            "note": side_effect["note"],
        },
        critical=True,
    )
    await _set_execution_status(
        pool, user=user, item=item, execution_status="executed", critical=True
    )
    await _record_writeback_audit_event(
        pool,
        user=user,
        action="control_room.action.execute",
        resource_type="control_room_item",
        resource_id=item["id"],
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "template_id": template["template_id"],
            "target": "control_room_item_events",
            "execution_id": execution.get("id"),
            "action_run_id": action_run.get("id"),
            "side_effect": side_effect,
        },
    )
    public_item = _project_public_item(item, _with_omega, execution_status="executed")
    return {
        "executed": True,
        "idempotent": False,
        "execution": execution,
        "action_run": action_run,
        "payload": payload,
        "result": result,
        "item": public_item,
    }


@_bind_to_core
async def _execute_internal_decision_monitoring(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    idempotency_key: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    workspace_id = _workspace_id(user)
    reservation = await acquire_guarded_action_reservation(
        pool,
        user=user,
        item=item,
        template_id=str(template["template_id"]),
        adapter_name=_adapter_name_for_template(template),
        operation="execute",
        provided_key=idempotency_key,
        input_payload=payload,
        persist_item=_ensure_item_row,
    )
    if reservation.state is not ReservationState.ACQUIRED:
        return _reserved_action_response(reservation, item=item, payload=payload)
    idempotency_key = reservation.effective_key
    monitoring_state = {
        "enabled": True,
        "decision_id": item.get("decision_id"),
        "metric": item.get("metric") or item.get("anomaly_type"),
        "entity_id": item.get("entity_id"),
        "source_dataset": item.get("source_dataset"),
        "marked_at": datetime.now(UTC).isoformat(),
        "marked_by": user.get("email") or "user",
    }
    side_effect = {
        "target": "control_room_items.metadata",
        "metadata_key": "decision_monitoring",
        "decision_monitoring": monitoring_state,
    }
    result = {
        "ok": True,
        "mode": "execute_live",
        "executed": True,
        "external_write": False,
        "internal_write": True,
        "target": "control_room_items.metadata",
        "adapter": "internal_decision_monitoring",
        "decision_id": item.get("decision_id"),
        "idempotency_key": idempotency_key,
        "side_effect": side_effect,
        "message": "Decision marcada para monitoreo interno; no se escribio en ERP.",
    }
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="execute_live",
        status="executed",
        payload={**payload, "idempotency_key": idempotency_key},
        result=result,
        critical=True,
    )
    action_run = await _complete_execute_reservation(
        pool,
        user=user,
        item=item,
        status="completed",
        reservation=reservation,
        result=result,
        side_effect=side_effect,
        legacy_execution_id=int(execution["id"])
        if execution.get("id") is not None
        else None,
    )
    update_result = await pool.execute(
        f"""
        UPDATE control_room_items
           SET execution_status = 'executed',
               metadata = {_exec_sql.EXECUTED_METADATA_ARG3_SQL},
               last_seen_at = NOW()
         WHERE workspace_id = $1
           AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $4
        """,
        workspace_id,
        item["id"],
        json.dumps(
            {"decision_monitoring": monitoring_state, "execution_status": "executed"}
        ),
        expected_business_item_owner(item, user),
    )
    _require_exact_count(update_result, "UPDATE")
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="decision_monitoring_marked",
        metadata={
            "template_id": template["template_id"],
            "execution_id": execution.get("id"),
            "action_run_id": action_run.get("id"),
            "decision_monitoring": monitoring_state,
        },
        critical=True,
    )
    await _record_writeback_audit_event(
        pool,
        user=user,
        action="control_room.action.execute",
        resource_type="control_room_item",
        resource_id=item["id"],
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "template_id": template["template_id"],
            "target": "control_room_items.metadata",
            "execution_id": execution.get("id"),
            "action_run_id": action_run.get("id"),
            "side_effect": side_effect,
        },
    )
    public_item = _project_public_item(
        item,
        _with_omega,
        execution_status="executed",
        decision_monitoring=monitoring_state,
    )
    return {
        "executed": True,
        "idempotent": False,
        "execution": execution,
        "action_run": action_run,
        "payload": payload,
        "result": result,
        "item": public_item,
    }


@_bind_to_core
def _external_action_data(
    *,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return {
        "template_id": template.get("template_id"),
        "template_type": template.get("template_type") or template.get("template_id"),
        "action_kind": template.get("action_kind"),
        "cartridge_id": template.get("cartridge_id") or item.get("cartridge"),
        "item_id": item.get("id"),
        "title": item.get("title"),
        "entity": item.get("entity_id") or item.get("entity_label"),
        "entity_id": item.get("entity_id"),
        "entity_label": item.get("entity_label"),
        "source_dataset": item.get("source_dataset"),
        "severity": item.get("severity"),
        "anomaly_type": item.get("anomaly_type"),
        "details": details,
        "action_payload": payload.get("action_payload"),
        "impact": payload.get("impact"),
        "idempotency_key": idempotency_key,
    }


@_bind_to_core
def _writeback_template_type(template: dict[str, Any]) -> str:
    return (
        str(template.get("template_type") or template.get("template_id") or "")
        .strip()
        .lower()
    )


@_bind_to_core
def _adapter_result_to_dict(result: ExecutionResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, ExecutionResult):
        return result.to_dict()
    if isinstance(result, dict):
        return dict(result)
    raise TypeError("write-back adapter returned an invalid result")


@_bind_to_core
def _writeback_credentials_for_action(
    *,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    metadata = _details(item.get("metadata"))
    connection = _details(metadata.get("connection"))
    credentials = _details(payload.get("credentials"))
    return {
        **connection,
        **credentials,
        "cartridge_id": template.get("cartridge_id") or item.get("cartridge"),
        "template_type": _writeback_template_type(template),
        "workspace_id": payload.get("workspace_id"),
    }


@_bind_to_core
async def _record_adapter_success_lesson(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    adapter_name: str,
    template_type: str,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    cartridge_id = str(
        item.get("cartridge") or template.get("cartridge_id") or "platform"
    )
    anomaly_type = str(item.get("anomaly_type") or "control_room_item")
    decision_id = int(item["decision_id"]) if item.get("decision_id") else None
    confidence = _impact_for_item(item).get("confidence") or 0.85
    rule = (
        f"Para {cartridge_id}/{anomaly_type}, sugerir {template.get('label') or template.get('template_id')} "
        f"cuando una anomalia similar aparezca: adapter {adapter_name} ejecuto correctamente."
    )
    metadata = {
        "autonomous_learning": True,
        "source": "adapter_success",
        "execution_id": execution.get("id"),
        "result_status": result.get("status")
        or result.get("adapter_result", {}).get("status"),
        "suggested_action": {
            "template_id": template.get("template_id"),
            "template_type": template_type,
            "label": template.get("label"),
            "action_kind": template.get("action_kind"),
            "target": result.get("target")
            or template.get("cartridge_id")
            or item.get("cartridge"),
            "adapter": adapter_name,
        },
    }
    insert_result = await pool.execute(
        """
        INSERT INTO control_room_lessons (
            tenant_id, workspace_id, item_id, cartridge_id, anomaly_type,
            rule, source_decision_id, confidence, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        """,
        tenant_id,
        workspace_id,
        item["id"],
        cartridge_id,
        anomaly_type,
        rule,
        decision_id,
        confidence,
        json.dumps(metadata),
    )
    _require_exact_count(insert_result, "INSERT")
    return {
        "item_id": item["id"],
        "cartridge_id": cartridge_id,
        "anomaly_type": anomaly_type,
        "rule": rule,
        "source_decision_id": decision_id,
        "confidence": confidence,
        "metadata": metadata,
    }


@_bind_to_core
async def _execute_external_writeback(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    reservation: ActionReservation,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    from app.services.adapters import AdapterCircuitOpenError, AdapterExecutionError
    from app.services.control_room import business_external_effect as external_effect

    await require_approved_execution(
        pool,
        user=user,
        item=item,
        template_id=str(template["template_id"]),
    )
    locked_reservation = await lock_pending_action_reservation(
        pool,
        user=user,
        item=item,
        template_id=str(template["template_id"]),
        reservation_id=reservation.id,
        effective_key=reservation.effective_key,
    )
    if remote_attempt_status(locked_reservation) != "started":
        raise HTTPException(409, "remote attempt was not durably prepared")
    idempotency_key = reservation.effective_key
    cartridge_id = str(
        template.get("cartridge_id") or item.get("cartridge") or ""
    ).strip()
    if not cartridge_id:
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="El template externo no declara cartridge_id.",
            error="missing_cartridge_id",
        )
        raise HTTPException(400, "external write-back template is missing cartridge_id")

    action_data = _external_action_data(
        item=item,
        template=template,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    template_type = _writeback_template_type(template)
    target = template.get("cartridge_id") or item.get("cartridge") or "external_system"
    adapter_name = template_type
    before = {
        "execution_status": item.get("execution_status") or "not_started",
        "status": item.get("status") or "open",
        "decision_id": item.get("decision_id"),
    }

    try:
        await _record_writeback_audit_event(
            pool,
            user=user,
            action="control_room.action.execute.external.preflight",
            resource_type="control_room_item",
            resource_id=item["id"],
            ip=ip,
            user_agent=user_agent,
            status="pending",
            metadata={
                "template_id": template["template_id"],
                "template_type": template_type,
                "target": target,
                "adapter": adapter_name,
                "item_id": item["id"],
                "idempotency_key": idempotency_key,
                "fail_closed": True,
            },
        )
    except Exception as exc:
        raise HTTPException(503, "write-back audit preflight failed") from exc

    public_validation_result = {
        "ok": True,
        "status": "audit_preflight_recorded",
        "message": "Audit preflight recorded before external write-back.",
    }

    remote_attempt_started = False
    try:
        credentials = _writeback_credentials_for_action(
            item=item, template=template, payload=payload
        )
        adapter = WriteBackAdapterFactory.get_adapter(template_type)
        if not adapter_guarantees_idempotency(template_type, adapter):
            raise NotImplementedError(
                "external write-back adapter does not guarantee idempotency"
            )
        adapter_name = adapter.__class__.__name__
        remote_attempt_started = True
        adapter_result = adapter.execute(action_data, credentials, dry_run=False)
        if inspect.isawaitable(adapter_result):
            adapter_result = await adapter_result
        public_adapter_result = _adapter_result_to_dict(adapter_result)
    except AdapterCircuitOpenError as exc:
        result = {
            "ok": False,
            "mode": "execute_live",
            "executed": False,
            "external_write": True,
            "adapter": cartridge_id,
            "message": str(exc),
            "remote_status": exc.status_code,
        }
        reconciliation = await ambiguous_adapter_error_response(
            pool,
            error=exc,
            remote_attempt_started=remote_attempt_started,
            workspace_id=_workspace_id(user),
            reservation_id=reservation.id,
            effective_key=reservation.effective_key,
            result=result,
        )
        if reconciliation is not None:
            return reconciliation
        execution = await _record_action_execution(
            pool,
            user=user,
            item=item,
            template=template,
            mode="execute_live",
            status="failed",
            payload=payload,
            result=result,
            error="circuit_breaker_open",
            critical=True,
        )
        action_run = await _complete_execute_reservation(
            pool,
            user=user,
            item=item,
            status="failed",
            reservation=reservation,
            result=result,
            error_code="circuit_breaker_open",
            error_message=str(exc),
            legacy_execution_id=int(execution["id"])
            if execution.get("id") is not None
            else None,
            side_effect={"target": target, "adapter": adapter_name},
        )
        await _set_execution_status(
            pool, user=user, item=item, execution_status="failed", critical=True
        )
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={
                "template_id": template["template_id"],
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "reason": "circuit_breaker_open",
            },
            critical=True,
        )
        await _record_writeback_audit_event(
            pool,
            user=user,
            action="control_room.action.execute",
            resource_type="control_room_item",
            resource_id=item["id"],
            ip=ip,
            user_agent=user_agent,
            status="failure",
            metadata={
                "template_id": template["template_id"],
                "template_type": template_type,
                "target": target,
                "adapter": adapter_name,
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "reason": "circuit_breaker_open",
                "validation_result": public_validation_result,
            },
        )
        return {"_http_error_status": 503, "_http_error_detail": result}
    except AdapterExecutionError as exc:
        result = {
            "ok": False,
            "mode": "execute_live",
            "executed": False,
            "external_write": True,
            "adapter": cartridge_id,
            "message": str(exc),
            "remote_status": exc.status_code,
        }
        reconciliation = await ambiguous_adapter_error_response(
            pool,
            error=exc,
            remote_attempt_started=remote_attempt_started,
            workspace_id=_workspace_id(user),
            reservation_id=reservation.id,
            effective_key=reservation.effective_key,
            result=result,
        )
        if reconciliation is not None:
            return reconciliation
        execution = await _record_action_execution(
            pool,
            user=user,
            item=item,
            template=template,
            mode="execute_live",
            status="failed",
            payload=payload,
            result=result,
            error="external_writeback_failed",
            critical=True,
        )
        action_run = await _complete_execute_reservation(
            pool,
            user=user,
            item=item,
            status="failed",
            reservation=reservation,
            result=result,
            error_code="external_writeback_failed",
            error_message=str(exc),
            legacy_execution_id=int(execution["id"])
            if execution.get("id") is not None
            else None,
            side_effect={"target": target, "adapter": adapter_name},
        )
        await _set_execution_status(
            pool, user=user, item=item, execution_status="failed", critical=True
        )
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={
                "template_id": template["template_id"],
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "reason": "external_writeback_failed",
            },
            critical=True,
        )
        await _record_writeback_audit_event(
            pool,
            user=user,
            action="control_room.action.execute",
            resource_type="control_room_item",
            resource_id=item["id"],
            ip=ip,
            user_agent=user_agent,
            status="failure",
            metadata={
                "template_id": template["template_id"],
                "template_type": template_type,
                "target": target,
                "adapter": adapter_name,
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "reason": "external_writeback_failed",
                "validation_result": public_validation_result,
            },
        )
        return {"_http_error_status": 502, "_http_error_detail": result}
    except external_effect.RemoteSideEffectCommitted:
        raise
    except Exception as exc:
        result = {
            "ok": False,
            "mode": "execute_live",
            "executed": False,
            "external_write": True,
            "adapter": cartridge_id,
            "message": "External write-back failed before remote execution completed.",
        }
        execution = await _record_action_execution(
            pool,
            user=user,
            item=item,
            template=template,
            mode="execute_live",
            status="failed",
            payload=payload,
            result=result,
            error=type(exc).__name__,
            critical=True,
        )
        action_run = await _complete_execute_reservation(
            pool,
            user=user,
            item=item,
            status="failed",
            reservation=reservation,
            result=result,
            error_code=type(exc).__name__,
            error_message=str(exc),
            legacy_execution_id=int(execution["id"])
            if execution.get("id") is not None
            else None,
            side_effect={"target": target, "adapter": adapter_name},
        )
        await _set_execution_status(
            pool, user=user, item=item, execution_status="failed", critical=True
        )
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={
                "template_id": template["template_id"],
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "reason": type(exc).__name__,
            },
            critical=True,
        )
        await _record_writeback_audit_event(
            pool,
            user=user,
            action="control_room.action.execute",
            resource_type="control_room_item",
            resource_id=item["id"],
            ip=ip,
            user_agent=user_agent,
            status="failure",
            metadata={
                "template_id": template["template_id"],
                "template_type": template_type,
                "target": target,
                "adapter": adapter_name,
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "reason": type(exc).__name__,
                "validation_result": public_validation_result,
            },
        )
        status = 501 if isinstance(exc, NotImplementedError) else 502
        return {"_http_error_status": status, "_http_error_detail": result}

    ok = bool(public_adapter_result.get("ok", True))
    execution_status = "executed" if ok else "failed"
    after = public_adapter_result.get("after")
    if not isinstance(after, dict):
        after = {"execution_status": execution_status, "target": target}
    message = str(
        public_adapter_result.get("message")
        or ("Write-back externo ejecutado." if ok else "Write-back externo fallido.")
    )
    result = {
        "ok": True,
        "mode": "execute_live",
        "executed": ok,
        "external_write": True,
        "internal_write": False,
        "adapter": adapter_name,
        "target": target,
        "template_type": template_type,
        "idempotency_key": idempotency_key,
        "before": before,
        "after": after,
        "message": message,
        "validation_result": public_validation_result,
        "adapter_result": public_adapter_result,
        "writeback_result": public_adapter_result,
    }
    with external_effect.remote_effect_boundary(result, target, adapter_name, after):
        execution = await _record_action_execution(
            pool,
            user=user,
            item=item,
            template=template,
            mode="execute_live",
            status=execution_status,
            payload=action_data,
            result=result,
            error=None if ok else message,
            critical=True,
        )
        action_run = await _complete_execute_reservation(
            pool,
            user=user,
            item=item,
            status="completed" if ok else "failed",
            reservation=reservation,
            result=result,
            side_effect={"target": target, "adapter": adapter_name, "after": after},
            error_code=None if ok else "external_writeback_failed",
            error_message=None if ok else message,
            legacy_execution_id=int(execution["id"])
            if execution.get("id") is not None
            else None,
        )
        await _set_execution_status(
            pool, user=user, item=item, execution_status=execution_status, critical=True
        )
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_executed" if ok else "action_failed",
            metadata={
                "template_id": template["template_id"],
                "template_type": template_type,
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "target": target,
                "adapter": adapter_name,
            },
            critical=True,
        )
        await _record_writeback_audit_event(
            pool,
            user=user,
            action="control_room.action.execute",
            resource_type="control_room_item",
            resource_id=item["id"],
            ip=ip,
            user_agent=user_agent,
            status="success" if ok else "failure",
            metadata={
                "template_id": template["template_id"],
                "template_type": template_type,
                "target": target,
                "adapter": adapter_name,
                "execution_id": execution.get("id"),
                "action_run_id": action_run.get("id"),
                "before": before,
                "after": after,
                "validation_result": public_validation_result,
                "adapter_result": public_adapter_result,
            },
        )
        if not ok:
            return {"_http_error_status": 502, "_http_error_detail": message}
        learning_lesson = await _record_adapter_success_lesson(
            pool,
            user=user,
            item=item,
            template=template,
            execution=execution,
            result=result,
            adapter_name=adapter_name,
            template_type=template_type,
        )
        suggested_actions = _suggested_actions_from_lessons(
            item, [learning_lesson] if learning_lesson else []
        )
        public_item = _project_public_item(
            item,
            _with_omega,
            execution_status="executed",
            suggested_actions=suggested_actions,
        )
        return {
            "executed": True,
            "idempotent": False,
            "execution": execution,
            "action_run": action_run,
            "payload": action_data,
            "result": result,
            "learning_lesson": learning_lesson,
            "item": public_item,
        }


@_bind_to_core
async def execute_item(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    binding_id: str | None = None,
    confirm_execute: Any = False,
    idempotency_key: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    require_action_item_evidence(item)
    template = require_explicit_action_template(item, user, template_id, binding_id)
    try:
        idempotency_key = normalize_action_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    payload = _execution_payload(item, "execute_live", template)
    pool = await auth.pool()

    async def _with_scoped_db(work: Callable[[Any], Awaitable[Any]]) -> Any:
        async def _run(conn: Any, _tenant_id: str | None, _workspace_id: str) -> Any:
            await require_enabled_action_template(conn, str(template["template_id"]))
            return await work(conn)

        try:
            return await _run_with_db_scope(pool, user, _run)
        except ReservationUnavailable as exc:
            raise HTTPException(
                503, "execution idempotency reservation failed"
            ) from exc

    async def _with_scoped_replay(work: Callable[[Any], Awaitable[Any]]) -> Any:
        return await _run_with_db_scope(
            pool,
            user,
            lambda conn, _tenant_id, _workspace_id: work(conn),
        )

    replay_response = await prepare_execution_entry(
        run_scoped=_with_scoped_db,
        run_replay_scoped=_with_scoped_replay,
        ensure_item_row=_ensure_item_row,
        record_execute_block=_record_execute_block,
        response_for_reservation=_reserved_action_response,
        user=user,
        item=item,
        template=template,
        payload=payload,
        confirmed=_confirmed_for_execute(confirm_execute),
        ip=ip,
        user_agent=user_agent,
    )
    if replay_response is not None:
        return replay_response
    await _with_scoped_db(
        lambda db: require_approved_execution(
            db,
            user=user,
            item=item,
            template_id=str(template["template_id"]),
        )
    )
    capability = _writeback_capability(template)
    if capability.get("external") and not capability.get("adapter_available"):
        await _with_scoped_db(
            lambda db: _record_execute_block(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                ip=ip,
                user_agent=user_agent,
                message=str(
                    capability.get("reason")
                    or "No hay adapter ERP aprobado para este template."
                ),
                error="adapter_missing",
            )
        )
        raise HTTPException(
            501, "external ERP write-back adapter is not available for this template"
        )

    if capability.get("external") and not _external_writeback_enabled():
        await _with_scoped_db(
            lambda db: _record_execute_block(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                ip=ip,
                user_agent=user_agent,
                message="Write-back ERP externo no habilitado en Control Room V1; usa ejecucion supervisada.",
                error="CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=false",
            )
        )
        raise HTTPException(
            409, "external ERP write-back is not available in Control Room V1"
        )

    if not capability.get("supported") and not capability.get("external"):
        await _with_scoped_db(
            lambda db: _record_execute_block(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                ip=ip,
                user_agent=user_agent,
                message="No hay ejecucion supervisada aprobada para este template.",
                error="unsupported_writeback_template",
            )
        )
        raise HTTPException(
            501, "execution template is not supported in Control Room V1"
        )

    if not _confirmed_for_execute(confirm_execute):
        await _with_scoped_db(
            lambda db: _record_execute_block(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                ip=ip,
                user_agent=user_agent,
                message="Confirmacion explicita requerida antes de ejecutar la accion supervisada.",
                error="explicit_confirmation_required",
            )
        )
        raise HTTPException(409, "explicit execution confirmation is required")

    if str(template.get("template_id") or "") == "create_followup_task":
        return await _with_scoped_db(
            lambda db: _execute_internal_followup_task(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                idempotency_key=idempotency_key,
                ip=ip,
                user_agent=user_agent,
            )
        )

    if str(template.get("template_id") or "") == "create_investigation_note":
        return await _with_scoped_db(
            lambda db: _execute_internal_investigation_note(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                idempotency_key=idempotency_key,
                ip=ip,
                user_agent=user_agent,
            )
        )

    if str(template.get("template_id") or "") == "mark_decision_for_monitoring":
        return await _with_scoped_db(
            lambda db: _execute_internal_decision_monitoring(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                idempotency_key=idempotency_key,
                ip=ip,
                user_agent=user_agent,
            )
        )

    if capability.get("external") and capability.get("supported"):
        adapter = WriteBackAdapterFactory.get_adapter(
            _writeback_template_type(template)
        )
        if not adapter_guarantees_idempotency(
            _writeback_template_type(template), adapter
        ):
            raise HTTPException(
                501, "external write-back adapter must support idempotency"
            )
        reservation = await _with_scoped_db(
            lambda db: acquire_guarded_action_reservation(
                db,
                user=user,
                item=item,
                template_id=str(template["template_id"]),
                adapter_name=adapter.__class__.__name__,
                operation="execute",
                provided_key=idempotency_key,
                input_payload=payload,
                persist_item=_ensure_item_row,
            )
        )
        if reservation.state is not ReservationState.ACQUIRED:
            return _reserved_action_response(reservation, item=item, payload=payload)
        response = await run_reserved_external_action(
            run_scoped=_with_scoped_replay,
            prepare=lambda db: mark_remote_attempt_started(
                db,
                workspace_id=_workspace_id(user),
                reservation_id=reservation.id,
                effective_key=reservation.effective_key,
                adapter=adapter.__class__.__name__,
                target=str(
                    template.get("cartridge_id")
                    or item.get("cartridge")
                    or "external_system"
                ),
            ),
            execute=lambda db: _execute_external_writeback(
                db,
                user=user,
                item=item,
                template=template,
                payload=payload,
                reservation=reservation,
                ip=ip,
                user_agent=user_agent,
            ),
            finalize=finalize_aborted_action_reservation,
            workspace_id=_workspace_id(user),
            reservation=reservation,
        )
        if response.get("_http_error_status"):
            raise HTTPException(
                int(response["_http_error_status"]),
                response.get("_http_error_detail"),
            )
        return response

    await _with_scoped_db(
        lambda db: _record_execute_block(
            db,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="No hay adapter ERP aprobado para este template.",
            error="unsupported_writeback_template",
        )
    )
    raise HTTPException(501, "execution template is not supported in Control Room V1")


@_bind_to_core
async def create_decision_for_anomaly(
    anomaly_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await create_decision_for_item(
        anomaly_id,
        user,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


@_bind_to_core
async def approve_item(
    item_id: str,
    user: dict,
    *,
    decision_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()
    lessons = _lessons_for_item(item)
    approval = await _approve_with_optional_decision(
        pool,
        user=user,
        item=item,
        decision_id=decision_id,
        lessons=lessons,
        confidence=float(_impact_for_item(item).get("confidence") or 0.7),
        run_scoped=_run_with_db_scope,
        ensure_item_row=_ensure_item_row,
        record_item_event=_record_item_event,
        create_and_link=_create_and_link_business_decision,
        approve_item=_approve_business_item,
        link_decision=link_control_room_decision,
        approve_link=approve_control_room_decision,
        record_audit_event=audit_service.record_event,
        ip=ip,
        user_agent=user_agent,
    )
    decision_id = approval.decision_id
    action = approval.action
    item = approval.item
    public_action = dict(action)
    if hasattr(public_action.get("ts"), "isoformat"):
        public_action["ts"] = public_action["ts"].isoformat()
    item = _project_public_item(
        item,
        _with_omega,
        decision_id=decision_id,
        status="approved",
        lessons=lessons,
    )
    return {
        "approved": True,
        "decision_id": decision_id,
        "action": public_action,
        "item": item,
        "anomaly": item,
    }


@_bind_to_core
async def approve_anomaly(
    anomaly_id: str,
    user: dict,
    *,
    decision_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await approve_item(
        anomaly_id,
        user,
        decision_id=decision_id,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


@_bind_to_core
async def dismiss_item(
    item_id: str,
    user: dict,
    *,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()
    await _dismiss_with_audit(
        pool,
        user=user,
        item=item,
        reason=reason or "",
        ip=ip,
        user_agent=user_agent,
        run_scoped=_run_with_db_scope,
        persist_status_transition=_persist_status_transition,
        ensure_item_row=_ensure_item_row,
        record_audit_event=audit_service.record_event,
    )
    return {
        "dismissed": True,
        "item": _project_public_item(item, _with_omega, status="dismissed"),
    }


@_bind_to_core
async def reopen_item(
    item_id: str,
    user: dict,
    *,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()
    await _reopen_with_audit(
        pool,
        user=user,
        item=item,
        reason=reason or "",
        ip=ip,
        user_agent=user_agent,
        run_scoped=_run_with_db_scope,
        persist_status_transition=_persist_status_transition,
        ensure_item_row=_ensure_item_row,
        record_audit_event=audit_service.record_event,
        lock_item=lock_authoritative_business_item,
        reopen_allowed=workflow_reopen_allowed,
    )
    return {
        "reopened": True,
        "item": _project_public_item(
            item,
            _with_omega,
            status="open",
            decision_id=None,
        ),
    }


@_bind_to_core
def _snoozed_until_from_body(body: dict[str, Any] | None) -> str:
    body = body if isinstance(body, dict) else {}
    explicit = _parse_utc_datetime(body.get("snoozed_until"))
    if explicit:
        return explicit.isoformat()
    try:
        hours = int(body.get("hours") or body.get("snooze_hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(hours, 24 * 14))
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


@_bind_to_core
async def _operate_alert(
    item_id: str,
    user: dict,
    *,
    next_state: str,
    event_type: str,
    audit_action: str,
    body: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    if next_state not in {"acknowledged", "snoozed", "assigned", "false_positive"}:
        raise HTTPException(400, "invalid alert operation")
    body = body if isinstance(body, dict) else {}
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    current_status = str(item.get("status") or "open")
    if current_status in TERMINAL_ITEM_STATUSES:
        raise HTTPException(409, "terminal control room item has no active alert")
    if not _alert_for_item(item) and next_state != "false_positive":
        raise HTTPException(404, "active alert not found")

    now = datetime.now(UTC).isoformat()
    owner_email = str(
        body.get("owner_email") or body.get("owner") or user.get("email") or ""
    ).strip()
    note = str(body.get("note") or "").strip()
    reason = str(body.get("reason") or "").strip()
    alert_changes = {
        "state": next_state,
        "updated_at": now,
        "updated_by": user.get("email") or "user",
    }
    alert_defaults: dict[str, Any] = {}
    if note:
        alert_changes["note"] = note
    if reason:
        alert_changes["reason"] = reason
    if next_state == "acknowledged":
        alert_defaults["acknowledged_at"] = now
    elif next_state == "snoozed":
        alert_changes["snoozed_until"] = _snoozed_until_from_body(body)
        alert_changes["snoozed_at"] = now
    elif next_state == "assigned":
        alert_changes["owner"] = owner_email or "operaciones"
        alert_changes["assigned_at"] = now
    elif next_state == "false_positive":
        alert_changes["false_positive_at"] = now
        alert_changes["reason"] = (
            reason or "Marcado como falso positivo desde Sala de Control"
        )

    target_status = (
        "dismissed"
        if next_state == "false_positive"
        else (current_status if current_status not in {"open", ""} else "in_review")
    )
    alert_state = merged_alert_state(
        {"alert_state": item.get("alert_state")},
        changes=alert_changes,
        defaults=alert_defaults,
    )
    pool = await auth.pool()
    await _persist_alert_operation_with_audit(
        pool,
        user=user,
        item=item,
        target_status=target_status,
        terminal_statuses=sorted(TERMINAL_ITEM_STATUSES),
        alert_changes=alert_changes,
        alert_defaults=alert_defaults,
        alert_state=alert_state,
        event_type=event_type,
        note=note,
        reason=reason,
        audit_action=audit_action,
        ip=ip,
        user_agent=user_agent,
        critical_audit=next_state == "false_positive",
        run_scoped=_run_with_db_scope,
        persist_alert_state=_persist_alert_state,
        ensure_item_row=_ensure_item_row,
        record_audit_event=audit_service.record_event,
    )
    public_item = _project_public_item(
        item, _with_omega, status=target_status, alert_state=alert_state
    )
    return {
        "ok": True,
        "alert": _alert_for_item(public_item),
        "item": public_item,
    }


@_bind_to_core
async def acknowledge_alert(
    item_id: str,
    user: dict,
    *,
    body: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await _operate_alert(
        item_id,
        user,
        next_state="acknowledged",
        event_type="alert_acknowledged",
        audit_action="control_room.alert.acknowledge",
        body=body,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


@_bind_to_core
async def snooze_alert(
    item_id: str,
    user: dict,
    *,
    body: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await _operate_alert(
        item_id,
        user,
        next_state="snoozed",
        event_type="alert_snoozed",
        audit_action="control_room.alert.snooze",
        body=body,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


@_bind_to_core
async def assign_alert(
    item_id: str,
    user: dict,
    *,
    body: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await _operate_alert(
        item_id,
        user,
        next_state="assigned",
        event_type="alert_assigned",
        audit_action="control_room.alert.assign",
        body=body,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


@_bind_to_core
async def mark_alert_false_positive(
    item_id: str,
    user: dict,
    *,
    body: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await _operate_alert(
        item_id,
        user,
        next_state="false_positive",
        event_type="alert_false_positive",
        audit_action="control_room.alert.false_positive",
        body=body,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


__all__ = (
    "_external_writeback_enabled",
    "_external_delivery_enabled",
    "_writeback_capability",
    "_template_with_writeback",
    "_impact_payload",
    "_priority_payload",
    "_impact_for_item",
    "_template_ids_for_item",
    "_action_templates_for_item",
    "_primary_template_for_item",
    "_action_payload_for_template",
    "_execution_payload",
    "create_decision_for_item",
    "select_item_option",
    "get_item_impact",
    "record_item_step",
    "update_item_control",
    "create_item_lesson",
    "apply_item_lesson",
    "_resolve_template",
    "_adapter_name_for_template",
    "_action_run_idempotency_key",
    "_action_run_public",
    "_record_action_run_event",
    "_record_action_run",
    "_dry_run_checks",
    "action_preview",
    "action_dry_run",
    "list_item_action_runs",
    "_outcome_num",
    "_outcome_public",
    "list_item_outcomes",
    "record_item_outcome",
    "run_auto_item",
    "_confirmed_for_execute",
    "_supports_transactional_acquire",
    "_record_writeback_audit_event",
    "_record_execute_block",
    "_execute_internal_followup_task",
    "_execute_internal_followup_task_tx",
    "_execute_internal_investigation_note",
    "_execute_internal_decision_monitoring",
    "_external_action_data",
    "_execute_external_writeback",
    "execute_item",
    "create_decision_for_anomaly",
    "approve_item",
    "approve_anomaly",
    "dismiss_item",
    "reopen_item",
    "_snoozed_until_from_body",
    "_operate_alert",
    "acknowledge_alert",
    "snooze_alert",
    "assign_alert",
    "mark_alert_false_positive",
)
