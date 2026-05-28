from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable

import httpx
from fastapi import HTTPException

from app.security import get_internal_api_key
from app.services import audit_service, auth
from app.services.security_context import build_security_context, rls_user_context


REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500").rstrip("/")
logger = logging.getLogger(__name__)

ACTIVE_INSTALLATION_STATUSES = {"ready", "active"}
CONTROL_ROOM_REFRESH_INTERVAL_SECONDS = 30
TERMINAL_ITEM_STATUSES = {"approved", "dismissed", "resolved"}
ITEM_STATUSES = {"open", "in_review", "decision_created", "approved", "dismissed", "resolved"}
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
ALERT_SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
OMEGA_STEPS = [
    {"id": "signals", "label": "Senales"},
    {"id": "investigation", "label": "Investigacion"},
    {"id": "options", "label": "Opciones"},
    {"id": "decision", "label": "Decision"},
    {"id": "execution", "label": "Ejecucion"},
    {"id": "control", "label": "Control"},
    {"id": "lessons", "label": "Lecciones aprendidas"},
]

EXECUTION_STATUSES = {
    "not_started",
    "preview_generated",
    "dry_run_validated",
    "blocked",
    "failed",
    "executed",
}
CONTROL_ITEM_STATUSES = {"open", "in_progress", "closed", "blocked"}
CONTROL_ITEM_STATUS_LABELS = {
    "open": "abierto",
    "in_progress": "en seguimiento",
    "closed": "cerrado",
    "blocked": "bloqueado",
}

ACTIVITY_LABELS = {
    "signals_opened": "Senal abierta",
    "investigation_reviewed": "Investigacion revisada",
    "options_reviewed": "Opciones revisadas",
    "decision_reviewed": "Decision revisada",
    "execution_reviewed": "Ejecucion revisada",
    "control_checked": "Control confirmado",
    "control_updated": "Control actualizado",
    "option_selected": "Opcion seleccionada",
    "decision_created": "Decision creada",
    "action_preview": "Preview generado",
    "action_dry_run": "Dry-run validado",
    "action_blocked": "Ejecucion bloqueada",
    "auto_run_completed": "Modo automatico completado",
    "approved": "Aprobacion registrada",
    "lesson_recorded": "Leccion registrada",
    "lesson_applied": "Leccion aplicada",
    "dismissed": "Item descartado",
    "reopened": "Item reabierto",
    "alert_acknowledged": "Alerta reconocida",
    "alert_snoozed": "Alerta pospuesta",
    "alert_assigned": "Alerta asignada",
    "alert_false_positive": "Falso positivo cerrado",
}

OMEGA_STEP_EVENT_TYPES = {
    "signals": "signals_opened",
    "investigation": "investigation_reviewed",
    "options": "options_reviewed",
    "decision": "decision_reviewed",
    "execution": "execution_reviewed",
    "control": "control_checked",
    "lessons": "lesson_recorded",
}

ACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "restore_data_source": {
        "template_id": "restore_data_source",
        "cartridge_id": "platform",
        "label": "Restaurar fuente de datos",
        "description": "Valida instalacion, credenciales, materializacion y scope tenant/workspace.",
        "action_kind": "data_recovery",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "create_followup_task": {
        "template_id": "create_followup_task",
        "cartridge_id": "platform",
        "label": "Crear seguimiento operativo",
        "description": "Genera una tarea auditada para responsable operativo.",
        "action_kind": "followup_task",
        "risk_level": "low",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "request_owner_review": {
        "template_id": "request_owner_review",
        "cartridge_id": "platform",
        "label": "Solicitar revision de owner",
        "description": "Prepara solicitud de revision humana con evidencia y SQL.",
        "action_kind": "owner_review",
        "risk_level": "low",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_replicon_adjustment": {
        "template_id": "prepare_replicon_adjustment",
        "cartridge_id": "replicon",
        "label": "Preparar ajuste Replicon",
        "description": "Construye payload seguro para revisar billing, timesheet o asignacion en Replicon.",
        "action_kind": "replicon_adjustment",
        "risk_level": "high",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_billing_review": {
        "template_id": "prepare_billing_review",
        "cartridge_id": "replicon",
        "label": "Preparar revision de facturacion",
        "description": "Construye evidencia para validar WIP, margen, horas y facturacion.",
        "action_kind": "billing_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_sap_review": {
        "template_id": "prepare_sap_review",
        "cartridge_id": "sap_s4hana",
        "label": "Preparar revision SAP",
        "description": "Construye payload de revision para revenue, backlog, compras o maestro S/4.",
        "action_kind": "sap_review",
        "risk_level": "high",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_s4_revenue_review": {
        "template_id": "prepare_s4_revenue_review",
        "cartridge_id": "sap_s4hana",
        "label": "Preparar revision comercial S/4",
        "description": "Prepara evidencia de revenue, backlog, pedido y owner comercial.",
        "action_kind": "s4_revenue_review",
        "risk_level": "high",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_s4_business_partner_review": {
        "template_id": "prepare_s4_business_partner_review",
        "cartridge_id": "sap_s4hana",
        "label": "Preparar revision de business partner",
        "description": "Prepara evidencia de maestro BP, datos fiscales, direccion y bloqueo preventivo.",
        "action_kind": "s4_business_partner_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_s4_procurement_review": {
        "template_id": "prepare_s4_procurement_review",
        "cartridge_id": "sap_s4hana",
        "label": "Preparar revision de compras S/4",
        "description": "Prepara evidencia de proveedor, gasto, contrato y aprobaciones.",
        "action_kind": "s4_procurement_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_hcm_access_review": {
        "template_id": "prepare_hcm_access_review",
        "cartridge_id": "sap_hcm",
        "label": "Preparar revision HCM acceso/nomina",
        "description": "Prepara baja, bloqueo de usuario, evidencia de posicion y posible cola de nomina.",
        "action_kind": "hcm_access_review",
        "risk_level": "high",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_hcm_org_review": {
        "template_id": "prepare_hcm_org_review",
        "cartridge_id": "sap_hcm",
        "label": "Preparar revision organizacional HCM",
        "description": "Prepara evidencia de centro de costo, posicion, jefe y estructura.",
        "action_kind": "hcm_org_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_successfactors_review": {
        "template_id": "prepare_successfactors_review",
        "cartridge_id": "sap_successfactors",
        "label": "Preparar revision SuccessFactors",
        "description": "Prepara evidencia de empleado, manager, departamento, job code y aprobador.",
        "action_kind": "successfactors_employee_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_successfactors_recruiting_review": {
        "template_id": "prepare_successfactors_recruiting_review",
        "cartridge_id": "sap_successfactors",
        "label": "Preparar revision recruiting SF",
        "description": "Prepara evidencia de requisicion, vacante, etapa y owner.",
        "action_kind": "successfactors_recruiting_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
}


@dataclass(frozen=True)
class ControlRoomSource:
    dataset: str
    cartridge: str
    domain: str
    module_label: str
    entity_kind: str
    entity_id_field: str
    entity_label_field: str
    kind: str = "anomaly"
    normalizer: str = "standard_anomaly"
    module_id: str | None = None

    @property
    def visible_module_id(self) -> str:
        return self.module_id or self.cartridge


@dataclass(frozen=True)
class ControlRoomModule:
    cartridge: str
    label: str
    domain: str
    accent: str
    sources: tuple[ControlRoomSource, ...] = ()
    operational: bool = False
    module_id: str | None = None
    description: str = ""

    @property
    def visible_id(self) -> str:
        return self.module_id or self.cartridge


MODULES: tuple[ControlRoomModule, ...] = (
    ControlRoomModule(
        cartridge="sap_hcm",
        label="Personal",
        domain="Recursos Humanos",
        accent="#7c3aed",
        sources=(
            ControlRoomSource(
                dataset="employees_anomalies",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Personal",
                entity_kind="Empleado",
                entity_id_field="pernr",
                entity_label_field="full_name",
                module_id="sap_hcm",
            ),
            ControlRoomSource(
                dataset="headcount_by_department",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Personal",
                entity_kind="Departamento",
                entity_id_field="department",
                entity_label_field="department",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm",
            ),
            ControlRoomSource(
                dataset="headcount_by_costcenter",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Personal",
                entity_kind="Centro de costo",
                entity_id_field="cost_center",
                entity_label_field="cost_center",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm",
            ),
        ),
        module_id="sap_hcm",
        description="Calidad, composicion y maestros de personal SAP HCM.",
    ),
    ControlRoomModule(
        cartridge="sap_hcm",
        label="Nomina",
        domain="Nomina",
        accent="#ef4444",
        sources=(
            ControlRoomSource(
                dataset="workforce_cost_monthly",
                cartridge="sap_hcm",
                domain="Nomina",
                module_label="Nomina",
                entity_kind="Centro de costo",
                entity_id_field="cost_center",
                entity_label_field="cost_center",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm_payroll",
            ),
        ),
        module_id="sap_hcm_payroll",
        description="Costos de fuerza laboral y senales base para control de nomina.",
    ),
    ControlRoomModule(
        cartridge="sap_hcm",
        label="Ausencias",
        domain="Recursos Humanos",
        accent="#a855f7",
        sources=(
            ControlRoomSource(
                dataset="absence_by_type_and_month",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Ausencias",
                entity_kind="Tipo de ausencia",
                entity_id_field="absence_type",
                entity_label_field="absence_type",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm_absences",
            ),
            ControlRoomSource(
                dataset="absence_balance_by_employee",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Ausencias",
                entity_kind="Empleado",
                entity_id_field="pernr",
                entity_label_field="pernr",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm_absences",
            ),
        ),
        module_id="sap_hcm_absences",
        description="Tendencias y saldos de ausentismo para HR Ops.",
    ),
    ControlRoomModule(
        cartridge="sap_hcm",
        label="Estructura Org",
        domain="Recursos Humanos",
        accent="#6d28d9",
        sources=(
            ControlRoomSource(
                dataset="manager_hierarchy",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Estructura Org",
                entity_kind="Manager",
                entity_id_field="manager_pernr",
                entity_label_field="manager_name",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm_org",
            ),
            ControlRoomSource(
                dataset="headcount_by_position_type",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Estructura Org",
                entity_kind="Posicion",
                entity_id_field="position_type",
                entity_label_field="position_type",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_hcm_org",
            ),
        ),
        module_id="sap_hcm_org",
        description="Jerarquia, span de control y estructura organizacional SAP HCM.",
    ),
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Employee Central",
        domain="Recursos Humanos",
        accent="#8b5cf6",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_employees_anomalies",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Employee Central",
                entity_kind="Empleado",
                entity_id_field="user_id",
                entity_label_field="full_name",
                module_id="sap_successfactors",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_headcount_by_department",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Employee Central",
                entity_kind="Departamento",
                entity_id_field="department",
                entity_label_field="department",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_turnover_by_period",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Employee Central",
                entity_kind="Periodo",
                entity_id_field="period",
                entity_label_field="period",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors",
            ),
        ),
        module_id="sap_successfactors",
        description="Employee Central, headcount, rotacion y calidad de datos.",
    ),
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Reclutamiento",
        domain="Recursos Humanos",
        accent="#a78bfa",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_recruitment_funnel",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Reclutamiento",
                entity_kind="Requisicion",
                entity_id_field="job_req_id",
                entity_label_field="job_req_id",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_recruiting",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_recruitment_pipeline",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Reclutamiento",
                entity_kind="Pipeline",
                entity_id_field="job_req_id",
                entity_label_field="job_req_id",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_recruiting",
            ),
        ),
        module_id="sap_successfactors_recruiting",
        description="Embudo, requisiciones y senales de cobertura de vacantes.",
    ),
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Desempeno",
        domain="Recursos Humanos",
        accent="#7c3aed",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_compensation_distribution",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Desempeno",
                entity_kind="Grupo",
                entity_id_field="department",
                entity_label_field="department",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_performance",
            ),
        ),
        module_id="sap_successfactors_performance",
        description="Compensacion disponible y senales relacionadas con desempeno.",
    ),
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Estructura Org",
        domain="Recursos Humanos",
        accent="#6d28d9",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_manager_hierarchy",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Estructura Org",
                entity_kind="Manager",
                entity_id_field="manager_id",
                entity_label_field="manager_name",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_org",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_org_structure",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Estructura Org",
                entity_kind="Unidad",
                entity_id_field="org_unit",
                entity_label_field="org_unit",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_org",
            ),
        ),
        module_id="sap_successfactors_org",
        description="Jerarquia, unidades y estructura organizacional SuccessFactors.",
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="ERP Core",
        domain="Finanzas",
        accent="#10b981",
        sources=(
            ControlRoomSource(
                dataset="business_partner_anomalies",
                cartridge="sap_s4hana",
                domain="Finanzas",
                module_label="ERP Core",
                entity_kind="Business Partner",
                entity_id_field="business_partner",
                entity_label_field="full_name",
                module_id="sap_s4hana",
            ),
            ControlRoomSource(
                dataset="gl_balance_by_account",
                cartridge="sap_s4hana",
                domain="Finanzas",
                module_label="ERP Core",
                entity_kind="Cuenta",
                entity_id_field="gl_account",
                entity_label_field="gl_account",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_s4hana",
            ),
        ),
        module_id="sap_s4hana",
        description="Maestros financieros, business partners y balance base S/4HANA.",
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="Ventas",
        domain="Ventas",
        accent="#8b5cf6",
        sources=(
            ControlRoomSource(
                dataset="revenue_by_customer",
                cartridge="sap_s4hana",
                domain="Ventas",
                module_label="Ventas",
                entity_kind="Cliente",
                entity_id_field="customer_code",
                entity_label_field="customer_code",
                kind="control_item",
                normalizer="s4_revenue",
                module_id="sap_s4hana_sales",
            ),
            ControlRoomSource(
                dataset="open_sales_orders",
                cartridge="sap_s4hana",
                domain="Ventas",
                module_label="Ventas",
                entity_kind="Cliente",
                entity_id_field="customer_code",
                entity_label_field="customer_code",
                kind="control_item",
                normalizer="s4_backlog",
                module_id="sap_s4hana_sales",
            ),
        ),
        module_id="sap_s4hana_sales",
        description="Revenue, backlog y senales comerciales S/4HANA.",
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="Compras",
        domain="Compras",
        accent="#f59e0b",
        sources=(
            ControlRoomSource(
                dataset="purchase_spend_by_supplier",
                cartridge="sap_s4hana",
                domain="Compras",
                module_label="Compras",
                entity_kind="Proveedor",
                entity_id_field="supplier_code",
                entity_label_field="supplier_code",
                kind="control_item",
                normalizer="s4_supplier_spend",
                module_id="sap_s4hana_procurement",
            ),
        ),
        module_id="sap_s4hana_procurement",
        description="Gasto por proveedor y controles de compras.",
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="Cuentas por Cobrar",
        domain="Finanzas",
        accent="#14b8a6",
        sources=(
            ControlRoomSource(
                dataset="overdue_billing",
                cartridge="sap_s4hana",
                domain="Finanzas",
                module_label="Cuentas por Cobrar",
                entity_kind="Factura",
                entity_id_field="billing_document",
                entity_label_field="billing_document",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_s4hana_ar",
            ),
        ),
        module_id="sap_s4hana_ar",
        description="Cartera vencida, facturacion y aging comercial.",
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="Presupuestos",
        domain="Presupuestos",
        accent="#0891b2",
        sources=(
            ControlRoomSource(
                dataset="cost_center_expense",
                cartridge="sap_s4hana",
                domain="Presupuestos",
                module_label="Presupuestos",
                entity_kind="Centro de costo",
                entity_id_field="cost_center",
                entity_label_field="cost_center",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_s4hana_budget",
            ),
        ),
        module_id="sap_s4hana_budget",
        description="Gasto por centro de costo como base para control presupuestal.",
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="Inventario",
        domain="Operacion",
        accent="#22c55e",
        sources=(
            ControlRoomSource(
                dataset="inventory_movement_summary",
                cartridge="sap_s4hana",
                domain="Operacion",
                module_label="Inventario",
                entity_kind="Material",
                entity_id_field="material",
                entity_label_field="material",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_s4hana_inventory",
            ),
        ),
        module_id="sap_s4hana_inventory",
        description="Movimientos de inventario y senales de operacion S/4HANA.",
    ),
    ControlRoomModule(
        cartridge="replicon",
        label="Servicios Profesionales",
        domain="Operacion",
        accent="#0ea5e9",
        sources=(
            ControlRoomSource(
                dataset="consultor_asignacion",
                cartridge="replicon",
                domain="Operacion",
                module_label="Asignacion",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="control_item",
                normalizer="replicon_allocation",
                module_id="replicon",
            ),
            ControlRoomSource(
                dataset="consultor_timesheet_semanal",
                cartridge="replicon",
                domain="Operacion",
                module_label="Timesheets",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="control_item",
                normalizer="replicon_timesheet",
                module_id="replicon",
            ),
            ControlRoomSource(
                dataset="project_progress_history",
                cartridge="replicon",
                domain="Operacion",
                module_label="Servicios Profesionales",
                entity_kind="Proyecto",
                entity_id_field="proyecto",
                entity_label_field="project_name",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="replicon",
            ),
        ),
        module_id="replicon",
        description="Asignacion, timesheets y delivery de servicios profesionales.",
    ),
    ControlRoomModule(
        cartridge="replicon",
        label="Margen y Facturacion",
        domain="Finanzas",
        accent="#0ea5e9",
        sources=(
            ControlRoomSource(
                dataset="pnl_mensual",
                cartridge="replicon",
                domain="Finanzas",
                module_label="Margen y Facturacion",
                entity_kind="Proyecto",
                entity_id_field="proyecto",
                entity_label_field="project_name",
                kind="control_item",
                normalizer="replicon_pnl",
                module_id="replicon_finance",
            ),
            ControlRoomSource(
                dataset="pnl_detalle_consultor",
                cartridge="replicon",
                domain="Finanzas",
                module_label="Margen y Facturacion",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="replicon_finance",
            ),
            ControlRoomSource(
                dataset="costo_consultor_mensual",
                cartridge="replicon",
                domain="Finanzas",
                module_label="Margen y Facturacion",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="replicon_finance",
            ),
        ),
        module_id="replicon_finance",
        description="P&L, WIP, costo y facturacion de servicios.",
    ),
    ControlRoomModule(
        cartridge="replicon",
        label="Skills y Staffing",
        domain="Recursos Humanos",
        accent="#38bdf8",
        sources=(
            ControlRoomSource(
                dataset="analytic_skill_gap_by_manager",
                cartridge="replicon",
                domain="Recursos Humanos",
                module_label="Skills y Staffing",
                entity_kind="Manager",
                entity_id_field="manager_name",
                entity_label_field="manager_name",
                kind="control_item",
                normalizer="replicon_skill_gap",
                module_id="replicon_skills",
            ),
            ControlRoomSource(
                dataset="fact_empleado_skills",
                cartridge="replicon",
                domain="Recursos Humanos",
                module_label="Skills y Staffing",
                entity_kind="Empleado",
                entity_id_field="empleado",
                entity_label_field="empleado",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="replicon_skills",
            ),
        ),
        module_id="replicon_skills",
        description="Brechas de skill, staffing y capacidad consultiva.",
    ),
    ControlRoomModule(
        cartridge="platform",
        label="Plataforma",
        domain="Operacion",
        accent="#64748b",
        operational=True,
        module_id="platform",
        description="Salud operativa de pipelines, fuentes y plataforma.",
    ),
)

DOMAIN_ORDER = ["Recursos Humanos", "Nomina", "Finanzas", "Presupuestos", "Compras", "Ventas", "Operacion"]
DOMAIN_ACCENTS = {
    "Recursos Humanos": "#7c3aed",
    "Nomina": "#ef4444",
    "Finanzas": "#10b981",
    "Presupuestos": "#0891b2",
    "Compras": "#f59e0b",
    "Ventas": "#8b5cf6",
    "Operacion": "#64748b",
}

ANOMALY_COPY: dict[str, dict[str, str]] = {
    "missing_cost_center": {
        "title": "Empleado activo sin centro de costo",
        "recommendation": "Asignar centro de costo, validar owner financiero y registrar seguimiento en una decision compartida.",
        "root_cause": "Dato maestro incompleto en la estructura organizacional.",
    },
    "missing_position": {
        "title": "Empleado activo sin posicion",
        "recommendation": "Corregir asignacion organizacional y revisar que la posicion exista antes del siguiente corte operativo.",
        "root_cause": "La asignacion de posicion no llego completa desde el origen.",
    },
    "terminated_but_active": {
        "title": "Empleado dado de baja sigue activo",
        "recommendation": "Validar ultima accion de personal, bloquear accesos si aplica y abrir remediacion con responsable de HR Ops.",
        "root_cause": "Baja laboral no sincronizada con estado operativo activo.",
    },
    "missing_address": {
        "title": "Business Partner sin direccion",
        "recommendation": "Completar datos maestros del partner antes de nuevas ordenes, facturacion o analisis de credito.",
        "root_cause": "Maestro financiero incompleto para operaciones comerciales.",
    },
    "duplicate_name": {
        "title": "Business Partners con nombre duplicado",
        "recommendation": "Revisar duplicidad de maestro, consolidar registros o marcar excepcion aprobada.",
        "root_cause": "Posible alta duplicada o normalizacion insuficiente del nombre legal.",
    },
    "missing_department": {
        "title": "Empleado sin departamento",
        "recommendation": "Asignar departamento valido y verificar reglas de reporting para evitar analisis incompleto.",
        "root_cause": "Employee Central no trae unidad organizacional completa.",
    },
    "missing_manager": {
        "title": "Empleado sin manager",
        "recommendation": "Confirmar si es excepcion ejecutiva; si no lo es, asignar manager y registrar control de seguimiento.",
        "root_cause": "La cadena de mando esta incompleta o no fue replicada.",
    },
    "invalid_job_code": {
        "title": "Job code invalido",
        "recommendation": "Corregir job code contra el catalogo FOJobCode y revisar impactos en compensacion/reporting.",
        "root_cause": "Codigo de puesto no existe o no esta vigente en el catalogo de referencia.",
    },
}


DatasetFetcher = Callable[[str, dict | None, int], Awaitable[list[dict[str, Any]]]]
ThresholdMap = dict[tuple[str, str, str], dict[str, Any]]


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _internal_headers(server: str) -> dict[str, str]:
    pair = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        key = pair
    elif _is_production_env():
        raise RuntimeError(f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server}; legacy fallback disabled in production")
    else:
        key = get_internal_api_key()
    return {"x-api-key": key, "x-internal-service": "console"}


def _mcp_payload(tool: str, args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = build_security_context(user)
    return payload


async def query_dataset_rows(dataset: str, user: dict | None, limit: int = 1000) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(headers=_internal_headers("REFINEMENT"), timeout=45) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "query_dataset",
                {"name": dataset, "limit": limit, "user_context": rls_user_context(user)},
                user,
            ),
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"dataset unavailable: {dataset}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error") and "data" not in payload:
        detail = payload.get("error") or payload.get("code") or f"dataset unavailable: {dataset}"
        raise HTTPException(503, str(detail))
    data = payload.get("data", payload.get("result", payload))
    if isinstance(data, dict):
        if data.get("error") and "data" not in data:
            detail = data.get("error") or data.get("code") or f"dataset unavailable: {dataset}"
            raise HTTPException(503, str(detail))
        data = data.get("data", [])
    if not isinstance(data, list):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


def _details(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except json.JSONDecodeError:
            return {"raw": value}
    return {}


def _encode_id(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _severity(value: Any) -> str:
    sev = str(value or "medium").strip().lower()
    if sev in {"critica", "critical", "critico"}:
        return "critical"
    if sev in {"alta", "high"}:
        return "high"
    if sev in {"media", "moderada", "medium"}:
        return "medium"
    if sev in {"baja", "low", "ok", "satisfactoria"}:
        return "low"
    return "medium"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _copy_for(anomaly_type: str) -> dict[str, str]:
    return ANOMALY_COPY.get(
        anomaly_type,
        {
            "title": anomaly_type.replace("_", " ").strip().title() or "Senal operativa",
            "recommendation": "Revisar el registro, asignar responsable y documentar la decision tomada.",
            "root_cause": "La fuente genero una senal fuera de regla que requiere investigacion.",
        },
    )


def _workspace_scope(user: dict | None) -> tuple[str | None, str]:
    workspace_id = (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id")
    tenant_id = (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id")
    if not workspace_id:
        raise HTTPException(400, "active workspace is required")
    return (str(tenant_id) if tenant_id else None), str(workspace_id)


def _workspace_id(user: dict | None) -> str:
    return _workspace_scope(user)[1]


def _module_by_cartridge() -> dict[str, ControlRoomModule]:
    return {module.cartridge: module for module in MODULES}


def _module_by_visible_id() -> dict[str, ControlRoomModule]:
    return {module.visible_id: module for module in MODULES}


def _all_sources() -> tuple[ControlRoomSource, ...]:
    sources: list[ControlRoomSource] = []
    for module in MODULES:
        sources.extend(module.sources)
    return tuple(sources)


def _allowed_from_user(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if "*" in allowed:
        return None
    return allowed if allowed else None


def _row_to_public(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _threshold_to_public(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    for key in ("warning_value", "critical_value"):
        data[key] = _num(data.get(key))
    data["currency"] = str(data.get("currency") or "USD").upper()
    data["enabled"] = bool(data.get("enabled", True))
    data["metadata"] = _details(data.get("metadata"))
    return data


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


async def _load_threshold_rows(user: dict | None, *, enabled_only: bool = True) -> list[dict[str, Any]]:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    where = ["workspace_id = $1"]
    if enabled_only:
        where.append("enabled = TRUE")
    try:
        rows = await pool.fetch(
            f"""
            SELECT id, cartridge_id, anomaly_type, metric, warning_value,
                   critical_value, currency, enabled, metadata, created_at, updated_at
              FROM control_room_thresholds
             WHERE {' AND '.join(where)}
             ORDER BY cartridge_id, anomaly_type, metric
            """,
            workspace_id,
        )
        return [_threshold_to_public(row) for row in rows]
    except Exception:
        return []


def _lesson_to_public(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    data["metadata"] = _details(data.get("metadata"))
    data["confidence"] = _num(data.get("confidence")) or 0.0
    return data


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
        rows = await pool.fetch(
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
        return [_lesson_to_public(row) for row in rows]
    except Exception:
        return []


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
    top_patterns.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("cartridge_id") or "")))
    return {
        "total": len(rows),
        "recent": rows[:5],
        "by_cartridge": by_cartridge,
        "top_patterns": top_patterns[:5],
    }


def _attach_lessons_to_items(items: list[dict[str, Any]], lesson_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []
    if not lesson_rows:
        return [
            _with_omega({**item, "related_lessons": item.get("related_lessons") or [], "lesson_count": 0})
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
        enriched.append(_with_omega({
            **item,
            "related_lessons": related[:5],
            "lesson_count": len(related),
            "learned_rules": rules[:5],
        }))
    enriched.sort(key=_status_sort_key)
    return enriched


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


def _threshold_rule(
    thresholds: ThresholdMap | None,
    cartridge_id: str,
    anomaly_type: str,
    metric: str,
) -> dict[str, Any] | None:
    if not thresholds:
        return None
    return thresholds.get((cartridge_id, anomaly_type, metric))


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
        _threshold_value(thresholds, cartridge_id, anomaly_type, metric, "critical_value", critical_default)
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


def _attach_thresholds(
    item: dict[str, Any],
    thresholds_applied: list[dict[str, Any]],
    state: str,
) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    item["thresholds_applied"] = thresholds_applied
    item["threshold_state"] = state
    item["details"] = {**details, "thresholds": thresholds_applied, "threshold_state": state}
    return item


async def _installed_cartridges(user: dict | None) -> list[dict[str, Any]]:
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    try:
        rows = await pool.fetch(
            """
            SELECT
                ci.cartridge_id,
                ci.status AS installation_status,
                ci.current_step,
                ci.error_message,
                ci.ready_at,
                COALESCE(mp.name, c.name, ci.cartridge_id) AS label,
                COALESCE(c.category, 'cartridge') AS category
            FROM cartridge_installations ci
            LEFT JOIN cartridges c ON c.id = ci.cartridge_id
            LEFT JOIN marketplace_products mp ON mp.cartridge_id = ci.cartridge_id
            WHERE ci.tenant_id = $1
              AND ci.workspace_id = $2
              AND NOT EXISTS (
                  SELECT 1
                    FROM user_cartridge_overrides uco
                   WHERE uco.tenant_id = ci.tenant_id
                     AND uco.workspace_id = ci.workspace_id
                     AND uco.cartridge_id = ci.cartridge_id
                     AND uco.user_id = $3
                     AND uco.mode = 'deny'
              )
            ORDER BY lower(COALESCE(mp.name, c.name, ci.cartridge_id))
            """,
            tenant_id,
            workspace_id,
            (user or {}).get("id"),
        )
        return [_row_to_public(row) for row in rows]
    except Exception as exc:
        logger.warning("control_room catalog unavailable; modules marked unavailable", exc_info=exc)
        allowed = _allowed_from_user(user)
        modules = MODULES if allowed is None else [module for module in MODULES if module.cartridge in allowed]
        return [
            {
                "cartridge_id": module.cartridge,
                "installation_status": "unavailable",
                "current_step": "catalog_unavailable",
                "label": module.label,
                "category": "platform" if module.operational else "cartridge",
                "error_message": "catalog unavailable; connector readiness not assumed",
            }
            for module in modules
        ]


async def _fetch_source(
    source: ControlRoomSource,
    user: dict | None,
    fetcher: DatasetFetcher,
    limit_per_source: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checked_at = datetime.now(UTC).isoformat()
    try:
        rows = await fetcher(source.dataset, user, limit_per_source)
    except HTTPException as exc:
        status = "missing" if exc.status_code == 404 else "unavailable"
        return [], {
            "dataset": source.dataset,
            "cartridge": source.cartridge,
            "connector_id": source.cartridge,
            "module_id": source.visible_module_id,
            "domain": source.domain,
            "module": source.module_label,
            "status": status,
            "error": str(exc.detail),
            "count": 0,
            "checked_at": checked_at,
        }
    except Exception as exc:
        return [], {
            "dataset": source.dataset,
            "cartridge": source.cartridge,
            "connector_id": source.cartridge,
            "module_id": source.visible_module_id,
            "domain": source.domain,
            "module": source.module_label,
            "status": "unavailable",
            "error": str(exc),
            "count": 0,
            "checked_at": checked_at,
        }

    if rows and source.normalizer == "standard_anomaly":
        missing_contract = all(
            source.entity_id_field not in row and source.entity_label_field not in row
            for row in rows
        )
        if missing_contract:
            return [], {
                "dataset": source.dataset,
                "cartridge": source.cartridge,
                "connector_id": source.cartridge,
                "module_id": source.visible_module_id,
                "domain": source.domain,
                "module": source.module_label,
                "status": "invalid_schema",
                "error": f"missing expected fields: {source.entity_id_field}/{source.entity_label_field}",
                "count": len(rows),
                "checked_at": checked_at,
            }
    return rows, {
        "dataset": source.dataset,
        "cartridge": source.cartridge,
        "connector_id": source.cartridge,
        "module_id": source.visible_module_id,
        "domain": source.domain,
        "module": source.module_label,
        "status": "empty" if not rows else "ok",
        "count": len(rows),
        "checked_at": checked_at,
    }


def _base_item(source: ControlRoomSource, row: dict[str, Any], item_type: str, entity_id: str, label: str) -> dict[str, Any]:
    severity = _severity(row.get("severity"))
    details = _details(row.get("details"))
    detected_at = str(row.get("detected_at") or row.get("mes") or row.get("semana") or "")
    technical_sql = (
        f"SELECT * FROM {source.dataset} "
        f"WHERE {source.entity_id_field} = '{str(entity_id or label).replace("'", "''")}' "
        f"LIMIT 50"
    )
    return {
        "id": _encode_id({
            "dataset": source.dataset,
            "type": item_type,
            "entity": entity_id or label,
        }),
        "kind": source.kind,
        "domain": source.domain,
        "module": source.module_label,
        "module_id": source.visible_module_id,
        "cartridge": source.cartridge,
        "connector_id": source.cartridge,
        "source_dataset": source.dataset,
        "entity_kind": source.entity_kind,
        "entity_id": entity_id,
        "entity_label": label,
        "anomaly_type": item_type,
        "severity": severity,
        "severity_weight": SEVERITY_WEIGHT[severity],
        "detected_at": detected_at,
        "details": details,
        "sql": technical_sql,
        "status": "open",
        "decision_id": None,
        "thresholds_applied": [],
        "threshold_state": "default",
    }


def _normalize_standard_anomaly(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any]:
    anomaly_type = str(row.get("anomaly_type") or "unknown").strip()
    entity_id = str(row.get(source.entity_id_field) or "").strip()
    label = str(row.get(source.entity_label_field) or entity_id or "Sin nombre").strip()
    details = _details(row.get("details"))
    copy = _copy_for(anomaly_type)
    item = _base_item(source, row, anomaly_type, entity_id, label)
    reason = str(details.get("reason") or "").strip()
    item.update({
        "title": copy["title"],
        "description": reason or f"{source.entity_kind} {label} requiere revision operativa.",
        "recommendation": copy["recommendation"],
        "root_cause": str(details.get("root_cause") or copy["root_cause"]),
        "impact": str(details.get("impact") or f"Riesgo operativo en {source.domain}."),
    })
    return item


def _normalize_replicon_allocation(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    pct = _num(row.get("pct_asignacion"))
    over_warning = _threshold_value(thresholds, "replicon", "over_allocation", "pct_asignacion", "warning_value", 110)
    over_critical = _threshold_value(thresholds, "replicon", "over_allocation", "pct_asignacion", "critical_value", 130)
    under_warning = _threshold_value(thresholds, "replicon", "under_allocation", "pct_asignacion", "warning_value", 40)
    under_critical = _threshold_value(thresholds, "replicon", "under_allocation", "pct_asignacion", "critical_value", 20)
    if pct is None or under_warning <= pct <= over_warning:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(row.get("proyecto") or row.get("project_name") or "Sin proyecto").strip()
    item_type = "over_allocation" if pct > 110 else "under_allocation"
    if pct > over_warning:
        item_type = "over_allocation"
        threshold_state = "critical" if pct >= over_critical else "warning"
        severity = "high" if threshold_state == "critical" else "medium"
        threshold_refs = [_threshold_ref(
            thresholds,
            "replicon",
            "over_allocation",
            "pct_asignacion",
            warning_default=110,
            critical_default=130,
            currency="PCT",
        )]
    else:
        item_type = "under_allocation"
        threshold_state = "critical" if pct <= under_critical else "warning"
        severity = "high" if threshold_state == "critical" else "medium"
        threshold_refs = [_threshold_ref(
            thresholds,
            "replicon",
            "under_allocation",
            "pct_asignacion",
            warning_default=40,
            critical_default=20,
            currency="PCT",
        )]
    item = _base_item(source, {**row, "severity": severity}, item_type, f"{consultor}:{proyecto}", consultor)
    direction = "sobreasignacion" if item_type == "over_allocation" else "subasignacion"
    item.update({
        "title": f"{consultor} con {direction} operativa",
        "description": f"{consultor} registra {pct:.1f}% de asignacion en {proyecto}.",
        "recommendation": "Rebalancear carga con el revenue manager y documentar excepcion si la asignacion es temporal.",
        "root_cause": "Diferencia entre capacidad mensual esperada y horas asignadas.",
        "impact": "Riesgo de capacidad, margen o entrega del proyecto.",
        "details": {**item["details"], **row},
    })
    return _attach_thresholds(item, threshold_refs, threshold_state)


def _normalize_replicon_timesheet(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    total = _num(row.get("horas_total")) or 0
    no_billable = _num(row.get("horas_no_facturables")) or 0
    if total <= 0:
        return None
    ratio = no_billable / total
    warning_ratio = _threshold_value(
        thresholds,
        "replicon",
        "non_billable_ratio",
        "horas_no_facturables_ratio",
        "warning_value",
        0.35,
    )
    critical_ratio = _threshold_value(
        thresholds,
        "replicon",
        "non_billable_ratio",
        "horas_no_facturables_ratio",
        "critical_value",
        0.55,
    )
    if ratio < warning_ratio:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(row.get("proyecto") or row.get("project_name") or "Sin proyecto").strip()
    threshold_state = "critical" if ratio >= critical_ratio else "warning"
    severity = "high" if threshold_state == "critical" else "medium"
    item = _base_item(source, {**row, "severity": severity}, "non_billable_ratio", f"{consultor}:{proyecto}", consultor)
    item.update({
        "title": "Horas no facturables fuera de rango",
        "description": f"{consultor} tiene {ratio:.0%} de horas no facturables en {proyecto}.",
        "recommendation": "Validar causa con PM/RM, reclasificar si procede y ajustar forecast de margen.",
        "root_cause": "Registro de tiempo no facturable alto frente al total semanal.",
        "impact": "Puede erosionar margen y ocultar demanda no planificada.",
        "details": {**item["details"], **row},
    })
    return _attach_thresholds(
        item,
        [_threshold_ref(
            thresholds,
            "replicon",
            "non_billable_ratio",
            "horas_no_facturables_ratio",
            warning_default=0.35,
            critical_default=0.55,
            currency="PCT",
        )],
        threshold_state,
    )


def _normalize_replicon_pnl(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    margin = _num(row.get("margen_bruto_pct"))
    wip = _num(row.get("wip_usd")) or 0
    margin_warning = _threshold_value(thresholds, "replicon", "low_margin", "margen_bruto_pct", "warning_value", 20)
    margin_critical = _threshold_value(thresholds, "replicon", "low_margin", "margen_bruto_pct", "critical_value", 0)
    wip_warning = _threshold_value(thresholds, "replicon", "wip_variance", "wip_usd", "warning_value", 5000)
    wip_critical = _threshold_value(thresholds, "replicon", "wip_variance", "wip_usd", "critical_value", 25000)
    margin_breached = margin is not None and margin < margin_warning
    wip_breached = abs(wip) >= wip_warning
    if not margin_breached and not wip_breached:
        return None
    proyecto = str(row.get("proyecto") or row.get("project_name") or "Sin proyecto").strip()
    manager = str(row.get("revenue_manager") or "Sin RM").strip()
    if margin is not None and margin < margin_critical:
        severity = "critical"
        threshold_state = "critical"
    elif abs(wip) >= wip_critical:
        severity = "critical"
        threshold_state = "critical"
    elif margin_breached:
        severity = "high"
        threshold_state = "warning"
    else:
        severity = "medium"
        threshold_state = "warning"
    item_type = "low_margin" if margin_breached else "wip_variance"
    item = _base_item(source, {**row, "severity": severity}, item_type, proyecto, str(row.get("project_name") or proyecto))
    item.update({
        "title": "Proyecto con margen o WIP fuera de control",
        "description": f"{proyecto} esta bajo {manager}; margen={margin if margin is not None else 'N/D'}%, WIP={wip:,.0f} USD.",
        "recommendation": "Revisar revenue, facturacion, costo hundido y compromiso de remediacion con finanzas.",
        "root_cause": "Desviacion entre ingreso reconocido, facturacion y costo total.",
        "impact": "Riesgo financiero directo en margen, cash flow o forecast.",
        "details": {**item["details"], **row},
    })
    refs = []
    if margin_breached:
        refs.append(_threshold_ref(
            thresholds,
            "replicon",
            "low_margin",
            "margen_bruto_pct",
            warning_default=20,
            critical_default=0,
            currency="PCT",
        ))
    if wip_breached:
        refs.append(_threshold_ref(
            thresholds,
            "replicon",
            "wip_variance",
            "wip_usd",
            warning_default=5000,
            critical_default=25000,
        ))
    return _attach_thresholds(item, refs, threshold_state)


def _normalize_replicon_skill_gap(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    level = str(row.get("brecha_nivel") or "").strip().upper()
    if level not in {"CRITICA", "MODERADA"}:
        return None
    manager = str(row.get("manager_name") or "Sin manager").strip()
    category = str(row.get("skill_category") or "Sin categoria").strip()
    severity = "high" if level == "CRITICA" else "medium"
    item = _base_item(source, {**row, "severity": severity}, "skill_gap", f"{manager}:{category}", manager)
    item.update({
        "title": "Brecha de skills en equipo",
        "description": f"{manager} tiene brecha {level.lower()} en {category}.",
        "recommendation": "Priorizar capacitacion, mentorias o staffing alterno antes de nuevas asignaciones criticas.",
        "root_cause": "Cobertura de habilidades insuficiente para la demanda operativa.",
        "impact": "Riesgo de delivery, calidad o dependencia de pocos expertos.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_s4_revenue(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    revenue = _num(row.get("revenue"))
    warning_revenue = _threshold_value(thresholds, "sap_s4hana", "negative_revenue", "revenue", "warning_value", 0)
    critical_revenue = _threshold_value(
        thresholds,
        "sap_s4hana",
        "negative_revenue",
        "revenue",
        "critical_value",
        -100000,
    )
    if revenue is None or revenue >= warning_revenue:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    month = str(row.get("revenue_month") or row.get("mes") or "").strip()
    severity = "critical" if revenue <= critical_revenue else "high"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "negative_revenue",
        f"{customer}:{month}",
        customer,
    )
    item.update({
        "title": "Revenue negativo en ventas",
        "description": f"{customer} registra revenue negativo por {abs(revenue):,.0f} en {month or 'el periodo'}.",
        "recommendation": "Revisar notas de credito, anulaciones y conciliacion de facturacion antes del cierre.",
        "root_cause": "La facturacion neta del periodo quedo por debajo de cero.",
        "impact": "Riesgo de distorsion de revenue, forecast y margen comercial.",
        "details": {**item["details"], **row},
    })
    return _attach_thresholds(
        item,
        [_threshold_ref(
            thresholds,
            "sap_s4hana",
            "negative_revenue",
            "revenue",
            warning_default=0,
            critical_default=-100000,
        )],
        "critical" if severity == "critical" else "warning",
    )


def _normalize_s4_backlog(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    age = _num(row.get("oldest_age_days")) or 0
    open_value = _num(row.get("open_value")) or 0
    age_warning = _threshold_value(thresholds, "sap_s4hana", "aged_sales_backlog", "oldest_age_days", "warning_value", 45)
    age_critical = _threshold_value(thresholds, "sap_s4hana", "aged_sales_backlog", "oldest_age_days", "critical_value", 90)
    value_warning = _threshold_value(thresholds, "sap_s4hana", "aged_sales_backlog", "open_value", "warning_value", 50000)
    value_critical = _threshold_value(thresholds, "sap_s4hana", "aged_sales_backlog", "open_value", "critical_value", 250000)
    age_breached = age >= age_warning
    value_breached = open_value >= value_warning
    if not age_breached and not value_breached:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    threshold_state = "critical" if age >= age_critical or open_value >= value_critical else "warning"
    severity = "critical" if threshold_state == "critical" else "high"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "aged_sales_backlog",
        customer,
        customer,
    )
    item.update({
        "title": "Backlog comercial envejecido",
        "description": f"{customer} tiene pedidos abiertos por {open_value:,.0f} con antiguedad maxima de {age:.0f} dias.",
        "recommendation": "Validar bloqueo, entrega, facturacion pendiente y responsable comercial.",
        "root_cause": "Pedidos abiertos permanecen sin completar fuera del ciclo normal.",
        "impact": "Riesgo de cash flow, cumplimiento de entrega y forecast de ventas.",
        "details": {**item["details"], **row},
    })
    refs = []
    if age_breached:
        refs.append(_threshold_ref(
            thresholds,
            "sap_s4hana",
            "aged_sales_backlog",
            "oldest_age_days",
            warning_default=45,
            critical_default=90,
            currency="DAYS",
        ))
    if value_breached:
        refs.append(_threshold_ref(
            thresholds,
            "sap_s4hana",
            "aged_sales_backlog",
            "open_value",
            warning_default=50000,
            critical_default=250000,
        ))
    return _attach_thresholds(item, refs, threshold_state)


def _normalize_s4_supplier_spend(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    spend = _num(row.get("total_spend")) or 0
    warning_spend = _threshold_value(
        thresholds,
        "sap_s4hana",
        "supplier_spend_concentration",
        "total_spend",
        "warning_value",
        250000,
    )
    critical_spend = _threshold_value(
        thresholds,
        "sap_s4hana",
        "supplier_spend_concentration",
        "total_spend",
        "critical_value",
        750000,
    )
    if spend < warning_spend:
        return None
    supplier = str(row.get("supplier_code") or "Sin proveedor").strip()
    month = str(row.get("spend_month") or "").strip()
    threshold_state = "critical" if spend >= critical_spend else "warning"
    severity = "high" if threshold_state == "critical" else "medium"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "supplier_spend_concentration",
        f"{supplier}:{month}",
        supplier,
    )
    item.update({
        "title": "Gasto alto concentrado en proveedor",
        "description": f"{supplier} concentra {spend:,.0f} de gasto en {month or 'el periodo'}.",
        "recommendation": "Revisar aprobaciones, categoria, contrato vigente y comparativo contra presupuesto.",
        "root_cause": "Concentracion de gasto relevante en compras del periodo.",
        "impact": "Riesgo de sobrepresupuesto, dependencia de proveedor o control de aprobaciones.",
        "details": {**item["details"], **row},
    })
    return _attach_thresholds(
        item,
        [_threshold_ref(
            thresholds,
            "sap_s4hana",
            "supplier_spend_concentration",
            "total_spend",
            warning_default=250000,
            critical_default=750000,
        )],
        threshold_state,
    )


def _normalize_row(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    if source.normalizer == "standard_anomaly":
        return _normalize_standard_anomaly(source, row)
    if source.normalizer == "replicon_allocation":
        return _normalize_replicon_allocation(source, row, thresholds)
    if source.normalizer == "replicon_timesheet":
        return _normalize_replicon_timesheet(source, row, thresholds)
    if source.normalizer == "replicon_pnl":
        return _normalize_replicon_pnl(source, row, thresholds)
    if source.normalizer == "replicon_skill_gap":
        return _normalize_replicon_skill_gap(source, row)
    if source.normalizer == "s4_revenue":
        return _normalize_s4_revenue(source, row, thresholds)
    if source.normalizer == "s4_backlog":
        return _normalize_s4_backlog(source, row, thresholds)
    if source.normalizer == "s4_supplier_spend":
        return _normalize_s4_supplier_spend(source, row, thresholds)
    return None


def _money_sum(rows: Iterable[dict[str, Any]], field: str) -> float:
    return round(sum(_num(row.get(field)) or 0 for row in rows), 2)


def _ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 2)


def _financial_metrics(
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    pnl_rows = rows_by_dataset.get("pnl_mensual", [])
    revenue_rows = rows_by_dataset.get("revenue_by_customer", [])
    backlog_rows = rows_by_dataset.get("open_sales_orders", [])
    purchase_rows = rows_by_dataset.get("purchase_spend_by_supplier", [])
    relevant = {
        source["dataset"]: {
            "status": source["status"],
            "count": int(source.get("count") or 0),
            "domain": source.get("domain"),
            "cartridge": source.get("cartridge"),
        }
        for source in sources
        if source["dataset"] in {
            "pnl_mensual",
            "revenue_by_customer",
            "open_sales_orders",
            "purchase_spend_by_supplier",
        }
    }

    revenue_usd = _money_sum(pnl_rows, "revenue_usd")
    billed_usd = _money_sum(pnl_rows, "facturacion_mes_usd")
    wip_usd = _money_sum(pnl_rows, "wip_usd")
    cost_usd = _money_sum(pnl_rows, "costo_total")
    margin_usd = _money_sum(pnl_rows, "margen_bruto_usd")
    sales_revenue = _money_sum(revenue_rows, "revenue")
    backlog_value = _money_sum(backlog_rows, "open_value")
    purchase_spend = _money_sum(purchase_rows, "total_spend")
    open_orders = int(_money_sum(backlog_rows, "open_orders"))
    oldest_backlog_days = int(max((_num(row.get("oldest_age_days")) or 0 for row in backlog_rows), default=0))

    risk_rows: list[dict[str, Any]] = []
    for row in pnl_rows:
        margin_pct = _num(row.get("margen_bruto_pct"))
        wip = _num(row.get("wip_usd")) or 0
        if (margin_pct is not None and margin_pct < 20) or abs(wip) >= 5000:
            risk_rows.append({
                "label": str(row.get("project_name") or row.get("proyecto") or "Proyecto"),
                "owner": str(row.get("revenue_manager") or "N/D"),
                "margin_pct": margin_pct,
                "wip_usd": round(wip, 2),
                "margin_usd": round(_num(row.get("margen_bruto_usd")) or 0, 2),
            })
    risk_rows.sort(key=lambda row: (row["margin_pct"] if row["margin_pct"] is not None else 999, -abs(row["wip_usd"])))

    available = any(source["status"] == "ok" and source.get("count", 0) for source in relevant.values())
    if available:
        status = "ok"
    elif any(source["status"] in {"unavailable", "invalid_schema"} for source in relevant.values()):
        status = "unavailable"
    elif any(source["status"] == "missing" for source in relevant.values()):
        status = "missing"
    elif relevant:
        status = "empty"
    else:
        status = "not_installed"

    return {
        "status": status,
        "sources": relevant,
        "revenue_usd": revenue_usd,
        "billed_usd": billed_usd,
        "wip_usd": wip_usd,
        "cost_usd": cost_usd,
        "margin_usd": margin_usd,
        "margin_pct": _ratio(margin_usd, revenue_usd),
        "sales_revenue": sales_revenue,
        "backlog_value": backlog_value,
        "open_orders": open_orders,
        "oldest_backlog_days": oldest_backlog_days,
        "purchase_spend": purchase_spend,
        "risk_projects": risk_rows[:6],
    }


def _cycle_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {step["id"]: 0 for step in OMEGA_STEPS}
    for item in items:
        status = str(item.get("status") or "open")
        counts["signals"] += 1
        if status in {"open", "in_review"}:
            counts["investigation"] += 1
        if status == "in_review" or item.get("selected_option_id"):
            counts["options"] += 1
        if item.get("decision_id") or status in {"decision_created", "approved", "resolved"}:
            counts["decision"] += 1
        if status in {"approved", "resolved"}:
            counts["execution"] += 1
            counts["control"] += 1
            counts["lessons"] += 1
        elif status == "dismissed":
            counts["control"] += 1
    return counts


def _lessons_for_item(item: dict[str, Any]) -> list[str]:
    cleaned: list[str] = []
    for key in ("lessons", "learned_rules"):
        existing = item.get(key)
        if isinstance(existing, list):
            for rule in existing:
                text = str(rule).strip()
                if text and text not in cleaned:
                    cleaned.append(text)
    if cleaned:
        return cleaned
    return []


def _external_writeback_enabled() -> bool:
    return os.environ.get("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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


def _action_templates_for_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(ACTION_TEMPLATES[template_id]) for template_id in _template_ids_for_item(item) if template_id in ACTION_TEMPLATES]


def _primary_template_for_item(item: dict[str, Any]) -> dict[str, Any]:
    templates = _action_templates_for_item(item)
    return templates[0] if templates else dict(ACTION_TEMPLATES["request_owner_review"])


def _action_payload_for_template(item: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    action_kind = str(template.get("action_kind") or "owner_review")
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
        "writeback": {
            "enabled": False,
            "mode": "preview_dry_run_only",
        },
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


def _execution_payload(item: dict[str, Any], mode: str, template: dict[str, Any]) -> dict[str, Any]:
    impact = _impact_for_item(item)
    action_payload = _action_payload_for_template(item, template)
    return {
        "mode": mode,
        "external_writeback_enabled": _external_writeback_enabled(),
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
        "operations": [
            {
                "operation": template.get("action_kind"),
                "status": "preview" if mode == "preview" else "validated",
                "requires_approval": True,
                "external_write": False,
                "payload": action_payload,
                "evidence": {
                    "sql": item.get("sql"),
                    "recommendation": item.get("recommendation"),
                    "root_cause": item.get("root_cause"),
                },
            }
        ],
        "guardrails": {
            "human_approval_required": True,
            "writeback_blocked_by_default": True,
            "feature_flag": "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
        },
    }


def _source_state_item(source: ControlRoomSource, status: str, error: str | None = None) -> dict[str, Any] | None:
    if status == "ok":
        return None
    severity = "high" if status in {"unavailable", "invalid_schema", "blocked", "no_permission"} else "medium"
    title_by_status = {
        "empty": "Fuente sin datos materializados",
        "missing": "Dataset requerido no registrado",
        "unavailable": "Fuente operativa no disponible",
        "invalid_schema": "Dataset con contrato invalido",
        "blocked": "Cartucho inactivo o bloqueado",
        "no_permission": "Cartucho sin permiso para este usuario",
    }
    description_by_status = {
        "empty": f"{source.dataset} existe pero no tiene filas para el workspace activo.",
        "missing": f"{source.dataset} no esta disponible en el catalogo del workspace activo.",
        "unavailable": f"{source.dataset} no pudo consultarse desde Refinement.",
        "invalid_schema": f"{source.dataset} no cumple el contrato esperado por la Sala de Control.",
        "blocked": f"{source.module_label} esta instalado pero no esta activo para el workspace.",
        "no_permission": f"{source.module_label} no esta permitido para este usuario.",
    }
    item = _base_item(
        source,
        {"severity": severity, "details": {"source_status": status, "error": error or ""}},
        f"source_{status}",
        source.dataset,
        source.dataset,
    )
    item.update({
        "kind": "source_state",
        "title": title_by_status.get(status, "Fuente requiere atencion"),
        "description": description_by_status.get(status, f"{source.dataset} requiere revision."),
        "recommendation": "Validar instalacion, credenciales, materializacion y scope tenant/workspace antes de operar con el cliente.",
        "root_cause": "La cadena de datos no esta lista para entregar senales de negocio confiables.",
        "impact": "El cartucho puede aparecer activo pero sin datos accionables en la sala.",
    })
    return item


def _control_state(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("control_state") if isinstance(item.get("control_state"), dict) else {}
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


def _control_items_for_item(
    item: dict[str, Any],
    *,
    status: str,
    decision_id: Any,
    approved: bool,
) -> list[dict[str, Any]]:
    control_state = _control_state(item)
    controls: list[dict[str, Any]] = []
    for control in _default_control_items(item, status=status, decision_id=decision_id, approved=approved):
        override = control_state.get(str(control["id"]), {})
        next_status = str(override.get("status") or control.get("status") or "open")
        if next_status not in CONTROL_ITEM_STATUSES:
            next_status = "open"
        controls.append({
            **control,
            **override,
            "id": str(control["id"]),
            "status": next_status,
            "st": CONTROL_ITEM_STATUS_LABELS.get(next_status, str(control.get("st") or next_status)),
            "owner": override.get("owner") or control.get("owner"),
            "due_at": override.get("due_at") or control.get("due_at"),
        })
    return controls


def _with_omega(item: dict[str, Any]) -> dict[str, Any]:
    decision_id = item.get("decision_id")
    status = item.get("status") or "open"
    approved = status == "approved"
    is_source_state = item.get("kind") == "source_state"
    primary_system = item.get("cartridge") or "platform"
    impact = _impact_for_item(item)
    action_templates = _action_templates_for_item(item)
    execution_status = str(item.get("execution_status") or "not_started")
    if execution_status not in EXECUTION_STATUSES:
        execution_status = "not_started"
    option_label = "Restaurar fuente de datos" if is_source_state else "Remediar dato/proceso"
    action_label = "Validar materializacion y permisos" if is_source_state else "Validar owner y remediacion"
    selected_option_id = str(item.get("selected_option_id") or "remediate")
    impact_money = (
        f"${impact['estimate']:,.0f} {impact['currency']} en revision"
        if impact.get("status") == "ok" and impact.get("estimate") is not None
        else "Impacto no calculable"
    )
    options = [
        {
            "id": "remediate",
            "label": option_label,
            "action": option_label,
            "money": impact_money,
            "time": "1-2 ciclos",
            "score": 92,
            "risk": "Bajo",
            "auto": True,
            "recommendation": item.get("recommendation"),
            "selected": False,
        },
        {
            "id": "exception",
            "label": "Aprobar excepcion temporal",
            "action": "Aprobar excepcion temporal",
            "money": "Costo medio",
            "time": "Mismo dia",
            "score": 68,
            "risk": "Medio",
            "auto": False,
            "recommendation": "Usar solo con responsable y fecha de control.",
            "selected": False,
        },
        {
            "id": "monitor",
            "label": "Monitorear sin cambio inmediato",
            "action": "Monitorear sin cambio inmediato",
            "money": "Sin gasto inmediato",
            "time": "Siguiente refresh",
            "score": 45,
            "risk": "Alto",
            "auto": False,
            "recommendation": "No recomendado para severidad alta o critica.",
            "selected": False,
        },
    ]
    if selected_option_id not in {option["id"] for option in options}:
        selected_option_id = "remediate"
    for option in options:
        option["selected"] = option["id"] == selected_option_id
    lessons = _lessons_for_item(item)
    lesson_count = int(item.get("lesson_count") or 0)
    priority = _priority_payload({**item, "lesson_count": lesson_count}, impact)
    alert_state = _alert_state(item)
    control_items = _control_items_for_item(
        item,
        status=status,
        decision_id=decision_id,
        approved=approved,
    )
    control_closed = all(str(control.get("status")) == "closed" for control in control_items)
    return {
        **item,
        "alert_state": alert_state,
        "control_state": _control_state(item),
        "impact_estimate": impact.get("estimate"),
        "impact_currency": impact.get("currency"),
        "impact_status": impact.get("status"),
        "confidence": impact.get("confidence"),
        "priority_score": priority["score"],
        "priority": priority,
        "impact_drivers": impact.get("drivers"),
        "impact_formula": impact.get("formula"),
        "impact_explanation": impact.get("explanation"),
        "thresholds_applied": item.get("thresholds_applied") or [],
        "threshold_state": item.get("threshold_state") or "default",
        "selected_option_id": selected_option_id,
        "execution_status": execution_status,
        "action_templates": action_templates,
        "related_lessons": item.get("related_lessons") or [],
        "lesson_count": lesson_count,
        "lesson_applications": item.get("lesson_applications") if isinstance(item.get("lesson_applications"), list) else [],
        "omega": {
            "signals": {
                "source": item.get("source_dataset"),
                "severity": item.get("severity"),
                "detected_at": item.get("detected_at"),
                "status": status,
                "priority_score": priority["score"],
                "priority_band": priority["band"],
                "threshold_state": item.get("threshold_state") or "default",
            },
            "investigation": {
                "root_cause": item.get("root_cause"),
                "impact": item.get("impact"),
                "evidence": item.get("details") or {},
                "money": impact,
                "thresholds": item.get("thresholds_applied") or [],
            },
            "options": options,
            "decision": {
                "decision_id": decision_id,
                "status": status,
                "label": f"Decision #{decision_id}" if decision_id else "Pendiente",
            },
            "execution": {
                "status": execution_status,
                "external_writeback_enabled": _external_writeback_enabled(),
                "templates": action_templates,
                "actions": [
                    {
                        "id": "preview",
                        "sys": "omega",
                        "act": "Generar preview de accion",
                        "label": "Preview seguro",
                        "done": execution_status in {"preview_generated", "dry_run_validated", "executed"},
                        "approved": execution_status in {"preview_generated", "dry_run_validated", "executed"},
                        "auto": True,
                    },
                    {
                        "id": "dry_run",
                        "sys": primary_system,
                        "act": "Validar dry-run sin write-back",
                        "label": "Dry-run seguro",
                        "done": execution_status in {"dry_run_validated", "executed"},
                        "approved": execution_status in {"dry_run_validated", "executed"},
                        "auto": True,
                    },
                    {
                        "id": "owner_review",
                        "sys": primary_system,
                        "act": action_label,
                        "label": action_label,
                        "done": approved,
                        "approved": approved,
                        "auto": False,
                    },
                    {
                        "id": "audit_log",
                        "sys": "omega",
                        "act": "Registrar bitacora y evidencia",
                        "label": "Registrar bitacora y evidencia",
                        "done": bool(decision_id),
                        "approved": bool(decision_id),
                        "auto": True,
                    },
                ],
            },
            "control": {
                "owner": item.get("module") or item.get("cartridge"),
                "cadence": "Proximo refresh operativo",
                "status": "cerrado" if control_closed else "abierto",
                "items": control_items,
            },
            "lessons": {
                "rules": lessons,
                "applied": item.get("lesson_applications") if isinstance(item.get("lesson_applications"), list) else [],
            },
        },
    }


def _status_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str, str]:
    active_rank = 1 if item.get("status") in TERMINAL_ITEM_STATUSES else 0
    return (
        active_rank,
        -int(item.get("priority_score") or 0),
        -int(item.get("severity_weight") or 0),
        str(item.get("domain") or ""),
        str(item.get("title") or ""),
    )


def _alert_type_for_item(item: dict[str, Any]) -> str:
    if item.get("kind") == "source_state":
        return "source_health"
    if str(item.get("threshold_state") or "default") in {"critical", "warning"}:
        return "threshold_breach"
    if int(item.get("lesson_count") or 0) > 0:
        return "learned_pattern"
    return "critical_signal" if item.get("severity") in {"critical", "high"} else "watchlist"


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


def _metadata_for_item(item: dict[str, Any], impact: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "module": item.get("module"),
        "description": item.get("description"),
        "recommendation": item.get("recommendation"),
        "root_cause": item.get("root_cause"),
        "impact": item.get("impact"),
        "details": item.get("details") if isinstance(item.get("details"), dict) else {},
        "sql": item.get("sql"),
        "impact_payload": impact,
        "thresholds_applied": item.get("thresholds_applied") or [],
        "threshold_state": item.get("threshold_state") or "default",
    }
    alert_state = item.get("alert_state") if isinstance(item.get("alert_state"), dict) else {}
    if alert_state:
        metadata["alert_state"] = alert_state
    control_state = item.get("control_state") if isinstance(item.get("control_state"), dict) else {}
    if control_state:
        metadata["control_state"] = control_state
    learned_rules = item.get("learned_rules") if isinstance(item.get("learned_rules"), list) else []
    if learned_rules:
        metadata["learned_rules"] = learned_rules[:10]
    lessons = item.get("lessons") if isinstance(item.get("lessons"), list) else []
    if lessons:
        metadata["lessons"] = lessons[:10]
    lesson_applications = (
        item.get("lesson_applications")
        if isinstance(item.get("lesson_applications"), list)
        else []
    )
    if lesson_applications:
        metadata["lesson_applications"] = lesson_applications[:20]
    return metadata


def _alert_message(item: dict[str, Any], alert_type: str) -> str:
    if alert_type == "source_health":
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        return f"{item.get('source_dataset')} esta {details.get('source_status') or 'sin datos operativos'}."
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
    return str(item.get("description") or item.get("title") or "Senal operativa requiere revision.")


def _alert_for_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if str(item.get("status") or "open") in TERMINAL_ITEM_STATUSES:
        return None
    alert_state = _alert_state(item)
    alert_status = str(alert_state.get("state") or "open")
    if alert_status == "false_positive":
        return None
    priority = item.get("priority") if isinstance(item.get("priority"), dict) else _priority_payload(item)
    score = int(priority.get("score") or item.get("priority_score") or 0)
    alert_type = _alert_type_for_item(item)
    threshold_state = str(item.get("threshold_state") or "default")
    should_alert = (
        alert_type in {"source_health", "threshold_breach", "learned_pattern"}
        or item.get("severity") in {"critical", "high"}
        or score >= 55
    )
    if not should_alert:
        return None
    severity = str(priority.get("band") or item.get("severity") or "medium")
    if severity not in ALERT_SEVERITY_WEIGHT:
        severity = "medium"
    delivery_status = {
        "open": "not_configured",
        "acknowledged": "acknowledged",
        "assigned": "assigned",
        "snoozed": "snoozed",
    }.get(alert_status, "not_configured")
    push_ready = False
    return {
        "id": f"alert:{item.get('id')}",
        "item_id": item.get("id"),
        "alert_type": alert_type,
        "severity": severity,
        "priority_score": score,
        "domain": item.get("domain"),
        "module": item.get("module"),
        "module_id": item.get("module_id") or item.get("cartridge"),
        "cartridge": item.get("cartridge"),
        "connector_id": item.get("connector_id") or item.get("cartridge"),
        "source_dataset": item.get("source_dataset"),
        "title": item.get("title"),
        "message": _alert_message(item, alert_type),
        "status": alert_status,
        "owner": alert_state.get("owner"),
        "note": alert_state.get("note"),
        "reason": alert_state.get("reason"),
        "acknowledged_at": alert_state.get("acknowledged_at"),
        "assigned_at": alert_state.get("assigned_at"),
        "snoozed_until": alert_state.get("snoozed_until"),
        "threshold_state": threshold_state,
        "lesson_count": int(item.get("lesson_count") or 0),
        "impact_estimate": item.get("impact_estimate"),
        "impact_currency": item.get("impact_currency") or "USD",
        "recommended_action": item.get("recommendation"),
        "drivers": priority.get("drivers") or [],
        "route_key": f"{item.get('cartridge')}:{item.get('anomaly_type')}:{item.get('module_id') or item.get('cartridge')}",
        "push_ready": push_ready,
        "delivery": {
            "status": delivery_status,
            "channels": [],
            "reason": "Canal externo no configurado en V1; la alerta queda solo en cola interna."
            if alert_status == "open"
            else f"Alerta en estado {alert_status}; no hay push externo en V1.",
        },
        "created_at": item.get("first_seen_at") or item.get("detected_at") or datetime.now(UTC).isoformat(),
        "updated_at": alert_state.get("updated_at") or item.get("last_seen_at") or datetime.now(UTC).isoformat(),
    }


def _alert_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    alerts = [alert for item in items if (alert := _alert_for_item(item))]
    alerts.sort(key=lambda alert: (
        -int(alert.get("priority_score") or 0),
        -ALERT_SEVERITY_WEIGHT.get(str(alert.get("severity") or "medium"), 2),
        str(alert.get("domain") or ""),
        str(alert.get("title") or ""),
    ))
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


async def _overlay_item_state(items: list[dict[str, Any]], user: dict | None, *, persist: bool = False) -> list[dict[str, Any]]:
    if not items:
        return []
    if not persist:
        baseline = [_with_omega(item) for item in items]
        baseline.sort(key=_status_sort_key)
        return baseline
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    item_ids = [item["id"] for item in items]

    if persist:
        for item in items:
            impact = _impact_for_item(item)
            try:
                await pool.execute(
                    """
                    INSERT INTO control_room_items (
                        tenant_id, workspace_id, item_id, cartridge_id, domain,
                        source_dataset, item_kind, title, severity, status,
                        entity_kind, entity_id, entity_label, anomaly_type, metadata,
                        impact_estimate, impact_currency, confidence, priority_score,
                        selected_option_id, execution_status,
                        first_seen_at, last_seen_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9, 'open',
                        $10, $11, $12, $13, $14::jsonb,
                        $15, $16, $17, $18, $19, $20,
                        NOW(), NOW()
                    )
                    ON CONFLICT (workspace_id, item_id) DO UPDATE
                    SET cartridge_id = EXCLUDED.cartridge_id,
                        domain = EXCLUDED.domain,
                        source_dataset = EXCLUDED.source_dataset,
                        item_kind = EXCLUDED.item_kind,
                        title = EXCLUDED.title,
                        severity = EXCLUDED.severity,
                        entity_kind = EXCLUDED.entity_kind,
                        entity_id = EXCLUDED.entity_id,
                        entity_label = EXCLUDED.entity_label,
                        anomaly_type = EXCLUDED.anomaly_type,
                        metadata = control_room_items.metadata || EXCLUDED.metadata,
                        impact_estimate = EXCLUDED.impact_estimate,
                        impact_currency = EXCLUDED.impact_currency,
                        confidence = EXCLUDED.confidence,
                        priority_score = EXCLUDED.priority_score,
                        last_seen_at = NOW(),
                        status = CASE
                            WHEN control_room_items.status = ANY($21::text[])
                            THEN control_room_items.status
                            ELSE control_room_items.status
                        END
                    """,
                    tenant_id,
                    workspace_id,
                    item["id"],
                    item["cartridge"],
                    item["domain"],
                    item["source_dataset"],
                    item["kind"],
                    item["title"],
                    item["severity"],
                    item.get("entity_kind"),
                    item.get("entity_id"),
                    item.get("entity_label"),
                    item.get("anomaly_type"),
                    json.dumps(_metadata_for_item(item, impact)),
                    impact.get("estimate"),
                    impact.get("currency") or "USD",
                    impact.get("confidence"),
                    impact.get("priority_score") or 0,
                    item.get("selected_option_id"),
                    item.get("execution_status") or "not_started",
                    sorted(TERMINAL_ITEM_STATUSES),
                )
            except Exception:
                break

    try:
        rows = await pool.fetch(
            """
            SELECT item_id, status, decision_id, metadata, first_seen_at, last_seen_at,
                   resolved_at, dismissed_at, impact_estimate, impact_currency,
                   confidence, priority_score, selected_option_id, execution_status
              FROM control_room_items
             WHERE workspace_id = $1
               AND item_id = ANY($2::text[])
            """,
            workspace_id,
            item_ids,
        )
        state_by_id = {row["item_id"]: _row_to_public(row) for row in rows}
    except Exception:
        state_by_id = {}

    merged = []
    for item in items:
        state = state_by_id.get(item["id"], {})
        metadata = _details(state.get("metadata"))
        status = str(state.get("status") or item.get("status") or "open")
        if status not in ITEM_STATUSES:
            status = "open"
        merged.append(_with_omega({
            **item,
            "status": status,
            "decision_id": state.get("decision_id") or item.get("decision_id"),
            "impact_estimate": state.get("impact_estimate"),
            "impact_currency": state.get("impact_currency"),
            "confidence": state.get("confidence"),
            "priority_score": state.get("priority_score"),
            "selected_option_id": state.get("selected_option_id") or metadata.get("selected_option_id"),
            "execution_status": state.get("execution_status") or metadata.get("execution_status"),
            "thresholds_applied": metadata.get("thresholds_applied") or item.get("thresholds_applied") or [],
            "threshold_state": metadata.get("threshold_state") or item.get("threshold_state") or "default",
            "alert_state": metadata.get("alert_state") if isinstance(metadata.get("alert_state"), dict) else item.get("alert_state"),
            "control_state": metadata.get("control_state") if isinstance(metadata.get("control_state"), dict) else item.get("control_state"),
            "lessons": metadata.get("lessons"),
            "learned_rules": metadata.get("learned_rules"),
            "lesson_applications": metadata.get("lesson_applications") if isinstance(metadata.get("lesson_applications"), list) else [],
            "first_seen_at": state.get("first_seen_at"),
            "last_seen_at": state.get("last_seen_at"),
            "resolved_at": state.get("resolved_at"),
            "dismissed_at": state.get("dismissed_at"),
        }))
    merged.sort(key=_status_sort_key)
    return merged


async def _collect_items(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
    include_source_state_items: bool = False,
    persist: bool = False,
    use_catalog: bool = True,
) -> dict[str, Any]:
    if use_catalog:
        installations = await _installed_cartridges(user)
    else:
        installations = [
            {
                "cartridge_id": module.cartridge,
                "installation_status": "ready",
                "current_step": "test_registry",
                "label": module.label,
                "category": "platform" if module.operational else "cartridge",
            }
            for module in MODULES
            if module.sources
        ]
    installation_by_cartridge = {
        str(row.get("cartridge_id")): row
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
    }
    installed = set(installation_by_cartridge)
    active = {
        cartridge_id
        for cartridge_id, row in installation_by_cartridge.items()
        if str(row.get("installation_status") or "ready") in ACTIVE_INSTALLATION_STATUSES
    }
    modules = [module for module in MODULES if module.cartridge in installed]

    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    rows_by_dataset: dict[str, list[dict[str, Any]]] = {}
    threshold_rows = await _load_threshold_rows(user) if use_catalog else []
    thresholds = _threshold_map(threshold_rows)
    for module in modules:
        installation = installation_by_cartridge.get(module.cartridge, {})
        if module.cartridge not in active:
            for source in module.sources:
                source_status = {
                    "dataset": source.dataset,
                    "cartridge": source.cartridge,
                    "connector_id": source.cartridge,
                    "module_id": source.visible_module_id,
                    "domain": source.domain,
                    "module": source.module_label,
                    "status": "blocked",
                    "error": str(installation.get("error_message") or installation.get("current_step") or ""),
                    "count": 0,
                    "checked_at": datetime.now(UTC).isoformat(),
                }
                rows_by_dataset[source.dataset] = []
                sources.append(source_status)
                if include_source_state_items:
                    source_item = _source_state_item(source, "blocked", source_status.get("error"))
                    if source_item:
                        items.append(source_item)
            continue
        for source in module.sources:
            rows, source_status = await _fetch_source(source, user, fetcher, limit_per_source)
            rows_by_dataset[source.dataset] = rows if source_status["status"] == "ok" else []
            sources.append(source_status)
            if include_source_state_items:
                source_item = _source_state_item(source, source_status["status"], source_status.get("error"))
                if source_item:
                    items.append(source_item)
            if source_status["status"] != "ok":
                continue
            for row in rows:
                item = _normalize_row(source, row, thresholds)
                if item:
                    items.append(item)

    items = await _overlay_item_state(items, user, persist=persist)
    return {
        "items": items,
        "sources": sources,
        "installations": installations,
        "modules": modules,
        "financial": _financial_metrics(sources, rows_by_dataset),
        "thresholds": threshold_rows,
    }


def _severity_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in SEVERITY_WEIGHT}
    for item in items:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return counts


def _source_rollup_status(module_sources: list[dict[str, Any]]) -> str:
    if not module_sources:
        return "no_sources"
    priority = [
        "no_permission",
        "blocked",
        "invalid_schema",
        "unavailable",
        "missing",
        "empty",
        "ok",
    ]
    statuses = {str(source.get("status") or "no_sources") for source in module_sources}
    if statuses == {"ok"}:
        return "ok"
    for status in priority:
        if status in statuses:
            return status
    return "attention"


def _domain_payload(
    domain: str,
    modules: list[ControlRoomModule],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    domain_modules = [module for module in modules if module.domain == domain or any(source.domain == domain for source in module.sources)]
    domain_items = [item for item in items if item["domain"] == domain]
    module_payload = []
    for module in domain_modules:
        module_sources = [
            source
            for source in sources
            if source.get("module_id") == module.visible_id and source.get("domain") == domain
        ]
        module_items = [
            item
            for item in domain_items
            if item.get("module_id", item.get("cartridge")) == module.visible_id
        ]
        source_count = sum(int(source.get("count") or 0) for source in module_sources)
        source_status = _source_rollup_status(module_sources)
        module_payload.append({
            "id": module.visible_id,
            "connector_id": module.cartridge,
            "label": module.label,
            "domain": domain,
            "accent": module.accent,
            "description": module.description,
            "item_count": len(module_items),
            "critical_count": sum(1 for item in module_items if item["severity"] == "critical"),
            "source_status": source_status,
            "kpis": [
                {
                    "label": "Registros fuente",
                    "value": source_count,
                    "tone": "neutral",
                    "bad": source_status not in {"ok", "no_sources"},
                    "sql": " UNION ALL ".join(
                        f"SELECT COUNT(*) AS registros, '{source['dataset']}' AS dataset FROM {source['dataset']}"
                        for source in module_sources
                    ) or "-- sin fuente materializada",
                },
                {
                    "label": "Items abiertos",
                    "value": sum(1 for item in module_items if item["status"] not in TERMINAL_ITEM_STATUSES),
                    "tone": "attention",
                    "bad": any(item["status"] not in TERMINAL_ITEM_STATUSES for item in module_items),
                    "sql": f"SELECT * FROM control_room_items WHERE cartridge_id = '{module.cartridge}' AND domain = '{domain}' AND status NOT IN ('approved','dismissed','resolved')",
                },
            ],
        })
    return {
        "id": domain.lower().replace(" ", "_"),
        "label": domain,
        "accent": DOMAIN_ACCENTS.get(domain, "#64748b"),
        "item_count": len(domain_items),
        "critical_count": sum(1 for item in domain_items if item["severity"] == "critical"),
        "cartridge_count": len({module.visible_id for module in domain_modules}),
        "modules": module_payload,
    }


async def dashboard(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
    persist: bool = True,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC)
    payload = await _collect_items(
        user,
        fetcher=fetcher,
        limit_per_source=limit_per_source,
        include_source_state_items=True,
        persist=persist,
    )
    items = payload["items"]
    sources = payload["sources"]
    modules = payload["modules"]
    installations = payload["installations"]
    financial = payload["financial"]
    thresholds = payload.get("thresholds") or []
    lesson_rows = await _load_lesson_rows(user, limit=200)
    lesson_summary = _lesson_insights(lesson_rows)
    items = _attach_lessons_to_items(items, lesson_rows)
    alerts_payload = _alert_payload(items)
    alerts = alerts_payload["alerts"]
    alert_summary = alerts_payload["summary"]

    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1

    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    open_decisions = 0
    try:
        open_decisions = int(await pool.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
            workspace_id,
        ) or 0)
    except Exception:
        open_decisions = 0

    installation_by_cartridge = {
        str(row.get("cartridge_id")): row
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
    }
    cartridges = []
    for module in modules:
        row = installation_by_cartridge.get(module.cartridge, {})
        cartridge_id = module.visible_id
        module_items = [
            item
            for item in items
            if item.get("module_id", item.get("cartridge")) == module.visible_id
        ]
        module_sources = [source for source in sources if source.get("module_id") == module.visible_id]
        source_status = _source_rollup_status(module_sources)
        installation_status = str(row.get("installation_status") or "ready")
        cartridges.append({
            "id": cartridge_id,
            "connector_id": module.cartridge,
            "connector_label": row.get("label") or module.cartridge,
            "label": module.label,
            "domain": module.domain,
            "accent": module.accent,
            "description": module.description,
            "status": installation_status,
            "current_step": row.get("current_step"),
            "active": installation_status in ACTIVE_INSTALLATION_STATUSES,
            "operational": bool(module.operational),
            "item_count": len(module_items),
            "critical_count": sum(1 for item in module_items if item["severity"] == "critical"),
            "source_status": source_status,
            "datasets": module_sources,
        })

    domain_labels = list(DOMAIN_ORDER)
    for module in modules:
        if module.domain not in domain_labels:
            domain_labels.append(module.domain)
        for source in module.sources:
            if source.domain not in domain_labels:
                domain_labels.append(source.domain)
    domains = [_domain_payload(domain, modules, items, sources) for domain in domain_labels]

    return {
        "meta": {
            "generated_at": generated_at.isoformat(),
            "refresh_interval_seconds": CONTROL_ROOM_REFRESH_INTERVAL_SECONDS,
            "live_mode": "polling",
            "source_count": len(sources),
            "item_count": len(items),
            "external_writeback_enabled": _external_writeback_enabled(),
        },
        "workspace": {
            "tenant_id": (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id"),
            "workspace_id": workspace_id,
        },
        "period": generated_at.strftime("%B %Y"),
        "omega_steps": OMEGA_STEPS,
        "summary": {
            "total_items": len(items),
            "total_anomalies": sum(1 for item in items if item["kind"] == "anomaly"),
            "control_items": sum(1 for item in items if item["kind"] != "anomaly"),
            "by_severity": by_severity,
            "by_cartridge": by_cartridge,
            "by_domain": by_domain,
            "critical": by_severity.get("critical", 0),
            "attention": by_severity.get("high", 0) + by_severity.get("medium", 0),
            "open_decisions": open_decisions,
            "active_connectors": len({
                row["connector_id"]
                for row in cartridges
                if row["active"] and not row["operational"]
            }),
            "active_modules": len([row for row in cartridges if row["active"] and not row["operational"]]),
            "active_cartridges": len([row for row in cartridges if row["active"] and not row["operational"]]),
            "operational_cartridges": len([row for row in cartridges if row["active"] and row["operational"]]),
            "source_states": {
                status: sum(1 for source in sources if source["status"] == status)
                for status in ["ok", "empty", "missing", "unavailable", "invalid_schema", "blocked", "no_permission"]
            },
            "cycle_counts": _cycle_counts(items),
            "financial": financial,
            "thresholds": {
                "active": sum(1 for row in thresholds if row.get("enabled", True)),
                "total": len(thresholds),
                "by_cartridge": {
                    cartridge_id: sum(1 for row in thresholds if row.get("cartridge_id") == cartridge_id)
                    for cartridge_id in sorted({
                        str(row.get("cartridge_id") or "").strip()
                        for row in thresholds
                        if str(row.get("cartridge_id") or "").strip()
                    })
                },
                "items_with_thresholds": sum(1 for item in items if item.get("thresholds_applied")),
            },
            "lessons": lesson_summary,
            "alerts": alert_summary,
        },
        "domains": domains,
        "cartridges": cartridges,
        "sources": sources,
        "alerts": alerts,
        "items": items,
    }


async def list_anomalies(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
) -> dict[str, Any]:
    payload = await _collect_items(
        user,
        fetcher=fetcher,
        limit_per_source=limit_per_source,
        include_source_state_items=False,
        persist=False,
        use_catalog=False,
    )
    anomalies = [item for item in payload["items"] if item["kind"] == "anomaly"]
    return {"anomalies": anomalies, "sources": payload["sources"]}


async def summary(user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows) -> dict[str, Any]:
    collected = await _collect_items(
        user,
        fetcher=fetcher,
        include_source_state_items=False,
        persist=False,
        use_catalog=False,
    )
    items = [item for item in collected["items"] if item["kind"] == "anomaly"]
    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    open_decisions = 0
    if workspace_id:
        open_decisions = int(await pool.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
            workspace_id,
        ) or 0)
    lesson_rows = await _load_lesson_rows(user, limit=100)
    items = _attach_lessons_to_items(items, lesson_rows)
    alert_summary = _alert_payload(items)["summary"]
    return {
        "total_anomalies": len(items),
        "by_severity": by_severity,
        "by_cartridge": by_cartridge,
        "by_domain": by_domain,
        "open_decisions": open_decisions,
        "sources": collected["sources"],
        "financial": collected["financial"],
        "thresholds": {
            "active": sum(1 for row in collected.get("thresholds", []) if row.get("enabled", True)),
            "total": len(collected.get("thresholds", [])),
        },
        "lessons": _lesson_insights(lesson_rows),
        "alerts": alert_summary,
    }


async def get_item(item_id: str, user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows) -> dict[str, Any]:
    payload = await dashboard(user, fetcher=fetcher, persist=True)
    for item in payload["items"]:
        if item["id"] == item_id:
            return item
    raise HTTPException(404, "control room item not found")


async def get_anomaly(anomaly_id: str, user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows) -> dict[str, Any]:
    item = await get_item(anomaly_id, user, fetcher=fetcher)
    if item["kind"] != "anomaly":
        raise HTTPException(404, "anomaly not found")
    return item


async def _persisted_item_for_mutation(item_id: str, user: dict) -> dict[str, Any] | None:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    try:
        row = await pool.fetchrow(
            """
            SELECT item_id, cartridge_id, domain, source_dataset, item_kind, title,
                   severity, status, decision_id, entity_kind, entity_id,
                   entity_label, anomaly_type, metadata, first_seen_at, last_seen_at,
                   resolved_at, dismissed_at, impact_estimate, impact_currency,
                   confidence, priority_score, selected_option_id, execution_status
              FROM control_room_items
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            workspace_id,
            item_id,
        )
    except Exception:
        return None
    if not row:
        return None
    try:
        row_item_id = row["item_id"]
    except Exception:
        return None
    if row_item_id != item_id:
        return None

    public_row = _row_to_public(row)
    metadata = _details(public_row.get("metadata"))
    severity = _severity(public_row["severity"])
    status = str(public_row["status"] or "open")
    if status not in ITEM_STATUSES:
        status = "open"
    escaped_item_id = item_id.replace("'", "''")
    item = {
        "id": public_row["item_id"],
        "kind": public_row["item_kind"],
        "domain": public_row["domain"],
        "module": metadata.get("module") or public_row["cartridge_id"],
        "cartridge": public_row["cartridge_id"],
        "source_dataset": public_row["source_dataset"],
        "entity_kind": public_row["entity_kind"] or "Entidad",
        "entity_id": public_row["entity_id"] or "",
        "entity_label": public_row["entity_label"] or public_row["entity_id"] or public_row["source_dataset"] or "Entidad",
        "anomaly_type": public_row["anomaly_type"] or "control_room_item",
        "severity": severity,
        "severity_weight": SEVERITY_WEIGHT[severity],
        "detected_at": "",
        "details": metadata.get("details") if isinstance(metadata.get("details"), dict) else {},
        "title": public_row["title"],
        "description": metadata.get("description") or public_row["title"],
        "recommendation": metadata.get("recommendation") or "Revisar, decidir y registrar evidencia.",
        "root_cause": metadata.get("root_cause") or "Senal persistida en Sala de Control.",
        "impact": metadata.get("impact") or "Riesgo operativo.",
        "sql": metadata.get("sql") or f"SELECT * FROM control_room_items WHERE item_id = '{escaped_item_id}'",
        "status": status,
        "decision_id": public_row["decision_id"],
        "impact_estimate": public_row.get("impact_estimate"),
        "impact_currency": public_row.get("impact_currency"),
        "confidence": public_row.get("confidence"),
        "priority_score": public_row.get("priority_score"),
        "thresholds_applied": metadata.get("thresholds_applied") or [],
        "threshold_state": metadata.get("threshold_state") or "default",
        "selected_option_id": public_row.get("selected_option_id") or metadata.get("selected_option_id"),
        "execution_status": public_row.get("execution_status") or metadata.get("execution_status"),
        "alert_state": metadata.get("alert_state") if isinstance(metadata.get("alert_state"), dict) else {},
        "control_state": metadata.get("control_state") if isinstance(metadata.get("control_state"), dict) else {},
        "lessons": metadata.get("lessons"),
        "learned_rules": metadata.get("learned_rules"),
        "lesson_applications": metadata.get("lesson_applications") if isinstance(metadata.get("lesson_applications"), list) else [],
        "first_seen_at": public_row.get("first_seen_at"),
        "last_seen_at": public_row.get("last_seen_at"),
        "resolved_at": public_row.get("resolved_at"),
        "dismissed_at": public_row.get("dismissed_at"),
    }
    return _with_omega(item)


async def _item_for_mutation(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    persisted = await _persisted_item_for_mutation(item_id, user)
    if persisted:
        return persisted
    return await get_item(item_id, user, fetcher=fetcher)


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


def _execution_to_activity(row: Any) -> dict[str, Any]:
    data = _row_to_public(row)
    mode = str(data.get("mode") or "execution")
    status = str(data.get("status") or "")
    label_by_mode = {
        "preview": "Preview generado",
        "dry_run": "Dry-run validado",
        "execute_live": "Ejecucion productiva",
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


async def get_item_activity(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    event_rows: list[Any] = []
    execution_rows: list[Any] = []
    decision_action_rows: list[Any] = []
    try:
        event_rows = await pool.fetch(
            """
            SELECT id, item_id, event_type, actor_email, metadata, created_at
              FROM control_room_item_events
             WHERE workspace_id = $1
               AND item_id = $2
             ORDER BY created_at DESC
             LIMIT 50
            """,
            workspace_id,
            item["id"],
        )
    except Exception:
        event_rows = []
    try:
        execution_rows = await pool.fetch(
            """
            SELECT id, item_id, template_id, mode, status, payload, result,
                   error, actor_email, created_at, completed_at
              FROM control_room_action_executions
             WHERE workspace_id = $1
               AND item_id = $2
             ORDER BY created_at DESC
             LIMIT 50
            """,
            workspace_id,
            item["id"],
        )
    except Exception:
        execution_rows = []
    if item.get("decision_id"):
        try:
            decision_action_rows = await pool.fetch(
                """
                SELECT da.id, da.decision_id, da.action_text, da.note, da.actor, da.ts
                  FROM decision_actions da
                  JOIN decisions d ON d.id = da.decision_id
                 WHERE d.workspace_id = $1
                   AND da.decision_id = $2
                 ORDER BY da.ts DESC
                 LIMIT 50
                """,
                workspace_id,
                int(item["decision_id"]),
            )
        except Exception:
            decision_action_rows = []

    activity = [
        *(_event_to_activity(row) for row in event_rows),
        *(_execution_to_activity(row) for row in execution_rows),
        *(_decision_action_to_activity(row) for row in decision_action_rows),
    ]
    activity.sort(key=lambda entry: str(entry.get("at") or ""), reverse=True)
    return {
        "item_id": item["id"],
        "activity": activity,
        "counts": {
            "events": len(event_rows),
            "executions": len(execution_rows),
            "decision_actions": len(decision_action_rows),
            "total": len(activity),
        },
    }


async def _record_item_event(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    event_type: str,
    metadata: dict[str, Any],
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
        return


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
                json.dumps({"source_dataset": item.get("source_dataset"), "status": item.get("status")}),
            )
        except Exception:
            return


async def _record_action_execution(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    mode: str,
    status: str,
    payload: dict[str, Any],
    result: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO control_room_action_executions (
                tenant_id, workspace_id, item_id, template_id, mode, status,
                payload, result, error, actor_id, actor_email, completed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11, NOW())
            RETURNING *
            """,
            tenant_id,
            workspace_id,
            item["id"],
            template.get("template_id"),
            mode,
            status,
            json.dumps(payload),
            json.dumps(result),
            error,
            user.get("id"),
            user.get("email"),
        )
        return _row_to_public(row)
    except Exception:
        return {
            "id": None,
            "workspace_id": workspace_id,
            "item_id": item["id"],
            "template_id": template.get("template_id"),
            "mode": mode,
            "status": status,
            "payload": payload,
            "result": result,
            "error": error,
            "actor_email": user.get("email"),
            "created_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
        }


async def _set_execution_status(pool: Any, *, user: dict, item: dict[str, Any], execution_status: str) -> None:
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
        return


async def _ensure_item_row(pool: Any, *, user: dict, item: dict[str, Any], status: str = "open") -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    impact = _impact_for_item(item)
    try:
        await pool.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                source_dataset, item_kind, title, severity, status,
                entity_kind, entity_id, entity_label, anomaly_type, metadata,
                impact_estimate, impact_currency, confidence, priority_score,
                selected_option_id, execution_status
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15::jsonb,
                $16, $17, $18, $19, $20, $21
            )
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET last_seen_at = NOW(),
                impact_estimate = EXCLUDED.impact_estimate,
                impact_currency = EXCLUDED.impact_currency,
                confidence = EXCLUDED.confidence,
                priority_score = EXCLUDED.priority_score,
                status = CASE
                    WHEN control_room_items.status = ANY($22::text[])
                    THEN control_room_items.status
                    ELSE EXCLUDED.status
                END
            """,
            tenant_id,
            workspace_id,
            item["id"],
            item["cartridge"],
            item["domain"],
            item["source_dataset"],
            item["kind"],
            item["title"],
            item["severity"],
            status,
            item.get("entity_kind"),
            item.get("entity_id"),
            item.get("entity_label"),
            item.get("anomaly_type"),
            json.dumps(_metadata_for_item(item, impact)),
            impact.get("estimate"),
            impact.get("currency") or "USD",
            impact.get("confidence"),
            impact.get("priority_score") or 0,
            item.get("selected_option_id"),
            item.get("execution_status") or "not_started",
            sorted(TERMINAL_ITEM_STATUSES),
        )
    except Exception:
        return


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


async def get_item_impact(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    return _impact_for_item(item)


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


def _lesson_matches_item(lesson: dict[str, Any], item: dict[str, Any]) -> bool:
    if lesson.get("item_id") == item.get("id"):
        return True
    return (
        lesson.get("cartridge_id") == item.get("cartridge")
        and lesson.get("anomaly_type") == item.get("anomaly_type")
    )


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


def _resolve_template(item: dict[str, Any], template_id: str | None) -> dict[str, Any]:
    templates = _action_templates_for_item(item)
    if not template_id:
        return templates[0] if templates else _primary_template_for_item(item)
    for template in templates:
        if template["template_id"] == template_id:
            return template
    raise HTTPException(400, "action template is not valid for this item")


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


async def execute_item(
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
    payload = _execution_payload(item, "execute_live", template)
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    if not _external_writeback_enabled():
        result = {
            "ok": False,
            "mode": "execute_live",
            "external_write": False,
            "blocked": True,
            "message": "Write-back externo deshabilitado para V1.",
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
            error="CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=false",
        )
        await _set_execution_status(pool, user=user, item=item, execution_status="blocked")
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={"template_id": template["template_id"], "execution_id": execution.get("id")},
        )
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="control_room.action.execute.blocked",
            resource_type="control_room_item",
            resource_id=item_id,
            ip=ip,
            user_agent=user_agent,
            status="blocked",
            metadata={"template_id": template["template_id"], "result": result},
            critical=True,
        )
        raise HTTPException(409, "external write-back is disabled for Control Room V1")
    raise HTTPException(501, "live write-back connector is not implemented in V1")


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
    if lessons:
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


async def list_alerts(
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    payload = await dashboard(user, fetcher=fetcher, persist=True)
    return {
        "alerts": payload.get("alerts") or [],
        "summary": payload.get("summary", {}).get("alerts") or _alert_payload(payload.get("items") or [])["summary"],
        "generated_at": payload.get("meta", {}).get("generated_at"),
    }


async def list_thresholds(user: dict) -> dict[str, Any]:
    rows = await _load_threshold_rows(user, enabled_only=False)
    return {"thresholds": rows, "summary": _threshold_insights(rows)}


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
    row = await pool.fetchrow(
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
        workspace_id,
        cartridge_id,
        anomaly_type,
        metric,
        warning_value,
        critical_value,
        currency,
        enabled,
        json.dumps(metadata),
    )
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
    return {
        "lessons": lessons,
        "summary": _lesson_insights(lessons),
    }
