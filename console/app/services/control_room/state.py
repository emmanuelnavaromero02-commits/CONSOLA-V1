from __future__ import annotations

import types

from app.services.control_room import core as _core
from app.services.control_room.business_alert_projection import build_business_alert
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
    resolve_command_item,
)
from app.services.control_room.business_omega_projection import (
    OmegaProjectionRuntime,
    build_omega_projection,
)
from app.services.control_room.business_state_rows import (
    diagnostic_metadata as _business_diagnostic_metadata,
)


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
_core.__dict__.setdefault("business_diagnostic_metadata", _business_diagnostic_metadata)
_core.__dict__.setdefault("build_business_alert", build_business_alert)
_core.__dict__.setdefault("build_omega_projection", build_omega_projection)
_core.__dict__.setdefault("load_persisted_command_item", load_persisted_command_item)
_core.__dict__.setdefault("OmegaProjectionRuntime", OmegaProjectionRuntime)
_core.__dict__.setdefault("resolve_command_item", resolve_command_item)


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


# Persistent state, thresholds, lessons, and alert overlays.
@_bind_to_core
def _row_to_public(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


@_bind_to_core
def _actor_id(value: Any) -> int | None:
    return business_actor_id(value)


@_bind_to_core
def _can_read_workspace_wide(user: dict | None) -> bool:
    return business_can_read_workspace_wide(user)


@_bind_to_core
def _threshold_to_public(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    for key in ("warning_value", "critical_value"):
        data[key] = _num(data.get(key))
    data["currency"] = str(data.get("currency") or "USD").upper()
    data["enabled"] = bool(data.get("enabled", True))
    data["metadata"] = _details(data.get("metadata"))
    return data


@_bind_to_core
def _threshold_insights(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    active = [row for row in items if row.get("enabled", True)]
    disabled = [row for row in items if not row.get("enabled", True)]
    by_cartridge: dict[str, int] = {}
    by_anomaly_type: dict[str, int] = {}
    for row in active:
        cartridge_id = str(row.get("cartridge_id") or "").strip()
        anomaly_type = str(row.get("anomaly_type") or "").strip()
        if cartridge_id:
            by_cartridge[cartridge_id] = by_cartridge.get(cartridge_id, 0) + 1
        if anomaly_type:
            by_anomaly_type[anomaly_type] = by_anomaly_type.get(anomaly_type, 0) + 1
    return {
        "total": len(items),
        "active": len(active),
        "disabled": len(disabled),
        "by_cartridge": by_cartridge,
        "by_anomaly_type": by_anomaly_type,
        "recent": sorted(
            items,
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
            reverse=True,
        )[:5],
    }


@_bind_to_core
async def _load_threshold_rows(
    user: dict | None, *, enabled_only: bool = True
) -> list[dict[str, Any]]:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    where = ["workspace_id = $1"]
    if enabled_only:
        where.append("enabled = TRUE")
    try:

        async def _load(
            conn: Any, _tenant_id: str | None, _workspace_id: str
        ) -> list[Any]:
            return await conn.fetch(
                f"""
                SELECT id, cartridge_id, anomaly_type, metric, warning_value,
                       critical_value, currency, enabled, metadata, created_at, updated_at
                  FROM control_room_thresholds
                 WHERE {' AND '.join(where)}
                 ORDER BY cartridge_id, anomaly_type, metric
                """,
                workspace_id,
            )

        rows = await _run_with_db_scope(pool, user or {}, _load)
        return [_threshold_to_public(row) for row in rows]
    except Exception:
        return []


@_bind_to_core
def _lesson_to_public(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    data["metadata"] = _details(data.get("metadata"))
    data["confidence"] = _num(data.get("confidence")) or 0.0
    return data


@_bind_to_core
async def _load_lesson_rows(
    user: dict | None,
    *,
    cartridge_id: str | None = None,
    anomaly_type: str | None = None,
    item_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    params: list[Any] = [workspace_id]
    where = ["workspace_id = $1"]
    if cartridge_id:
        params.append(cartridge_id)
        where.append(f"cartridge_id = ${len(params)}")
    if anomaly_type:
        params.append(anomaly_type)
        where.append(f"anomaly_type = ${len(params)}")
    if item_id:
        params.append(item_id)
        where.append(f"item_id = ${len(params)}")
    params.append(max(1, min(int(limit or 100), 500)))
    try:

        async def _load(
            conn: Any, _tenant_id: str | None, _workspace_id: str
        ) -> list[Any]:
            return await conn.fetch(
                f"""
                SELECT id, item_id, cartridge_id, anomaly_type, rule,
                       source_decision_id, confidence, metadata, created_at
                  FROM control_room_lessons
                 WHERE {' AND '.join(where)}
                 ORDER BY created_at DESC
                 LIMIT ${len(params)}
                """,
                *params,
            )

        rows = await _run_with_db_scope(pool, user or {}, _load)
        return [_lesson_to_public(row) for row in rows]
    except Exception:
        return []


@_bind_to_core
def _lesson_insights(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_cartridge: dict[str, int] = {}
    patterns: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        cartridge_id = str(row.get("cartridge_id") or "unknown")
        anomaly_type = str(row.get("anomaly_type") or "control_room_item")
        by_cartridge[cartridge_id] = by_cartridge.get(cartridge_id, 0) + 1
        key = (cartridge_id, anomaly_type)
        pattern = patterns.setdefault(
            key,
            {
                "cartridge_id": cartridge_id,
                "anomaly_type": anomaly_type,
                "count": 0,
                "confidence_total": 0.0,
                "latest_rule": "",
                "last_seen_at": row.get("created_at"),
            },
        )
        pattern["count"] += 1
        pattern["confidence_total"] += float(row.get("confidence") or 0.0)
        if not pattern.get("latest_rule"):
            pattern["latest_rule"] = row.get("rule") or ""
            pattern["last_seen_at"] = row.get("created_at")

    top_patterns = []
    for pattern in patterns.values():
        count = int(pattern["count"] or 0)
        confidence = float(pattern.pop("confidence_total", 0.0))
        pattern["avg_confidence"] = round(confidence / count, 2) if count else 0.0
        top_patterns.append(pattern)
    top_patterns.sort(
        key=lambda item: (
            -int(item.get("count") or 0),
            str(item.get("cartridge_id") or ""),
        )
    )
    return {
        "total": len(rows),
        "recent": rows[:5],
        "by_cartridge": by_cartridge,
        "top_patterns": top_patterns[:5],
    }


@_bind_to_core
def _suggested_action_from_lesson(lesson: dict[str, Any]) -> dict[str, Any] | None:
    metadata = _details(lesson.get("metadata"))
    if not metadata.get("autonomous_learning"):
        return None
    action = metadata.get("suggested_action")
    if not isinstance(action, dict):
        return None
    template_id = str(action.get("template_id") or "").strip()
    if not template_id:
        return None
    return {
        "template_id": template_id,
        "template_type": action.get("template_type"),
        "label": action.get("label") or template_id.replace("_", " ").title(),
        "action_kind": action.get("action_kind"),
        "target": action.get("target") or lesson.get("cartridge_id"),
        "adapter": action.get("adapter"),
        "confidence": lesson.get("confidence"),
        "lesson_id": lesson.get("id"),
        "source_item_id": lesson.get("item_id"),
        "source_decision_id": lesson.get("source_decision_id"),
        "reason": lesson.get("rule"),
    }


@_bind_to_core
def _suggested_actions_from_lessons(
    item: dict[str, Any],
    lesson_rows: Iterable[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lesson in lesson_rows:
        if not _lesson_matches_item(lesson, item):
            continue
        suggestion = _suggested_action_from_lesson(lesson)
        if not suggestion:
            continue
        key = str(suggestion.get("template_id") or suggestion.get("lesson_id"))
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
    suggestions.sort(key=lambda row: float(row.get("confidence") or 0.0), reverse=True)
    return suggestions[: max(1, min(int(limit or 5), 20))]


@_bind_to_core
def _attach_lessons_to_items(
    items: list[dict[str, Any]], lesson_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not items:
        return []
    business_ids = eligible_item_ids(items)
    if not lesson_rows:
        return [
            _with_omega(
                {
                    **item,
                    "related_lessons": item.get("related_lessons") or [],
                    "lesson_count": 0,
                    "suggested_actions": item.get("suggested_actions") or [],
                },
                eligible_parent_ids=business_ids,
            )
            for item in items
        ]

    enriched: list[dict[str, Any]] = []
    for item in items:
        related = [
            row
            for row in lesson_rows
            if row.get("item_id") == item.get("id")
            or (
                row.get("cartridge_id") == item.get("cartridge")
                and row.get("anomaly_type") == item.get("anomaly_type")
            )
        ]
        rules: list[str] = []
        for row in related:
            rule = str(row.get("rule") or "").strip()
            if rule and rule not in rules:
                rules.append(rule)
        enriched.append(
            _with_omega(
                {
                    **item,
                    "related_lessons": related[:5],
                    "lesson_count": len(related),
                    "learned_rules": rules[:5],
                    "suggested_actions": _suggested_actions_from_lessons(item, related),
                },
                eligible_parent_ids=business_ids,
            )
        )
    enriched.sort(key=_status_sort_key)
    return enriched


@_bind_to_core
def _threshold_map(rows: Iterable[dict[str, Any]]) -> ThresholdMap:
    thresholds: ThresholdMap = {}
    for row in rows:
        if not row.get("enabled", True):
            continue
        cartridge_id = str(row.get("cartridge_id") or "").strip()
        anomaly_type = str(row.get("anomaly_type") or "").strip()
        metric = str(row.get("metric") or "").strip()
        if cartridge_id and anomaly_type and metric:
            thresholds[(cartridge_id, anomaly_type, metric)] = row
    return thresholds


@_bind_to_core
def _threshold_rule(
    thresholds: ThresholdMap | None,
    cartridge_id: str,
    anomaly_type: str,
    metric: str,
) -> dict[str, Any] | None:
    if not thresholds:
        return None
    return thresholds.get((cartridge_id, anomaly_type, metric))


@_bind_to_core
def _threshold_value(
    thresholds: ThresholdMap | None,
    cartridge_id: str,
    anomaly_type: str,
    metric: str,
    field: str,
    default: float,
) -> float:
    rule = _threshold_rule(thresholds, cartridge_id, anomaly_type, metric)
    if not rule:
        return default
    value = _num(rule.get(field))
    return default if value is None else value


@_bind_to_core
def _threshold_ref(
    thresholds: ThresholdMap | None,
    cartridge_id: str,
    anomaly_type: str,
    metric: str,
    *,
    warning_default: float,
    critical_default: float | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    rule = _threshold_rule(thresholds, cartridge_id, anomaly_type, metric)
    warning_value = _threshold_value(
        thresholds,
        cartridge_id,
        anomaly_type,
        metric,
        "warning_value",
        warning_default,
    )
    critical_value = (
        _threshold_value(
            thresholds,
            cartridge_id,
            anomaly_type,
            metric,
            "critical_value",
            critical_default,
        )
        if critical_default is not None
        else None
    )
    return {
        "cartridge_id": cartridge_id,
        "anomaly_type": anomaly_type,
        "metric": metric,
        "warning_value": warning_value,
        "critical_value": critical_value,
        "currency": str((rule or {}).get("currency") or currency).upper(),
        "source": "workspace" if rule else "default",
    }


@_bind_to_core
def _source_state_item(
    source: ControlRoomSource,
    status: str,
    error: str | None = None,
    data_readiness: str | None = None,
    readiness_reason: str | None = None,
    readiness_blockers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    readiness = data_readiness or ("ready" if status == "ok" else status)
    if status == "ok" and readiness == "ready":
        return None
    severity = (
        "high"
        if status in {"unavailable", "invalid_schema", "blocked", "no_permission"}
        or readiness == "stub"
        else "medium"
    )
    title_by_status = {
        "empty": "Fuente sin datos materializados",
        "missing": "Dataset requerido no registrado",
        "unavailable": "Fuente operativa no disponible",
        "invalid_schema": "Dataset con contrato invalido",
        "blocked": "Cartucho inactivo o bloqueado",
        "no_permission": "Cartucho sin permiso para este usuario",
        "partial": "Fuente parcial: no apta para operacion completa",
        "stub": "Fuente stub: no apta para decisiones operativas",
    }
    description_by_status = {
        "empty": f"{source.dataset} existe pero no tiene filas para el workspace activo.",
        "missing": f"{source.dataset} no esta disponible en el catalogo del workspace activo.",
        "unavailable": f"{source.dataset} no pudo consultarse desde Refinement.",
        "invalid_schema": f"{source.dataset} no cumple el contrato esperado por la Sala de Control.",
        "blocked": f"{source.module_label} esta instalado pero no esta activo para el workspace.",
        "no_permission": f"{source.module_label} no esta permitido para este usuario.",
        "partial": readiness_reason
        or f"{source.dataset} devuelve datos, pero su contrato aun es parcial.",
        "stub": readiness_reason
        or f"{source.dataset} conserva placeholders/TODO y no debe contarse como operativo.",
    }
    item = _base_item(
        source,
        {
            "severity": severity,
            "details": {
                "source_status": status,
                "data_readiness": readiness,
                "readiness_reason": readiness_reason or "",
                "readiness_blockers": list(readiness_blockers or ()),
                "error": error or "",
            },
        },
        f"source_{readiness}",
        source.dataset,
        source.dataset,
    )
    item.update(
        {
            "kind": "source_state",
            "title": title_by_status.get(
                readiness, title_by_status.get(status, "Fuente requiere atencion")
            ),
            "description": description_by_status.get(
                readiness,
                description_by_status.get(
                    status, f"{source.dataset} requiere revision."
                ),
            ),
            "recommendation": "Validar instalacion, credenciales, materializacion y scope tenant/workspace antes de operar con el cliente.",
            "root_cause": "La cadena de datos no esta lista para entregar senales de negocio confiables.",
            "impact": "El cartucho puede aparecer activo pero sin datos accionables en la sala.",
        }
    )
    return item


@_bind_to_core
def _control_state(item: dict[str, Any]) -> dict[str, Any]:
    raw = (
        item.get("control_state") if isinstance(item.get("control_state"), dict) else {}
    )
    normalized: dict[str, Any] = {}
    for control_id, value in raw.items():
        if not isinstance(value, dict):
            continue
        state = str(value.get("status") or value.get("state") or "").strip()
        if state not in CONTROL_ITEM_STATUSES:
            state = "open"
        normalized[str(control_id)] = {
            **value,
            "status": state,
            "st": CONTROL_ITEM_STATUS_LABELS[state],
        }
    return normalized


@_bind_to_core
def _default_control_items(
    item: dict[str, Any],
    *,
    status: str,
    decision_id: Any,
    approved: bool,
) -> list[dict[str, Any]]:
    module_owner = item.get("module") or item.get("cartridge") or "operaciones"
    base_days = 1 if approved else 3
    now = datetime.now(UTC)
    return [
        {
            "id": "refresh",
            "desc": "Confirmar que el siguiente refresh conserva o corrige la senal.",
            "owner": module_owner,
            "status": "closed" if status in TERMINAL_ITEM_STATUSES else "open",
            "st": "cerrado" if status in TERMINAL_ITEM_STATUSES else "abierto",
            "impact": item.get("impact") or "Riesgo operativo",
            "days": base_days,
            "due_at": (now + timedelta(days=base_days)).isoformat(),
        },
        {
            "id": "audit",
            "desc": "Mantener evidencia ligada a decision_actions y audit_events.",
            "owner": "omega",
            "status": "closed" if decision_id else "open",
            "st": "listo" if decision_id else "pendiente",
            "impact": "Trazabilidad",
            "days": 0 if decision_id else 2,
            "due_at": (now + timedelta(days=0 if decision_id else 2)).isoformat(),
        },
    ]


@_bind_to_core
def _control_items_for_item(
    item: dict[str, Any],
    *,
    status: str,
    decision_id: Any,
    approved: bool,
) -> list[dict[str, Any]]:
    control_state = _control_state(item)
    controls: list[dict[str, Any]] = []
    for control in _default_control_items(
        item, status=status, decision_id=decision_id, approved=approved
    ):
        override = control_state.get(str(control["id"]), {})
        next_status = str(override.get("status") or control.get("status") or "open")
        if next_status not in CONTROL_ITEM_STATUSES:
            next_status = "open"
        controls.append(
            {
                **control,
                **override,
                "id": str(control["id"]),
                "status": next_status,
                "st": CONTROL_ITEM_STATUS_LABELS.get(
                    next_status, str(control.get("st") or next_status)
                ),
                "owner": override.get("owner") or control.get("owner"),
                "due_at": override.get("due_at") or control.get("due_at"),
            }
        )
    return controls


@_bind_to_core
def _decision_intelligence_for_item(item: dict[str, Any]) -> dict[str, Any]:
    direct = item.get("decision_intelligence")
    if isinstance(direct, dict):
        return direct
    intelligence = (
        item.get("intelligence") if isinstance(item.get("intelligence"), dict) else {}
    )
    nested = intelligence.get("decision_intelligence")
    return nested if isinstance(nested, dict) else {}


@_bind_to_core
def _with_omega(
    item: dict[str, Any], *, eligible_parent_ids: set[str] | None = None
) -> dict[str, Any]:
    return build_omega_projection(
        item,
        eligible_parent_ids=eligible_parent_ids,
        runtime=OmegaProjectionRuntime(
            impact_builder=_impact_for_item,
            action_templates_builder=_action_templates_for_item,
            decision_intelligence_builder=_decision_intelligence_for_item,
            lessons_builder=_lessons_for_item,
            priority_builder=_priority_payload,
            alert_state_builder=_alert_state,
            control_state_builder=_control_state,
            control_items_builder=_control_items_for_item,
            external_writeback_enabled=_external_writeback_enabled,
            execution_statuses=EXECUTION_STATUSES,
            terminal_statuses=TERMINAL_ITEM_STATUSES,
            supported_writeback_templates=SUPPORTED_INTERNAL_WRITEBACK_TEMPLATES,
        ),
    )


@_bind_to_core
def _status_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str, str]:
    active_rank = 1 if item.get("status") in TERMINAL_ITEM_STATUSES else 0
    return (
        active_rank,
        -int(item.get("priority_score") or 0),
        -int(item.get("severity_weight") or 0),
        str(item.get("domain") or ""),
        str(item.get("title") or ""),
    )


@_bind_to_core
def _alert_type_for_item(item: dict[str, Any]) -> str:
    if str(item.get("threshold_state") or "default") in {"critical", "warning"}:
        return "threshold_breach"
    if int(item.get("lesson_count") or 0) > 0:
        return "learned_pattern"
    return (
        "critical_signal"
        if item.get("severity") in {"critical", "high"}
        else "watchlist"
    )


@_bind_to_core
def _parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@_bind_to_core
def _alert_state(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("alert_state") if isinstance(item.get("alert_state"), dict) else {}
    state = str(raw.get("state") or "open")
    if state not in {"open", "acknowledged", "snoozed", "assigned", "false_positive"}:
        state = "open"
    snoozed_until = raw.get("snoozed_until")
    if state == "snoozed":
        parsed = _parse_utc_datetime(snoozed_until)
        if parsed and parsed <= datetime.now(UTC):
            state = "open"
    return {
        **raw,
        "state": state,
    }


@_bind_to_core
def _metadata_for_item(item: dict[str, Any], impact: dict[str, Any]) -> dict[str, Any]:
    return business_item_metadata(
        item,
        impact,
        decision_intelligence=_decision_intelligence_for_item,
    )


@_bind_to_core
def _alert_message(item: dict[str, Any], alert_type: str) -> str:
    if alert_type == "threshold_breach":
        thresholds = item.get("thresholds_applied") or []
        if thresholds:
            names = ", ".join(
                f"{row.get('anomaly_type')}/{row.get('metric')}"
                for row in thresholds[:2]
            )
            return f"Umbral operativo excedido: {names}."
    if alert_type == "learned_pattern":
        return f"Patron repetido con {int(item.get('lesson_count') or 0)} lecciones previas."
    return str(
        item.get("description")
        or item.get("title")
        or "Senal operativa requiere revision."
    )


@_bind_to_core
def _alert_for_item(
    item: dict[str, Any], *, eligible_parent_ids: set[str] | None = None
) -> dict[str, Any] | None:
    return build_business_alert(
        item,
        eligible_parent_ids=eligible_parent_ids,
        terminal_statuses=TERMINAL_ITEM_STATUSES,
        severity_weights=ALERT_SEVERITY_WEIGHT,
        alert_state_builder=_alert_state,
        priority_builder=_priority_payload,
        alert_type_builder=_alert_type_for_item,
        alert_message_builder=_alert_message,
        external_delivery_enabled=_external_delivery_enabled,
    )


@_bind_to_core
def _alert_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    business_ids = eligible_item_ids(items)
    alerts = [
        alert
        for item in items
        if (alert := _alert_for_item(item, eligible_parent_ids=business_ids))
    ]
    alerts.sort(
        key=lambda alert: (
            -int(alert.get("priority_score") or 0),
            -ALERT_SEVERITY_WEIGHT.get(str(alert.get("severity") or "medium"), 2),
            str(alert.get("domain") or ""),
            str(alert.get("title") or ""),
        )
    )
    by_type: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for alert in alerts:
        alert_type = str(alert.get("alert_type") or "watchlist")
        domain = str(alert.get("domain") or "unknown")
        severity = str(alert.get("severity") or "medium")
        status = str(alert.get("status") or "open")
        by_type[alert_type] = by_type.get(alert_type, 0) + 1
        by_domain[domain] = by_domain.get(domain, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "alerts": alerts,
        "summary": {
            "total": len(alerts),
            "critical": by_severity.get("critical", 0),
            "high": by_severity.get("high", 0),
            "medium": by_severity.get("medium", 0),
            "low": by_severity.get("low", 0),
            "push_ready": sum(1 for alert in alerts if alert.get("push_ready")),
            "by_type": by_type,
            "by_domain": by_domain,
            "by_severity": by_severity,
            "by_status": by_status,
            "top": alerts[:5],
        },
    }


@_bind_to_core
def _diagnostic_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return business_diagnostic_metadata(item)


@_bind_to_core
async def _persist_item_state(items: list[dict[str, Any]], user: dict | None) -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    await persist_refresh_items(
        items,
        user=user or {},
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=_actor_id((user or {}).get("id")),
        workspace_wide=_can_read_workspace_wide(user),
        pool_factory=auth.pool,
        run_scoped=_run_with_db_scope,
        impact_builder=_impact_for_item,
        metadata_builder=_metadata_for_item,
        diagnostic_builder=_diagnostic_metadata,
    )


@_bind_to_core
async def _overlay_item_state(
    items: list[dict[str, Any]], user: dict | None
) -> list[dict[str, Any]]:
    if not items:
        return []
    tenant_id, workspace_id = _workspace_scope(user)
    item_ids = [item["id"] for item in items]
    owner_id = (
        None if _can_read_workspace_wide(user) else _actor_id((user or {}).get("id"))
    )

    async def _load(
        conn: Any, _tenant_id: str | None, _workspace_id: str
    ) -> dict[str, dict[str, Any]]:
        if not _can_read_workspace_wide(user) and owner_id is None:
            return {}
        return await load_overlay_state(
            conn,
            workspace_id=workspace_id,
            item_ids=item_ids,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    try:
        pool = await auth.pool()
        state_by_id = await _run_with_db_scope(pool, user or {}, _load)
    except Exception:
        state_by_id = {}

    return overlay_business_state(
        items,
        state_by_id,
        item_statuses=ITEM_STATUSES,
        projector=_with_omega,
        sort_key=_status_sort_key,
    )


@_bind_to_core
async def _persisted_item_for_mutation(
    item_id: str, user: dict
) -> dict[str, Any] | None:
    return await load_persisted_command_item(
        item_id,
        user,
        pool_factory=auth.pool,
        run_scoped=_run_with_db_scope,
        item_statuses=ITEM_STATUSES,
        severity_weights=SEVERITY_WEIGHT,
    )


@_bind_to_core
async def _item_for_mutation(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await resolve_command_item(
        item_id,
        user,
        load_persisted=lambda target: _persisted_item_for_mutation(target, user),
        collect_items=lambda: _collect_items(
            user,
            fetcher=fetcher,
            include_source_state_items=True,
        ),
        normalize_lineage=_persisted_intelligence_payload,
        pool_factory=auth.pool,
        run_scoped=_run_with_db_scope,
        projector=_with_omega,
    )


@_bind_to_core
async def _item_for_read(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await _item_for_mutation(item_id, user, fetcher=fetcher)


@_bind_to_core
def _event_to_activity(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    event_type = str(data.get("event_type") or "event")
    metadata = _details(data.get("metadata"))
    return {
        "id": f"event:{data.get('id')}",
        "kind": "event",
        "type": event_type,
        "label": ACTIVITY_LABELS.get(event_type, event_type.replace("_", " ").title()),
        "status": event_type,
        "actor": data.get("actor_email") or "sistema",
        "at": data.get("created_at"),
        "metadata": metadata,
    }


@_bind_to_core
def _execution_to_activity(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    mode = str(data.get("mode") or "execution")
    status = str(data.get("status") or "")
    result = _details(data.get("result"))
    if mode == "execute_live" and result.get("external_write"):
        execute_label = "Write-back ERP"
    elif mode == "execute_live":
        execute_label = "Ejecucion supervisada"
    else:
        execute_label = "Ejecucion"
    label_by_mode = {
        "preview": "Preview generado",
        "dry_run": "Dry-run validado",
        "execute_live": execute_label,
    }
    return {
        "id": f"execution:{data.get('id')}",
        "kind": "execution",
        "type": mode,
        "label": label_by_mode.get(mode, mode.replace("_", " ").title()),
        "status": status,
        "actor": data.get("actor_email") or "sistema",
        "at": data.get("created_at"),
        "metadata": {
            "template_id": data.get("template_id"),
            "completed_at": data.get("completed_at"),
        },
        "payload": _details(data.get("payload")),
        "result": _details(data.get("result")),
        "error": data.get("error"),
    }


@_bind_to_core
def _decision_action_to_activity(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    label = str(data.get("action_text") or "Accion de decision")
    return {
        "id": f"decision_action:{data.get('id')}",
        "kind": "decision_action",
        "type": "decision_action",
        "label": label,
        "status": "recorded",
        "actor": data.get("actor") or "user",
        "at": data.get("ts"),
        "metadata": {
            "decision_id": data.get("decision_id"),
            "note": data.get("note"),
        },
    }


@_bind_to_core
def _action_run_to_activity(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    status = str(data.get("status") or "recorded")
    label_by_status = {
        "preview_generated": "Preview persistido",
        "dry_run_completed": "Dry-run persistente validado",
        "dry_run_failed": "Dry-run persistente fallido",
        "blocked": "Ejecucion bloqueada",
        "completed": "Action run completado",
        "failed": "Action run fallido",
    }
    return {
        "id": f"action_run:{data.get('id')}",
        "kind": "action_run",
        "type": data.get("mode") or data.get("action_type"),
        "label": label_by_status.get(status, status.replace("_", " ").title()),
        "status": status,
        "actor": data.get("actor_email") or "sistema",
        "at": data.get("created_at"),
        "metadata": {
            "action_type": data.get("action_type"),
            "adapter_name": data.get("adapter_name"),
            "legacy_execution_id": data.get("legacy_execution_id"),
            "completed_at": data.get("completed_at"),
        },
        "payload": _details(data.get("input")),
        "result": _details(data.get("dry_run_result"))
        or _details(data.get("execution_result")),
        "side_effect": _details(data.get("side_effect")),
        "error": data.get("error_message") or data.get("error_code"),
    }


@_bind_to_core
def _outcome_to_activity(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    return {
        "id": f"outcome:{data.get('id')}",
        "kind": "outcome",
        "type": "prediction_outcome",
        "label": str(data.get("outcome_summary") or "Outcome registrado"),
        "status": "recorded",
        "actor": _details(data.get("metadata")).get("reported_by") or "user",
        "at": data.get("created_at"),
        "metadata": {
            "option_id": data.get("option_id"),
            "action_taken": data.get("action_taken"),
            "prediction_error": data.get("prediction_error"),
            "learned_rule": data.get("learned_rule"),
        },
    }


@_bind_to_core
async def get_item_activity(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_read(item_id, user, fetcher=fetcher)
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    event_rows: list[Any] = []
    execution_rows: list[Any] = []
    decision_action_rows: list[Any] = []
    action_run_rows: list[Any] = []
    outcome_rows: list[Any] = []

    async def _load_item_activity(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> tuple[list[Any], list[Any], list[Any]]:
        events = await conn.fetch(
            """
            SELECT id, item_id, event_type, actor_email, metadata, created_at
              FROM control_room_item_events
             WHERE workspace_id = $1
               AND item_id = $2
             ORDER BY created_at DESC
             LIMIT 50
            """,
            scoped_workspace_id,
            item["id"],
        )
        executions = await conn.fetch(
            """
            SELECT id, item_id, template_id, mode, status, payload, result,
                   error, actor_email, created_at, completed_at
              FROM control_room_action_executions
             WHERE workspace_id = $1
               AND item_id = $2
             ORDER BY created_at DESC
             LIMIT 50
            """,
            scoped_workspace_id,
            item["id"],
        )
        decision_actions: list[Any] = []
        if item.get("decision_id"):
            decision_actions = await conn.fetch(
                """
                SELECT da.id, da.decision_id, da.action_text, da.note, da.actor, da.ts
                  FROM decision_actions da
                  JOIN decisions d ON d.id = da.decision_id
                 WHERE d.workspace_id = $1
                   AND da.decision_id = $2
                 ORDER BY da.ts DESC
                 LIMIT 50
                """,
                scoped_workspace_id,
                int(item["decision_id"]),
            )
        return list(events), list(executions), list(decision_actions)

    event_rows, execution_rows, decision_action_rows = await _run_with_db_scope(
        pool, user, _load_item_activity
    )

    async def _load_scoped_activity(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> tuple[Any, Any]:
        runs = await conn.fetch(
            """
            SELECT id, item_id, decision_id, legacy_execution_id, action_type,
                   adapter_name, mode, status, input, dry_run_result,
                   execution_result, side_effect, error_code, error_message,
                   actor_email, created_at, completed_at
              FROM action_runs
             WHERE workspace_id = $1
               AND item_id = $2
             ORDER BY created_at DESC
             LIMIT 50
            """,
            scoped_workspace_id,
            item["id"],
        )
        outcomes = await conn.fetch(
            """
            SELECT id, signal_id, option_id, action_taken, predicted_value,
                   actual_value, prediction_error, outcome_summary,
                   learned_rule, metadata, created_at
              FROM prediction_outcomes
             WHERE workspace_id = $1
               AND signal_id = $2
             ORDER BY created_at DESC
             LIMIT 50
            """,
            scoped_workspace_id,
            item["id"],
        )
        return runs, outcomes

    action_run_rows, outcome_rows = await _run_with_db_scope(
        pool, user, _load_scoped_activity
    )

    activity = [
        *(_event_to_activity(row) for row in event_rows),
        *(_execution_to_activity(row) for row in execution_rows),
        *(_decision_action_to_activity(row) for row in decision_action_rows),
        *(_action_run_to_activity(row) for row in action_run_rows),
        *(_outcome_to_activity(row) for row in outcome_rows),
    ]
    activity.sort(key=lambda entry: str(entry.get("at") or ""), reverse=True)
    return {
        "item_id": item["id"],
        "activity": activity,
        "counts": {
            "events": len(event_rows),
            "executions": len(execution_rows),
            "decision_actions": len(decision_action_rows),
            "action_runs": len(action_run_rows),
            "outcomes": len(outcome_rows),
            "total": len(activity),
        },
    }


@_bind_to_core
async def _record_item_event(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    event_type: str,
    metadata: dict[str, Any],
    critical: bool = False,
) -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        await pool.execute(
            """
            INSERT INTO control_room_item_events (
                tenant_id, workspace_id, item_id, event_type,
                actor_id, actor_email, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            tenant_id,
            workspace_id,
            item["id"],
            event_type,
            user.get("id"),
            user.get("email"),
            json.dumps(metadata),
        )
    except Exception:
        if critical:
            raise
        return


@_bind_to_core
def _merge_rule(existing: Any, rule: str) -> list[str]:
    rules: list[str] = []
    if isinstance(existing, list):
        for value in existing:
            text = str(value).strip()
            if text and text not in rules:
                rules.append(text)
    if rule and rule not in rules:
        rules.insert(0, rule)
    return rules[:10]


@_bind_to_core
async def _persist_lessons(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    decision_id: int | None,
    lessons: list[str],
) -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    for rule in lessons:
        try:
            await pool.execute(
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
                item.get("cartridge") or "platform",
                item.get("anomaly_type") or "control_room_item",
                rule,
                decision_id,
                _impact_for_item(item).get("confidence") or 0.7,
                json.dumps(
                    {
                        "source_dataset": item.get("source_dataset"),
                        "status": item.get("status"),
                    }
                ),
            )
        except Exception:
            return


@_bind_to_core
async def _set_execution_status(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    execution_status: str,
    critical: bool = False,
) -> None:
    workspace_id = _workspace_id(user)
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET execution_status = $1,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            execution_status,
            workspace_id,
            item["id"],
            json.dumps({"execution_status": execution_status}),
        )
    except Exception:
        if critical:
            raise
        return


@_bind_to_core
async def _ensure_item_row(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    status: str = "open",
    critical: bool = False,
) -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    impact = _impact_for_item(item)
    try:
        row = ensured_row(
            item,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id=expected_business_item_owner(item, user),
            status=status,
            impact=impact,
            metadata=_metadata_for_item(item, impact),
        )
        await ensure_business_item_row(
            pool,
            row,
            terminal_statuses=sorted(TERMINAL_ITEM_STATUSES),
            owner_scope_id=owner_scope_id(user),
            workspace_wide=_can_read_workspace_wide(user),
        )
    except OwnerScopeConflict:
        raise HTTPException(404, "control room item not found") from None
    except Exception:
        if critical:
            raise
        return


@_bind_to_core
def _lesson_matches_item(lesson: dict[str, Any], item: dict[str, Any]) -> bool:
    if lesson.get("item_id") == item.get("id"):
        return True
    return lesson.get("cartridge_id") == item.get("cartridge") and lesson.get(
        "anomaly_type"
    ) == item.get("anomaly_type")


@_bind_to_core
def _lesson_applications(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("lesson_applications")
    if not isinstance(raw, list):
        raw = item.get("omega", {}).get("lessons", {}).get("applied")
    if not isinstance(raw, list):
        return []
    applications: list[dict[str, Any]] = []
    for value in raw:
        if isinstance(value, dict):
            applications.append(value)
    return applications[:20]


@_bind_to_core
def _dedupe_lessons(lessons: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for lesson in lessons:
        key = str(lesson.get("id") or f"{lesson.get('item_id')}:{lesson.get('rule')}")
        if key in seen:
            continue
        seen.add(key)
        unique.append(lesson)
    return unique


@_bind_to_core
async def list_thresholds(user: dict) -> dict[str, Any]:
    rows = await _load_threshold_rows(user, enabled_only=False)
    return {"thresholds": rows, "summary": _threshold_insights(rows)}


@_bind_to_core
async def upsert_threshold(
    body: dict[str, Any],
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    cartridge_id = str(body.get("cartridge_id") or "").strip()
    anomaly_type = str(body.get("anomaly_type") or "").strip()
    metric = str(body.get("metric") or "").strip()
    if not cartridge_id or not anomaly_type or not metric:
        raise HTTPException(400, "cartridge_id, anomaly_type and metric are required")
    known_cartridges = {module.cartridge for module in MODULES}
    if cartridge_id not in known_cartridges:
        raise HTTPException(400, "unknown cartridge_id")
    allowed = _allowed_from_user(user)
    if allowed is not None and cartridge_id not in allowed:
        raise HTTPException(403, "cartridge not allowed for active workspace")
    warning_value = _num(body.get("warning_value"))
    critical_value = _num(body.get("critical_value"))
    currency = str(body.get("currency") or "USD").strip().upper()[:8] or "USD"
    enabled = bool(body.get("enabled", True))
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    pool = await auth.pool()

    async def _write_threshold(
        conn: Any, _tenant_id: str | None, scoped_workspace_id: str
    ) -> Any:
        return await conn.fetchrow(
            """
            INSERT INTO control_room_thresholds (
                tenant_id, workspace_id, cartridge_id, anomaly_type, metric,
                warning_value, critical_value, currency, enabled, metadata, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW())
            ON CONFLICT (workspace_id, cartridge_id, anomaly_type, metric) DO UPDATE
            SET warning_value = EXCLUDED.warning_value,
                critical_value = EXCLUDED.critical_value,
                currency = EXCLUDED.currency,
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING id, cartridge_id, anomaly_type, metric, warning_value,
                      critical_value, currency, enabled, metadata, created_at, updated_at
            """,
            tenant_id,
            scoped_workspace_id,
            cartridge_id,
            anomaly_type,
            metric,
            warning_value,
            critical_value,
            currency,
            enabled,
            json.dumps(metadata),
        )

    row = await _run_with_db_scope(pool, user, _write_threshold)
    public = _threshold_to_public(row)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.threshold.upsert",
        resource_type="control_room_threshold",
        resource_id=str(public.get("id")),
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata=public,
        critical=True,
    )
    return {"threshold": public}


@_bind_to_core
async def list_lessons(
    user: dict,
    *,
    cartridge_id: str | None = None,
    anomaly_type: str | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    lessons = await _load_lesson_rows(
        user,
        cartridge_id=cartridge_id,
        anomaly_type=anomaly_type,
        item_id=item_id,
        limit=100,
    )
    lessons = filter_by_eligible_parent(
        lessons,
        eligible_item_ids(await _persisted_business_items(user)),
    )
    return {
        "lessons": lessons,
        "summary": _lesson_insights(lessons),
    }


@_bind_to_core
async def get_suggested_actions(
    user: dict,
    item: dict[str, Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    lesson_rows = await _load_lesson_rows(
        user,
        cartridge_id=item.get("cartridge"),
        anomaly_type=item.get("anomaly_type"),
        limit=100,
    )
    item_lessons = await _load_lesson_rows(user, item_id=item.get("id"), limit=100)
    persisted_items = await _persisted_business_items(user)
    business_ids = eligible_item_ids([*persisted_items, item])
    lessons = filter_by_eligible_parent(
        _dedupe_lessons([*lesson_rows, *item_lessons]),
        business_ids,
    )
    suggestions = _suggested_actions_from_lessons(item, lessons, limit=limit)
    return {
        "item_id": item.get("id"),
        "cartridge_id": item.get("cartridge"),
        "anomaly_type": item.get("anomaly_type"),
        "suggested_actions": suggestions,
    }


class ControlRoomService:
    async def get_suggested_actions(
        self,
        user: dict,
        item: dict[str, Any],
        *,
        limit: int = 5,
    ) -> dict[str, Any]:
        return await get_suggested_actions(user, item, limit=limit)


__all__ = (
    "_row_to_public",
    "_actor_id",
    "_can_read_workspace_wide",
    "_threshold_to_public",
    "_threshold_insights",
    "_load_threshold_rows",
    "_lesson_to_public",
    "_load_lesson_rows",
    "_lesson_insights",
    "_suggested_action_from_lesson",
    "_suggested_actions_from_lessons",
    "_attach_lessons_to_items",
    "_threshold_map",
    "_threshold_rule",
    "_threshold_value",
    "_threshold_ref",
    "_source_state_item",
    "_control_state",
    "_default_control_items",
    "_control_items_for_item",
    "_decision_intelligence_for_item",
    "_with_omega",
    "_status_sort_key",
    "_alert_type_for_item",
    "_parse_utc_datetime",
    "_alert_state",
    "_metadata_for_item",
    "_alert_message",
    "_alert_for_item",
    "_alert_payload",
    "_diagnostic_metadata",
    "_persist_item_state",
    "_overlay_item_state",
    "_persisted_item_for_mutation",
    "_item_for_mutation",
    "_item_for_read",
    "_event_to_activity",
    "_execution_to_activity",
    "_decision_action_to_activity",
    "_action_run_to_activity",
    "_outcome_to_activity",
    "get_item_activity",
    "_record_item_event",
    "_merge_rule",
    "_persist_lessons",
    "_set_execution_status",
    "_ensure_item_row",
    "_lesson_matches_item",
    "_lesson_applications",
    "_dedupe_lessons",
    "list_thresholds",
    "upsert_threshold",
    "list_lessons",
    "get_suggested_actions",
    "ControlRoomService",
)
