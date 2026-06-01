from __future__ import annotations

import types

from app.services.control_room import core as _core


_RESERVED_GLOBALS = {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__"}
for _name, _value in _core.__dict__.items():
    if _name not in _RESERVED_GLOBALS:
        globals()[_name] = _value


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
    return os.environ.get("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@_bind_to_core
def _external_delivery_enabled() -> bool:
    return os.environ.get("CONTROL_ROOM_ENABLE_EXTERNAL_DELIVERY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@_bind_to_core
def _writeback_capability(template: dict[str, Any]) -> dict[str, Any]:
    template_id = str(template.get("template_id") or "")
    if template_id in SUPPORTED_INTERNAL_WRITEBACK_TEMPLATES:
        return {
            "supported": True,
            "mode": "supervised_execution",
            "target": "decision_actions",
            "external": False,
            "requires_flag": False,
            "requires_external_writeback_flag": False,
            "requires_confirmation": True,
            "requires_decision": True,
            "requires_dry_run": True,
            "permission": "control_room.execute",
            "status": "supported",
            "description": "Crea un seguimiento operativo auditado en decision_actions; no escribe en ERP.",
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
    estimate_value = round(float(estimate or 0), 2) if estimate is not None else None
    severity_weight = SEVERITY_WEIGHT.get(str(item.get("severity") or "medium"), 2)
    impact_points = 0 if estimate_value is None else min(42, int(abs(estimate_value) / 10_000))
    threshold_state = str(item.get("threshold_state") or "")
    threshold_points = {"critical": 12, "warning": 6}.get(threshold_state, 0)
    priority_score = min(
        100,
        max(0, severity_weight * 14 + impact_points + int(confidence * 20) + threshold_points),
    )
    return {
        "item_id": item.get("id"),
        "status": status,
        "estimate": estimate_value,
        "currency": currency,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "priority_score": priority_score,
        "drivers": drivers,
        "formula": formula,
        "explanation": explanation,
    }


@_bind_to_core
def _priority_payload(item: dict[str, Any], impact: dict[str, Any] | None = None) -> dict[str, Any]:
    impact = impact or _impact_for_item(item)
    score = int(impact.get("priority_score") or 0)
    drivers: list[dict[str, Any]] = [
        {
            "label": "Severidad",
            "value": item.get("severity") or "medium",
            "points": SEVERITY_WEIGHT.get(str(item.get("severity") or "medium"), 2) * 14,
        }
    ]
    if impact.get("status") == "ok" and impact.get("estimate") is not None:
        drivers.append({
            "label": "Impacto economico",
            "value": impact.get("estimate"),
            "currency": impact.get("currency") or "USD",
            "points": min(42, int(abs(float(impact.get("estimate") or 0)) / 10_000)),
        })
    else:
        drivers.append({"label": "Impacto economico", "value": "no disponible", "points": 0})

    threshold_state = str(item.get("threshold_state") or "default")
    threshold_points = {"critical": 16, "warning": 8}.get(threshold_state, 0)
    if threshold_points:
        drivers.append({"label": "Umbral", "value": threshold_state, "points": threshold_points})
        score += threshold_points

    source_status = ""
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    if item.get("kind") == "source_state":
        source_status = str(details.get("source_status") or item.get("status") or "")
    source_points = {
        "invalid_schema": 24,
        "unavailable": 22,
        "missing": 20,
        "blocked": 18,
        "no_permission": 18,
        "empty": 8,
    }.get(source_status, 0)
    if source_points:
        drivers.append({"label": "Salud fuente", "value": source_status, "points": source_points})
        score += source_points

    lesson_count = int(item.get("lesson_count") or 0)
    lesson_points = min(12, lesson_count * 4)
    if lesson_points:
        drivers.append({"label": "Patron aprendido", "value": lesson_count, "points": lesson_points})
        score += lesson_points

    if str(item.get("status") or "open") in TERMINAL_ITEM_STATUSES:
        drivers.append({"label": "Estado cerrado", "value": item.get("status"), "points": -35})
        score -= 35

    score = max(0, min(100, score))
    band = "critical" if score >= 90 else "high" if score >= 75 else "medium" if score >= 55 else "low"
    return {
        "score": score,
        "band": band,
        "drivers": drivers,
        "formula": "severity + impact + confidence + thresholds + source_health + learned_patterns",
    }


@_bind_to_core
def _impact_for_item(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    if item.get("kind") == "source_state":
        return _impact_payload(
            item=item,
            estimate=None,
            status="unavailable",
            confidence=0.2,
            drivers=[{"label": "Estado fuente", "value": details.get("source_status") or item.get("status")}],
            formula="Sin impacto monetario hasta restaurar materializacion.",
            explanation="La fuente no entrega datos suficientes para calcular dinero sin inventar cifras.",
        )

    stored = _num(item.get("impact_estimate"))
    if stored is not None and stored > 0:
        return _impact_payload(
            item=item,
            estimate=stored,
            status="ok",
            confidence=_num(item.get("confidence")) or 0.6,
            drivers=[{"label": "Impacto persistido", "value": stored, "currency": item.get("impact_currency") or "USD"}],
            formula="Impacto persistido en control_room_items.",
            explanation="Estimacion recuperada del estado operativo persistido.",
            currency=str(item.get("impact_currency") or "USD"),
        )

    anomaly_type = str(item.get("anomaly_type") or "")
    cartridge = str(item.get("cartridge") or "")

    if cartridge == "replicon" and anomaly_type in {"low_margin", "wip_variance"}:
        revenue = _num(details.get("revenue_usd"))
        margin_usd = _num(details.get("margen_bruto_usd"))
        wip = _num(details.get("wip_usd")) or 0
        margin_gap = 0.0
        if revenue is not None and margin_usd is not None:
            margin_gap = max(0.0, revenue * 0.20 - margin_usd)
        exposure = margin_gap + (abs(wip) if abs(wip) >= 5000 else 0)
        if exposure > 0:
            return _impact_payload(
                item=item,
                estimate=exposure,
                status="ok",
                confidence=0.78,
                drivers=[
                    {"label": "Brecha margen objetivo 20%", "value": round(margin_gap, 2), "currency": "USD"},
                    {"label": "WIP bajo revision", "value": round(abs(wip), 2), "currency": "USD"},
                ],
                formula="max(0, revenue_usd * 20% - margen_bruto_usd) + abs(wip_usd si >= 5000)",
                explanation="Usa P&L Replicon materializado; no escribe en Replicon.",
            )

    if cartridge == "replicon" and anomaly_type == "non_billable_ratio":
        hours = _num(details.get("horas_no_facturables"))
        rate = (
            _num(details.get("billing_rate_usd"))
            or _num(details.get("billing_rate"))
            or _num(details.get("rate_usd"))
            or _num(details.get("currenthourlybillingamount"))
        )
        if hours is not None and rate is not None:
            return _impact_payload(
                item=item,
                estimate=hours * rate,
                status="ok",
                confidence=0.7,
                drivers=[
                    {"label": "Horas no facturables", "value": round(hours, 2)},
                    {"label": "Tarifa Replicon", "value": round(rate, 2), "currency": "USD"},
                ],
                formula="horas_no_facturables * tarifa_replicon",
                explanation="Calcula exposicion de horas no facturables con tarifa real disponible.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "negative_revenue":
        revenue = _num(details.get("revenue"))
        if revenue is not None:
            return _impact_payload(
                item=item,
                estimate=abs(revenue),
                status="ok",
                confidence=0.74,
                drivers=[{"label": "Revenue negativo", "value": revenue, "currency": "USD"}],
                formula="abs(revenue)",
                explanation="Usa revenue materializado por cliente/periodo.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "aged_sales_backlog":
        open_value = _num(details.get("open_value"))
        if open_value is not None:
            return _impact_payload(
                item=item,
                estimate=open_value,
                status="ok",
                confidence=0.62,
                drivers=[
                    {"label": "Backlog abierto", "value": round(open_value, 2), "currency": "USD"},
                    {"label": "Antiguedad maxima", "value": _num(details.get("oldest_age_days")) or 0, "unit": "dias"},
                ],
                formula="open_value",
                explanation="Exposicion comercial, no perdida confirmada.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "supplier_spend_concentration":
        spend = _num(details.get("total_spend"))
        if spend is not None:
            return _impact_payload(
                item=item,
                estimate=spend,
                status="ok",
                confidence=0.45,
                drivers=[{"label": "Gasto concentrado", "value": round(spend, 2), "currency": "USD"}],
                formula="total_spend",
                explanation="Exposicion de compras bajo revision, no ahorro garantizado.",
            )

    if cartridge == "sap_s4hana" and anomaly_type in {"missing_address", "missing_tax_id", "duplicate_business_partner"}:
        exposure = (
            _num(details.get("open_value"))
            or _num(details.get("balance_usd"))
            or _num(details.get("exposure_usd"))
            or _num(details.get("total_spend"))
        )
        if exposure is not None and exposure > 0:
            return _impact_payload(
                item=item,
                estimate=exposure,
                status="ok",
                confidence=0.52,
                drivers=[
                    {"label": "Exposicion BP", "value": round(exposure, 2), "currency": "USD"},
                    {"label": "Tipo maestro", "value": anomaly_type},
                ],
                formula="open_value | balance_usd | exposure_usd | total_spend",
                explanation="Usa exposicion comercial/proveedor disponible para el maestro BP.",
            )

    if cartridge == "sap_hcm" and anomaly_type == "terminated_but_active":
        monthly_cost = (
            _num(details.get("monthly_cost_usd"))
            or _num(details.get("salary_monthly_usd"))
            or _num(details.get("costo_mensual_usd"))
        )
        if monthly_cost is not None and monthly_cost > 0:
            return _impact_payload(
                item=item,
                estimate=monthly_cost * 3,
                status="ok",
                confidence=0.66,
                drivers=[
                    {"label": "Costo mensual empleado", "value": round(monthly_cost, 2), "currency": "USD"},
                    {"label": "Ventana control", "value": 3, "unit": "meses"},
                ],
                formula="monthly_cost_usd * 3 meses de exposicion",
                explanation="Estima cola de costo/acceso para empleado terminado pero activo.",
            )

    if cartridge == "sap_successfactors" and anomaly_type in {"missing_manager", "missing_department", "missing_job_code"}:
        affected = (
            _num(details.get("affected_employees"))
            or _num(details.get("direct_reports"))
            or _num(details.get("headcount"))
        )
        monthly_cost = _num(details.get("avg_monthly_cost_usd")) or _num(details.get("salary_monthly_usd"))
        if affected is not None and monthly_cost is not None and affected > 0 and monthly_cost > 0:
            return _impact_payload(
                item=item,
                estimate=affected * monthly_cost * 0.15,
                status="ok",
                confidence=0.48,
                drivers=[
                    {"label": "Personas afectadas", "value": round(affected, 2)},
                    {"label": "Costo mensual promedio", "value": round(monthly_cost, 2), "currency": "USD"},
                ],
                formula="affected_employees * avg_monthly_cost_usd * 15%",
                explanation="Proxy de riesgo operativo SF cuando hay base de costo y poblacion afectada.",
            )

    monthly_cost = (
        _num(details.get("monthly_cost_usd"))
        or _num(details.get("salary_monthly_usd"))
        or _num(details.get("costo_mensual_usd"))
    )
    if monthly_cost is not None and monthly_cost > 0:
        return _impact_payload(
            item=item,
            estimate=monthly_cost,
            status="ok",
            confidence=0.55,
            drivers=[{"label": "Costo mensual", "value": round(monthly_cost, 2), "currency": "USD"}],
            formula="monthly_cost_usd",
            explanation="Usa costo directo disponible en la fuente.",
        )

    return _impact_payload(
        item=item,
        estimate=None,
        status="unavailable",
        confidence=0.25,
        drivers=[],
        formula="Sin base monetaria disponible en el dataset.",
        explanation="La senal es operativa; falta cost basis para convertirla a dinero sin inventar cifras.",
    )


@_bind_to_core
def _template_ids_for_item(item: dict[str, Any]) -> list[str]:
    anomaly_type = str(item.get("anomaly_type") or "")
    cartridge = str(item.get("cartridge") or "")
    module_id = str(item.get("module_id") or "")
    if item.get("kind") == "source_state":
        return ["restore_data_source", "create_followup_task", "request_owner_review"]
    if cartridge == "replicon":
        if anomaly_type in {"low_margin", "wip_variance", "non_billable_ratio"}:
            return ["prepare_billing_review", "prepare_replicon_adjustment", "create_followup_task"]
        return ["prepare_replicon_adjustment", "request_owner_review", "create_followup_task"]
    if cartridge == "sap_s4hana":
        if anomaly_type in {"negative_revenue", "aged_sales_backlog"} or module_id == "sap_s4hana_sales":
            return ["prepare_s4_revenue_review", "prepare_sap_review", "create_followup_task"]
        if anomaly_type in {"missing_address", "missing_tax_id", "duplicate_business_partner"}:
            return ["prepare_s4_business_partner_review", "prepare_sap_review", "create_followup_task"]
        if anomaly_type == "supplier_spend_concentration" or module_id == "sap_s4hana_procurement":
            return ["prepare_s4_procurement_review", "prepare_sap_review", "create_followup_task"]
        return ["prepare_sap_review", "request_owner_review", "create_followup_task"]
    if cartridge == "sap_hcm":
        if anomaly_type == "terminated_but_active":
            return ["prepare_hcm_access_review", "request_owner_review", "create_followup_task"]
        return ["prepare_hcm_org_review", "request_owner_review", "create_followup_task"]
    if cartridge == "sap_successfactors":
        if module_id == "sap_successfactors_recruiting":
            return ["prepare_successfactors_recruiting_review", "prepare_successfactors_review", "create_followup_task"]
        return ["prepare_successfactors_review", "request_owner_review", "create_followup_task"]
    return ["request_owner_review", "create_followup_task"]


@_bind_to_core
def _action_templates_for_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _template_with_writeback(ACTION_TEMPLATES[template_id])
        for template_id in _template_ids_for_item(item)
        if template_id in ACTION_TEMPLATES
    ]


@_bind_to_core
def _primary_template_for_item(item: dict[str, Any]) -> dict[str, Any]:
    templates = _action_templates_for_item(item)
    return templates[0] if templates else dict(ACTION_TEMPLATES["request_owner_review"])


@_bind_to_core
def _action_payload_for_template(item: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    action_kind = str(template.get("action_kind") or "owner_review")
    writeback = _writeback_capability(template)
    base = {
        "action_kind": action_kind,
        "template_id": template.get("template_id"),
        "entity": {
            "kind": item.get("entity_kind"),
            "id": item.get("entity_id"),
            "label": item.get("entity_label"),
        },
        "source_dataset": item.get("source_dataset"),
        "severity": item.get("severity"),
        "recommendation": item.get("recommendation"),
        "writeback": writeback,
    }
    if action_kind == "billing_review":
        return {
            **base,
            "replicon": {
                "project": details.get("project_name") or details.get("proyecto") or item.get("entity_label"),
                "revenue_manager": details.get("revenue_manager"),
                "revenue_usd": _num(details.get("revenue_usd")),
                "margin_pct": _num(details.get("margen_bruto_pct")),
                "wip_usd": _num(details.get("wip_usd")),
                "billing_gap_usd": _num(details.get("billing_gap_usd")),
            },
            "prepared_actions": [
                "validar WIP y facturacion contra contrato",
                "confirmar owner financiero",
                "preparar ajuste Replicon sin ejecutarlo",
            ],
        }
    if action_kind == "replicon_adjustment":
        return {
            **base,
            "replicon": {
                "consultant": details.get("consultant_name") or details.get("consultor") or item.get("entity_label"),
                "project": details.get("project_name") or details.get("proyecto"),
                "allocation_pct": _num(details.get("pct_asignacion")),
                "billable_hours": _num(details.get("billable_hours")),
                "non_billable_hours": _num(details.get("horas_no_facturables")),
            },
            "prepared_actions": [
                "validar asignacion/timesheet",
                "preparar ajuste para owner",
            ],
        }
    if action_kind in {"s4_revenue_review", "s4_business_partner_review", "s4_procurement_review", "sap_review"}:
        return {
            **base,
            "sap_s4hana": {
                "business_partner": details.get("business_partner"),
                "customer": details.get("customer_code") or details.get("customer_name"),
                "supplier": details.get("supplier_code") or details.get("supplier_name"),
                "open_value": _num(details.get("open_value")),
                "revenue": _num(details.get("revenue")),
                "spend": _num(details.get("total_spend")),
            },
            "prepared_actions": [
                "validar maestro/partida en SAP",
                "adjuntar evidencia a decision",
                "bloquear write-back hasta aprobacion",
            ],
        }
    if action_kind in {"hcm_access_review", "hcm_org_review"}:
        return {
            **base,
            "sap_hcm": {
                "pernr": details.get("pernr") or item.get("entity_id"),
                "position": details.get("position") or details.get("plans"),
                "cost_center": details.get("cost_center") or details.get("kostl"),
                "monthly_cost_usd": _num(details.get("monthly_cost_usd") or details.get("salary_monthly_usd")),
            },
            "prepared_actions": [
                "validar baja/posicion/centro de costo",
                "preparar bloqueo o correccion para aprobacion",
            ],
        }
    if action_kind in {"successfactors_employee_review", "successfactors_recruiting_review"}:
        return {
            **base,
            "sap_successfactors": {
                "user_id": details.get("user_id") or item.get("entity_id"),
                "manager": details.get("manager_id") or details.get("manager"),
                "department": details.get("department"),
                "job_code": details.get("job_code"),
                "requisition": details.get("requisition_id"),
            },
            "prepared_actions": [
                "validar owner en SuccessFactors",
                "preparar correccion o excepcion auditada",
            ],
        }
    return {
        **base,
        "prepared_actions": [
            "solicitar revision de owner",
            "mantener evidencia y fecha de control",
        ],
    }


@_bind_to_core
def _execution_payload(item: dict[str, Any], mode: str, template: dict[str, Any]) -> dict[str, Any]:
    impact = _impact_for_item(item)
    action_payload = _action_payload_for_template(item, template)
    writeback = _writeback_capability(template)
    return {
        "mode": mode,
        "external_writeback_enabled": _external_writeback_enabled(),
        "supervised_execution_enabled": True,
        "execution_contract": "supervised_execution",
        "dry_run": mode != "execute_live",
        "template": template,
        "target_system": template.get("cartridge_id") if template.get("cartridge_id") != "platform" else item.get("cartridge"),
        "item": {
            "id": item.get("id"),
            "title": item.get("title"),
            "kind": item.get("kind"),
            "cartridge": item.get("cartridge"),
            "domain": item.get("domain"),
            "source_dataset": item.get("source_dataset"),
            "entity_kind": item.get("entity_kind"),
            "entity_id": item.get("entity_id"),
            "entity_label": item.get("entity_label"),
            "anomaly_type": item.get("anomaly_type"),
            "severity": item.get("severity"),
            "selected_option_id": item.get("selected_option_id"),
        },
        "impact": impact,
        "action_payload": action_payload,
        "writeback": writeback,
        "operations": [
            {
                "operation": template.get("action_kind"),
                "status": "pending_execution" if mode == "execute_live" else "preview" if mode == "preview" else "validated",
                "requires_approval": True,
                "external_write": bool(writeback.get("external")),
                "internal_write": bool(writeback.get("supported")) and not bool(writeback.get("external")),
                "payload": action_payload,
                "writeback": writeback,
                "evidence": {
                    "sql": item.get("sql"),
                    "recommendation": item.get("recommendation"),
                    "root_cause": item.get("root_cause"),
                },
            }
        ],
        "guardrails": {
            "human_approval_required": True,
            "supervised_execution_available": True,
            "external_writeback_blocked_by_default": not _external_writeback_enabled(),
            "writeback_blocked_by_default": not _external_writeback_enabled(),
            "feature_flag": "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
            "supported_templates": sorted(SUPPORTED_INTERNAL_WRITEBACK_TEMPLATES),
        },
    }


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
    workspace_id = _workspace_id(user)
    title = f"{item['title']} - {item['entity_label']}"
    description = (
        f"{item['description']}\n\n"
        f"Recomendacion OMEGA: {item['recommendation']}\n\n"
        f"Fuente: {item['source_dataset']} ({item['cartridge']})."
    )
    kpis = [{
        "label": "Severidad",
        "value": item["severity"],
        "source": item["source_dataset"],
    }, {
        "label": "Entidad",
        "value": item["entity_label"],
        "source": item["cartridge"],
    }, {
        "label": "Estado OMEGA",
        "value": item.get("status") or "open",
        "source": "control_room",
    }]
    pool = await auth.pool()
    row = await pool.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
           VALUES ($1, $2, CURRENT_DATE + 7, $3::jsonb, $4, NULL, 'shared', $5)
           RETURNING *""",
        title,
        description,
        json.dumps(kpis),
        user["id"],
        workspace_id,
    )
    await pool.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        row["id"],
        "Decision creada desde Sala de Control",
        item["recommendation"],
        user.get("email") or "user",
    )
    await _ensure_item_row(pool, user=user, item=item, status="decision_created")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'decision_created',
                   decision_id = $1,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            row["id"],
            workspace_id,
            item["id"],
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="decision_created",
        metadata={"decision_id": row["id"]},
    )
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
    item = {**item, "decision_id": row["id"], "status": "decision_created"}
    return {"decision": dict(row), "item": _with_omega(item), "anomaly": _with_omega(item)}


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
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status="in_review")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = CASE
                       WHEN status = ANY($4::text[]) THEN status
                       ELSE 'in_review'
                   END,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                   selected_option_id = $5,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            json.dumps({"selected_option_id": option_id}),
            workspace_id,
            item["id"],
            sorted(TERMINAL_ITEM_STATUSES),
            option_id,
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="option_selected",
        metadata={"option_id": option_id},
    )
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
    item = _with_omega({**item, "selected_option_id": option_id, "status": "in_review"})
    return {"selected": True, "option_id": option_id, "item": item, "anomaly": item}


@_bind_to_core
async def get_item_impact(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
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
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "open")
    event_type = OMEGA_STEP_EVENT_TYPES[step]
    metadata = {
        "step_id": step,
        "note": str(note or "").strip()[:500],
        "control_id": str(control_id or "").strip()[:120],
    }
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type=event_type,
        metadata=metadata,
    )
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
    control = next((row for row in current_controls if str(row.get("id")) == control_key), None)
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
            due_at = (datetime.now(UTC) + timedelta(days=max(0, int(body.get("days"))))).isoformat()
        except (TypeError, ValueError):
            due_at = control.get("due_at")
    else:
        due_at = control.get("due_at")

    owner = str(body.get("owner") or body.get("owner_email") or control.get("owner") or user.get("email") or "operaciones").strip()
    note = str(body.get("note") or "").strip()[:500]
    now = datetime.now(UTC).isoformat()
    existing_state = _control_state(item)
    next_control = {
        **control,
        **existing_state.get(control_key, {}),
        "id": control_key,
        "status": next_status,
        "st": CONTROL_ITEM_STATUS_LABELS[next_status],
        "owner": owner,
        "due_at": due_at,
        "note": note,
        "updated_at": now,
        "updated_by": user.get("email") or "user",
    }
    next_state = {
        **existing_state,
        control_key: next_control,
    }
    item_status = str(item.get("status") or "open")
    target_status = item_status if item_status in TERMINAL_ITEM_STATUSES else "in_review"
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status=target_status)
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = CASE
                       WHEN status = ANY($4::text[]) THEN status
                       ELSE $5::text
                   END,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            json.dumps({"control_state": next_state}),
            workspace_id,
            item["id"],
            sorted(TERMINAL_ITEM_STATUSES),
            target_status,
        )
    except Exception:
        pass
    event_type = "control_updated" if next_status != "closed" else "control_checked"
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type=event_type,
        metadata={
            "control_id": control_key,
            "control_status": next_status,
            "owner": owner,
            "due_at": due_at,
            "note": note,
        },
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
    public_item = _with_omega({**item, "status": target_status, "control_state": next_state})
    updated_control = next(
        row for row in public_item.get("omega", {}).get("control", {}).get("items", [])
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
    decision_id = int(item["decision_id"]) if item.get("decision_id") is not None else None
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    await _persist_lessons(pool, user=user, item=item, decision_id=decision_id, lessons=[rule])
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            _workspace_id(user),
            item["id"],
            json.dumps({"learned_rules": _merge_rule(item.get("omega", {}).get("lessons", {}).get("rules"), rule)}),
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="lesson_recorded",
        metadata={"decision_id": decision_id, "lessons": [rule], "manual": True},
    )
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
        lessons = [{
            "id": None,
            "item_id": item["id"],
            "cartridge_id": item.get("cartridge") or "platform",
            "anomaly_type": item.get("anomaly_type") or "control_room_item",
            "rule": rule,
            "source_decision_id": decision_id,
            "confidence": _impact_for_item(item).get("confidence") or 0.7,
            "metadata": {"manual": True},
            "created_at": datetime.now(UTC).isoformat(),
        }]
    public_item = _with_omega({
        **item,
        "related_lessons": lessons[:5],
        "lesson_count": len(lessons),
        "learned_rules": _merge_rule(item.get("omega", {}).get("lessons", {}).get("rules"), rule),
    })
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
    lesson = next((row for row in lessons if int(row.get("id") or 0) == int(lesson_id)), None)
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

    applications = _lesson_applications(item)
    applications = [entry for entry in applications if int(entry.get("lesson_id") or 0) != int(lesson_id)]
    applications.insert(0, application)
    applications = applications[:20]
    learned_rules = _merge_rule(item.get("omega", {}).get("lessons", {}).get("rules"), rule)
    target_status = item.get("status") if item.get("status") in TERMINAL_ITEM_STATUSES else "in_review"

    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=target_status)
    try:
        await pool.execute(
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
            """,
            _workspace_id(user),
            item["id"],
            json.dumps({
                "learned_rules": learned_rules,
                "lesson_applications": applications,
            }),
            sorted(TERMINAL_ITEM_STATUSES),
            target_status,
        )
    except Exception:
        pass

    await _record_item_event(
        pool,
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
    related = _dedupe_lessons([lesson, *(item.get("related_lessons") or []), *lessons])[:5]
    public_item = _with_omega({
        **item,
        "status": target_status,
        "related_lessons": related,
        "lesson_count": max(len(related), int(item.get("lesson_count") or 0), 1),
        "learned_rules": learned_rules,
        "lesson_applications": applications,
    })
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
    raise HTTPException(400, "action template is not valid for this item")


@_bind_to_core
async def action_preview(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    template = _resolve_template(item, template_id)
    payload = _execution_payload(item, "preview", template)
    result = {
        "ok": True,
        "mode": "preview",
        "message": "Preview generado; no se ejecuto ningun cambio externo.",
        "external_write": False,
    }
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="preview",
        status="generated",
        payload=payload,
        result=result,
    )
    await _set_execution_status(pool, user=user, item=item, execution_status="preview_generated")
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_preview",
        metadata={"template_id": template["template_id"], "execution_id": execution.get("id")},
    )
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
    public_item = _with_omega({**item, "execution_status": "preview_generated"})
    return {"execution": execution, "payload": payload, "result": result, "item": public_item}


@_bind_to_core
async def action_dry_run(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    template = _resolve_template(item, template_id)
    payload = _execution_payload(item, "dry_run", template)
    warnings = []
    if payload["impact"]["status"] != "ok":
        warnings.append("impact_unavailable")
    if not item.get("decision_id"):
        warnings.append("decision_not_created_yet")
    result = {
        "ok": True,
        "mode": "dry_run",
        "validated": True,
        "external_write": False,
        "warnings": warnings,
        "message": "Dry-run validado. V1 no escribe en sistemas externos.",
    }
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="dry_run",
        status="validated",
        payload=payload,
        result=result,
    )
    await _set_execution_status(pool, user=user, item=item, execution_status="dry_run_validated")
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_dry_run",
        metadata={"template_id": template["template_id"], "execution_id": execution.get("id"), "warnings": warnings},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.action.dry_run",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"template_id": template["template_id"], "result": result},
        critical=True,
    )
    public_item = _with_omega({**item, "execution_status": "dry_run_validated"})
    return {"execution": execution, "payload": payload, "result": result, "item": public_item}


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

    template_id = _primary_template_for_item(item)["template_id"]
    preview = await action_preview(
        item_id,
        user,
        template_id=template_id,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    dry_run = await action_dry_run(
        item_id,
        user,
        template_id=template_id,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )
    item = dry_run["item"]
    steps.append({"step": "execution", "template_id": template_id, "status": item.get("execution_status")})

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
    await _record_item_event(
        pool,
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
        return value.strip().lower() in {"execute", "confirm", "confirmed", "true", "yes", "1"}
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
    await db.execute(
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
    await _set_execution_status(pool, user=user, item=item, execution_status="blocked", critical=True)
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_blocked",
        metadata={"template_id": template["template_id"], "execution_id": execution.get("id"), "reason": error},
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
        metadata={"template_id": template["template_id"], "result": result, "reason": error},
        critical=True,
    )


@_bind_to_core
async def _existing_executed_writeback(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    idempotency_key: str | None,
    critical: bool = False,
) -> dict[str, Any] | None:
    workspace_id = _workspace_id(user)
    try:
        if idempotency_key:
            row = await pool.fetchrow(
                """
                SELECT id, item_id, template_id, mode, status, payload, result,
                       error, actor_email, created_at, completed_at
                  FROM control_room_action_executions
                 WHERE workspace_id = $1
                   AND item_id = $2
                   AND template_id = $3
                   AND mode = 'execute_live'
                   AND status = 'executed'
                   AND (payload->>'idempotency_key' = $4 OR result->>'idempotency_key' = $4)
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                workspace_id,
                item["id"],
                template["template_id"],
                idempotency_key,
            )
        else:
            row = await pool.fetchrow(
                """
                SELECT id, item_id, template_id, mode, status, payload, result,
                       error, actor_email, created_at, completed_at
                  FROM control_room_action_executions
                 WHERE workspace_id = $1
                   AND item_id = $2
                   AND template_id = $3
                   AND mode = 'execute_live'
                   AND status = 'executed'
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                workspace_id,
                item["id"],
                template["template_id"],
            )
    except Exception:
        if critical:
            raise
        return None
    return _row_to_public(row) if row else None


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
    await db.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"control_room_writeback:{workspace_id}:{item['id']}:{template['template_id']}",
    )
    existing = await _existing_executed_writeback(
        db,
        user=user,
        item=item,
        template=template,
        idempotency_key=None,
        critical=True,
    )
    if existing:
        existing_result = _details(existing.get("result"))
        public_item = _with_omega({**item, "execution_status": "executed"})
        return {
            "executed": True,
            "idempotent": True,
            "execution": existing,
            "payload": _details(existing.get("payload")) or payload,
            "result": {**existing_result, "idempotent": True},
            "item": public_item,
        }
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
    await db.execute(
        """
        UPDATE control_room_items
           SET execution_status = 'executed',
               metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
               last_seen_at = NOW()
         WHERE workspace_id = $1
           AND item_id = $2
        """,
        workspace_id,
        item["id"],
        json.dumps({
            "execution_status": "executed",
            "writeback_result": {
                "adapter": "internal_followup_task",
                "target": "decision_actions",
                "decision_action_id": public_action.get("id"),
                "execution_id": execution.get("id"),
            },
        }),
    )
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
            "before": before,
            "after": after,
        },
    )
    public_item = _with_omega({**item, "execution_status": "executed"})
    return {
        "executed": True,
        "idempotent": False,
        "execution": execution,
        "payload": payload,
        "result": result,
        "decision_action": public_action,
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
    return str(template.get("template_type") or template.get("template_id") or "").strip().lower()


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
) -> dict[str, Any] | None:
    tenant_id, workspace_id = _workspace_scope(user)
    cartridge_id = str(item.get("cartridge") or template.get("cartridge_id") or "platform")
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
        "result_status": result.get("status") or result.get("adapter_result", {}).get("status"),
        "suggested_action": {
            "template_id": template.get("template_id"),
            "template_type": template_type,
            "label": template.get("label"),
            "action_kind": template.get("action_kind"),
            "target": result.get("target") or template.get("cartridge_id") or item.get("cartridge"),
            "adapter": adapter_name,
        },
    }
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
            cartridge_id,
            anomaly_type,
            rule,
            decision_id,
            confidence,
            json.dumps(metadata),
        )
    except Exception:
        return None
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
    idempotency_key: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    from app.services.adapters import AdapterCircuitOpenError, AdapterExecutionError

    cartridge_id = str(template.get("cartridge_id") or item.get("cartridge") or "").strip()
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

    try:
        credentials = _writeback_credentials_for_action(item=item, template=template, payload=payload)
        adapter = WriteBackAdapterFactory.get_adapter(template_type)
        adapter_name = adapter.__class__.__name__
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
        await _set_execution_status(pool, user=user, item=item, execution_status="failed", critical=True)
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={"template_id": template["template_id"], "execution_id": execution.get("id"), "reason": "circuit_breaker_open"},
            critical=True,
        )
        raise HTTPException(503, result) from exc
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
        await _set_execution_status(pool, user=user, item=item, execution_status="failed", critical=True)
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={"template_id": template["template_id"], "execution_id": execution.get("id"), "reason": "external_writeback_failed"},
            critical=True,
        )
        raise HTTPException(502, result) from exc
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
        await _set_execution_status(pool, user=user, item=item, execution_status="failed", critical=True)
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={"template_id": template["template_id"], "execution_id": execution.get("id"), "reason": type(exc).__name__},
            critical=True,
        )
        raise HTTPException(502, result) from exc

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
    await _set_execution_status(pool, user=user, item=item, execution_status=execution_status, critical=True)
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_executed" if ok else "action_failed",
        metadata={
            "template_id": template["template_id"],
            "template_type": template_type,
            "execution_id": execution.get("id"),
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
            "before": before,
            "after": after,
            "validation_result": public_validation_result,
            "adapter_result": public_adapter_result,
        },
    )
    if not ok:
        raise HTTPException(502, message)
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
    suggested_actions = _suggested_actions_from_lessons(item, [learning_lesson] if learning_lesson else [])
    public_item = _with_omega({
        **item,
        "execution_status": "executed",
        "suggested_actions": suggested_actions,
    })
    return {
        "executed": True,
        "idempotent": False,
        "execution": execution,
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
    confirm_execute: Any = False,
    idempotency_key: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    template = _resolve_template(item, template_id)
    payload = _execution_payload(item, "execute_live", template)
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review", critical=True)
    capability = _writeback_capability(template)
    if capability.get("external") and not capability.get("adapter_available"):
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message=str(capability.get("reason") or "No hay adapter ERP aprobado para este template."),
            error="adapter_missing",
        )
        raise HTTPException(501, "external ERP write-back adapter is not available for this template")

    if capability.get("external") and not _external_writeback_enabled():
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="Write-back ERP externo no habilitado en Control Room V1; usa ejecucion supervisada.",
            error="CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=false",
        )
        raise HTTPException(409, "external ERP write-back is not available in Control Room V1")

    if not capability.get("supported") and not capability.get("external"):
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="No hay ejecucion supervisada aprobada para este template.",
            error="unsupported_writeback_template",
        )
        raise HTTPException(501, "execution template is not supported in Control Room V1")

    if not _confirmed_for_execute(confirm_execute):
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="Confirmacion explicita requerida antes de ejecutar la accion supervisada.",
            error="explicit_confirmation_required",
        )
        raise HTTPException(409, "explicit execution confirmation is required")

    if not item.get("decision_id"):
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="Se requiere decision aprobada antes de la ejecucion supervisada.",
            error="decision_required",
        )
        raise HTTPException(409, "decision is required before execution")

    if str(item.get("status") or "") in TERMINAL_ITEM_STATUSES:
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="Item cerrado no puede ejecutar acciones supervisadas.",
            error="terminal_item",
        )
        raise HTTPException(409, "terminal control room item cannot execute supervised action")

    try:
        existing = await _existing_executed_writeback(
            pool,
            user=user,
            item=item,
            template=template,
            idempotency_key=(str(idempotency_key).strip() if idempotency_key else None),
            critical=True,
        )
    except Exception as exc:
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="No se pudo validar idempotencia antes de la ejecucion supervisada.",
            error="idempotency_lookup_failed",
        )
        raise HTTPException(503, "execution idempotency lookup failed") from exc
    if existing:
        existing_result = _details(existing.get("result"))
        public_item = _with_omega({**item, "execution_status": "executed"})
        return {
            "executed": True,
            "idempotent": True,
            "execution": existing,
            "payload": _details(existing.get("payload")) or payload,
            "result": {**existing_result, "idempotent": True},
            "item": public_item,
        }

    if str(item.get("execution_status") or "not_started") != "dry_run_validated":
        await _record_execute_block(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message="Se requiere dry-run validado antes de la ejecucion supervisada.",
            error="dry_run_required",
        )
        raise HTTPException(409, "dry-run validation is required before execution")

    if str(template.get("template_id") or "") == "create_followup_task":
        return await _execute_internal_followup_task(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            idempotency_key=(str(idempotency_key).strip() if idempotency_key else None),
            ip=ip,
            user_agent=user_agent,
        )

    if capability.get("external") and capability.get("supported"):
        return await _execute_external_writeback(
            pool,
            user=user,
            item=item,
            template=template,
            payload=payload,
            idempotency_key=(str(idempotency_key).strip() if idempotency_key else None),
            ip=ip,
            user_agent=user_agent,
        )

    await _record_execute_block(
        pool,
        user=user,
        item=item,
        template=template,
        payload=payload,
        ip=ip,
        user_agent=user_agent,
        message="No hay adapter ERP aprobado para este template.",
        error="unsupported_writeback_template",
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
    if decision_id is None:
        created = await create_decision_for_item(
            item_id,
            user,
            ip=ip,
            user_agent=user_agent,
            fetcher=fetcher,
        )
        decision_id = int(created["decision"]["id"])

    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    visible = await pool.fetchrow(
        "SELECT id FROM decisions WHERE id = $1 AND workspace_id = $2",
        decision_id,
        workspace_id,
    )
    if not visible:
        raise HTTPException(404, "decision not found")
    lessons = _lessons_for_item(item)
    action = await pool.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        decision_id,
        f"Aprobacion de recomendacion OMEGA: {item['title']}",
        item["recommendation"],
        user.get("email") or "user",
    )
    await _ensure_item_row(pool, user=user, item=item, status="approved")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'approved',
                   decision_id = $1,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                   resolved_at = COALESCE(resolved_at, NOW()),
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            decision_id,
            workspace_id,
            item["id"],
            json.dumps({"lessons": lessons}),
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="approved",
        metadata={"decision_id": decision_id, "action_id": dict(action).get("id")},
    )
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="lesson_recorded",
        metadata={"decision_id": decision_id, "lessons": lessons},
    )
    await _persist_lessons(pool, user=user, item=item, decision_id=decision_id, lessons=lessons)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.approve",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"decision_id": decision_id, "item": item},
        critical=True,
    )
    public_action = dict(action)
    if hasattr(public_action.get("ts"), "isoformat"):
        public_action["ts"] = public_action["ts"].isoformat()
    item = _with_omega({**item, "decision_id": decision_id, "status": "approved", "lessons": lessons})
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
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status="dismissed")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'dismissed',
                   dismissed_at = COALESCE(dismissed_at, NOW()),
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            workspace_id,
            item["id"],
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="dismissed",
        metadata={"reason": reason or ""},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.dismiss",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"reason": reason or "", "item": item},
        critical=True,
    )
    return {"dismissed": True, "item": _with_omega({**item, "status": "dismissed"})}


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
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status="open")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'open',
                   decision_id = NULL,
                   resolved_at = NULL,
                   dismissed_at = NULL,
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            workspace_id,
            item["id"],
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="reopened",
        metadata={"reason": reason or ""},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.reopen",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"reason": reason or "", "item": item},
        critical=True,
    )
    return {"reopened": True, "item": _with_omega({**item, "status": "open", "decision_id": None})}


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
    if current_status in TERMINAL_ITEM_STATUSES and next_state != "false_positive":
        raise HTTPException(409, "terminal control room item has no active alert")
    if not _alert_for_item(item) and next_state != "false_positive":
        raise HTTPException(404, "active alert not found")

    now = datetime.now(UTC).isoformat()
    existing_state = item.get("alert_state") if isinstance(item.get("alert_state"), dict) else {}
    owner_email = str(body.get("owner_email") or body.get("owner") or user.get("email") or "").strip()
    note = str(body.get("note") or "").strip()
    reason = str(body.get("reason") or "").strip()
    alert_state = {
        **existing_state,
        "state": next_state,
        "updated_at": now,
        "updated_by": user.get("email") or "user",
    }
    if note:
        alert_state["note"] = note
    if reason:
        alert_state["reason"] = reason
    if next_state == "acknowledged":
        alert_state["acknowledged_at"] = existing_state.get("acknowledged_at") or now
    elif next_state == "snoozed":
        alert_state["snoozed_until"] = _snoozed_until_from_body(body)
        alert_state["snoozed_at"] = now
    elif next_state == "assigned":
        alert_state["owner"] = owner_email or "operaciones"
        alert_state["assigned_at"] = now
    elif next_state == "false_positive":
        alert_state["false_positive_at"] = now
        alert_state["reason"] = reason or "Marcado como falso positivo desde Sala de Control"

    target_status = "dismissed" if next_state == "false_positive" else (
        current_status if current_status not in {"open", ""} else "in_review"
    )
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status=target_status)
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = CASE
                       WHEN $5::text = 'dismissed' THEN 'dismissed'
                       WHEN status = ANY($4::text[]) THEN status
                       ELSE $5::text
                   END,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                   dismissed_at = CASE
                       WHEN $5::text = 'dismissed' THEN COALESCE(dismissed_at, NOW())
                       ELSE dismissed_at
                   END,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            json.dumps({"alert_state": alert_state}),
            workspace_id,
            item["id"],
            sorted(TERMINAL_ITEM_STATUSES),
            target_status,
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type=event_type,
        metadata={"alert_state": alert_state, "note": note, "reason": reason},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action=audit_action,
        resource_type="control_room_alert",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"alert_state": alert_state, "item": item},
        critical=next_state == "false_positive",
    )
    public_item = _with_omega({**item, "status": target_status, "alert_state": alert_state})
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
    '_external_writeback_enabled',
    '_external_delivery_enabled',
    '_writeback_capability',
    '_template_with_writeback',
    '_impact_payload',
    '_priority_payload',
    '_impact_for_item',
    '_template_ids_for_item',
    '_action_templates_for_item',
    '_primary_template_for_item',
    '_action_payload_for_template',
    '_execution_payload',
    'create_decision_for_item',
    'select_item_option',
    'get_item_impact',
    'record_item_step',
    'update_item_control',
    'create_item_lesson',
    'apply_item_lesson',
    '_resolve_template',
    'action_preview',
    'action_dry_run',
    'run_auto_item',
    '_confirmed_for_execute',
    '_supports_transactional_acquire',
    '_record_writeback_audit_event',
    '_record_execute_block',
    '_existing_executed_writeback',
    '_execute_internal_followup_task',
    '_execute_internal_followup_task_tx',
    '_external_action_data',
    '_execute_external_writeback',
    'execute_item',
    'create_decision_for_anomaly',
    'approve_item',
    'approve_anomaly',
    'dismiss_item',
    'reopen_item',
    '_snoozed_until_from_body',
    '_operate_alert',
    'acknowledge_alert',
    'snooze_alert',
    'assign_alert',
    'mark_alert_false_positive'
)
