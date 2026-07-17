from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.middleware.request_id import request_id_var
from app.security import get_internal_api_key
from app.version import app_version
from app.services import audit_service, auth
from app.services.db_scope import SET_SCOPE_SQL, run_with_db_scope
from app.services.control_room.readiness_manifest import dataset_readiness_registry
from app.services.control_room.business_eligibility import (
    BUSINESS_EVIDENCE_FIELDS,
    BUSINESS_MATERIALIZATION_FIELDS,
    BUSINESS_OBSERVATION_FIELDS,
    BusinessEligibilityError,
    EligibilityReason,
    classify_business_item,
    require_business_eligible,
)
from app.services.control_room.business_observation_codec import (
    nonempty_mapping_fields,
    with_observation_envelope,
)
from app.services.control_room.business_access import (
    actor_id as business_actor_id,
    can_read_workspace_wide as business_can_read_workspace_wide,
    expected_item_owner as expected_business_item_owner,
    owner_projection as business_owner_projection,
    owner_scope_id,
    workspace_scope as business_workspace_scope,
)
from app.services.control_room.business_agentops import (
    _agentops_alert_rows,
    _agentops_execution_rows,
    _agentops_orchestration_rows,
    _agentops_origin_rows,
)
from app.services.control_room.business_lineage import item_kinds, parent_references
from app.services.control_room.business_projection import (
    business_parent_context,
    diagnostic_items,
    duplicate_item_ids,
    eligible_item_ids,
    evolve_business_item,
    filter_business_items,
    filter_by_eligible_parent,
    lineage_parent_ids,
    project_business_item,
    strip_business_fields,
)
from app.services.control_room.business_item_persistence import (
    OwnerScopeConflict,
    ensure_item_row as ensure_business_item_row,
    persist_item_rows,
)
from app.services.control_room.business_item_reader import (
    fetch_eligible_persisted_items,
    resolve_scoped_business_item_lookup,
)
from app.services.control_room.business_repository import (
    decision_provenance,
    fetch_lineage_rows,
    link_control_room_decision,
)
from app.services.control_room.business_state_rows import ensured_row, state_rows
from app.services.control_room.business_runtime_projection import (
    persisted_business_projection,
)
from app.services.control_room.business_decision_summary import (
    count_open_business_decisions,
)
from app.services.security_context import build_security_context, rls_user_context


REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500").rstrip("/")
VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8300").rstrip("/")

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
    "action_executed": "Ejecucion supervisada registrada",
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

SUPPORTED_INTERNAL_WRITEBACK_TEMPLATES = {
    "create_followup_task",
    "create_investigation_note",
    "mark_decision_for_monitoring",
}


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    status: str
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
        }
        if self.data is not None:
            payload["data"] = self.data
        return payload


class BaseAdapter(ABC):
    @abstractmethod
    def execute(
        self,
        action_data: dict[str, Any],
        credentials: dict[str, Any],
        dry_run: bool = True,
    ) -> ExecutionResult | dict[str, Any] | Awaitable[ExecutionResult | dict[str, Any]]:
        """Execute an approved external write-back action."""


class WriteBackAdapterFactory:
    _registry: dict[str, type[BaseAdapter]] = {}

    @classmethod
    def _ensure_builtin_adapters(cls) -> None:
        from app.services.adapters.replicon_adapter import RepliconAdapter
        from app.services.adapters.sap_hcm_adapter import SapHcmAdapter

        cls._register_builtin("prepare_billing_review", RepliconAdapter)
        cls._register_builtin("prepare_replicon_adjustment", RepliconAdapter)
        cls._register_builtin("sap_hcm_it0008", SapHcmAdapter)

    @classmethod
    def _register_builtin(cls, template_type: str, adapter_cls: type[BaseAdapter]) -> None:
        registered_cls = adapter_cls
        if not issubclass(adapter_cls, BaseAdapter):
            # Pytest can import the legacy facade and modular core in an order
            # that leaves adapter modules bound to a different BaseAdapter
            # object with the same module name. Bridge the class back onto the
            # active factory contract without changing runtime behavior.
            class BuiltinAdapterBridge(BaseAdapter):
                def execute(self, action_data, credentials, dry_run=True):
                    return adapter_cls().execute(action_data, credentials, dry_run=dry_run)

            BuiltinAdapterBridge.__name__ = adapter_cls.__name__
            BuiltinAdapterBridge.__qualname__ = adapter_cls.__qualname__
            registered_cls = BuiltinAdapterBridge

        existing = cls._registry.get(template_type)
        if existing is None or not issubclass(existing, BaseAdapter):
            cls._registry[template_type] = registered_cls

    @classmethod
    def register_adapter(cls, template_type: str, adapter_cls: type[BaseAdapter]) -> None:
        if not issubclass(adapter_cls, BaseAdapter):
            raise TypeError("adapter_cls must inherit BaseAdapter")
        cls._registry[str(template_type)] = adapter_cls

    @classmethod
    def get_adapter(cls, template_type: str) -> BaseAdapter:
        cls._ensure_builtin_adapters()
        adapter_cls = cls._registry.get(str(template_type))
        if not adapter_cls:
            raise NotImplementedError(f"No write-back adapter registered for {template_type}")
        return adapter_cls()

    @classmethod
    def has_adapter(cls, template_type: str) -> bool:
        cls._ensure_builtin_adapters()
        return str(template_type) in cls._registry

    @classmethod
    def supports(cls, template_type: str) -> bool:
        return cls.has_adapter(template_type)

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
    "create_investigation_note": {
        "template_id": "create_investigation_note",
        "cartridge_id": "platform",
        "label": "Crear nota de investigacion",
        "description": "Registra una nota interna auditada con evidencia del item.",
        "action_kind": "investigation_note",
        "risk_level": "low",
        "mode_default": "dry_run",
        "requires_approval": False,
    },
    "mark_decision_for_monitoring": {
        "template_id": "mark_decision_for_monitoring",
        "cartridge_id": "platform",
        "label": "Marcar decision para monitoreo",
        "description": "Activa seguimiento interno de la decision y sus metricas.",
        "action_kind": "decision_monitoring",
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
        "template_type": "sap_hcm_it0008",
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
    data_readiness: str = "ready"
    readiness_reason: str = ""
    readiness_blockers: tuple[str, ...] = ()
    contract_warnings: tuple[str, ...] = ()

    @property
    def visible_module_id(self) -> str:
        return self.module_id or self.cartridge


DATA_READINESS_STATES = (
    "ready",
    "partial",
    "stub",
    "empty",
    "missing",
    "unavailable",
    "invalid_schema",
    "blocked",
    "no_permission",
)
DATA_READY_STATES = {"ready"}
NON_READY_SOURCE_STATES = {"empty", "missing", "unavailable", "invalid_schema", "blocked", "no_permission"}
CONTROL_ROOM_DATASET_READINESS: dict[tuple[str, str], dict[str, Any]] = dataset_readiness_registry()


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
                entity_id_field="org_id",
                entity_label_field="org_name",
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
                entity_id_field="employee_group",
                entity_label_field="employee_group",
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
                entity_id_field="department_id",
                entity_label_field="department_name",
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
                entity_id_field="termination_month",
                entity_label_field="termination_month",
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
                entity_kind="Departamento",
                entity_id_field="department",
                entity_label_field="department",
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
    # Etapa 1 (Trabajo 1): "Desempeno" apunta al dataset REAL de performance (C/P/A), no al
    # stub de compensacion. Se mantiene el mecanismo metric_snapshot (solo cablea; el rediseno
    # a un widget de Performance es Etapa 2). Compensacion queda como modulo SEPARADO abajo.
    # DEUDA TECNICA (aprobada 2026-07-10): sap_successfactors_talent_cpa_scores es una fuente
    # TRANSITORIA para esta tarjeta. En una etapa posterior construir un dataset de negocio
    # dedicado a Desempeno en vez de reutilizar uno disenado para C/P/A. No bloquea Etapa 1.
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Desempeno",
        domain="Recursos Humanos",
        accent="#7c3aed",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_talent_cpa_scores",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Desempeno",
                entity_kind="Grupo",
                entity_id_field="department_name",
                entity_label_field="department_name",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_performance",
            ),
        ),
        module_id="sap_successfactors_performance",
        description="Senales de desempeno (C/P/A) del talento. Compensacion es un modulo aparte.",
    ),
    # Compensacion como modulo propio: conserva el dataset compensation_distribution (stub por
    # proteccion de paycomp) para que la tarjeta "Compensacion y pagos" siga respaldada y honesta,
    # sin mezclarse con Desempeno.
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Compensacion",
        domain="Recursos Humanos",
        accent="#0891b2",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_compensation_distribution",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Compensacion",
                entity_kind="Grupo",
                entity_id_field="department_id",
                entity_label_field="department_id",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_compensation",
            ),
        ),
        module_id="sap_successfactors_compensation",
        description="Distribucion de compensacion por grupo; agregacion pendiente por proteccion de paycomp.",
    ),
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Talento",
        domain="Recursos Humanos",
        accent="#0ea5e9",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_talent_employee_profile",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Talento",
                entity_kind="Empleado",
                entity_id_field="employee_key",
                entity_label_field="employee_key",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_talent",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_talent_role_profile",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Talento",
                entity_kind="Rol",
                entity_id_field="job_code",
                entity_label_field="role_name",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_talent",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_talent_readiness",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Talento",
                entity_kind="Empleado",
                entity_id_field="employee_key",
                entity_label_field="employee_key",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_talent",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_talent_9box_operational",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Talento",
                entity_kind="Caja 9-box",
                entity_id_field="box_key",
                entity_label_field="box_label",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_talent",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_talent_action_candidates",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Talento",
                entity_kind="Accion recomendada",
                entity_id_field="action_id",
                entity_label_field="title",
                kind="metric",
                normalizer="metric_snapshot",
                module_id="sap_successfactors_talent",
            ),
            ControlRoomSource(
                dataset="sap_successfactors_talent_signals",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Talento",
                entity_kind="Senal Talento",
                entity_id_field="signal_id",
                entity_label_field="title",
                kind="intelligence_signal",
                normalizer="sap_successfactors_talent_signal",
                module_id="sap_successfactors_talent",
            ),
        ),
        module_id="sap_successfactors_talent",
        description="9-box, readiness y senales WisdomBit Talento en modo recomendacion.",
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
                entity_label_field="manager_id",
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
                entity_id_field="department_id",
                entity_label_field="department_name",
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
                entity_kind="Mes",
                entity_id_field="posting_month",
                entity_label_field="posting_month",
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
                dataset="consultor_mensual",
                cartridge="replicon",
                domain="Operacion",
                module_label="Horas mensuales",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="control_item",
                normalizer="replicon_timesheet",
                module_id="replicon",
            ),
        ),
        module_id="replicon",
        description="Asignacion, horas y delivery de servicios profesionales.",
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


def _readiness_contract(source: ControlRoomSource) -> dict[str, Any]:
    registry = CONTROL_ROOM_DATASET_READINESS.get((source.cartridge, source.dataset), {})
    data_readiness = str(source.data_readiness or registry.get("data_readiness") or "ready")
    if data_readiness == "ready" and registry:
        data_readiness = str(registry.get("data_readiness") or "ready")
    if data_readiness not in DATA_READINESS_STATES:
        data_readiness = "partial"
    blockers = tuple(source.readiness_blockers or registry.get("blockers") or ())
    warnings = tuple(source.contract_warnings or registry.get("warnings") or ())
    return {
        "data_readiness": data_readiness,
        "readiness_reason": source.readiness_reason or str(registry.get("reason") or ""),
        "readiness_blockers": list(blockers),
        "contract_warnings": list(warnings),
    }


def _source_status_payload(
    source: ControlRoomSource,
    status: str,
    *,
    count: int,
    checked_at: str,
    error: str | None = None,
) -> dict[str, Any]:
    readiness = _readiness_contract(source)
    data_readiness = readiness["data_readiness"]
    blockers = list(readiness["readiness_blockers"])
    reason = str(readiness["readiness_reason"] or "")
    if status in NON_READY_SOURCE_STATES:
        data_readiness = status
        if not reason:
            reason = {
                "empty": "La consulta fue valida pero no devolvio filas para el workspace activo.",
                "missing": "El dataset no esta registrado o materializado para este workspace.",
                "unavailable": "Refinement no pudo consultar el dataset.",
                "invalid_schema": "La tabla no cumple el contrato esperado por Control Room.",
                "blocked": "El cartucho no esta activo para el workspace.",
                "no_permission": "El usuario no puede ver este cartucho en el workspace activo.",
            }.get(status, "La fuente requiere revision.")
        if status not in blockers:
            blockers.append(status)
    operationally_ready = status == "ok" and count > 0 and data_readiness in DATA_READY_STATES
    payload = {
        "dataset": source.dataset,
        "cartridge": source.cartridge,
        "connector_id": source.cartridge,
        "module_id": source.visible_module_id,
        "domain": source.domain,
        "module": source.module_label,
        "status": status,
        "count": count,
        "checked_at": checked_at,
        "data_readiness": data_readiness,
        "operationally_ready": operationally_ready,
        "readiness_reason": reason,
        "readiness_blockers": blockers,
        "contract_warnings": list(readiness["contract_warnings"]),
    }
    if error:
        payload["error"] = error
    return payload


def _readiness_counts(sources: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {state: 0 for state in DATA_READINESS_STATES}
    for source in sources:
        state = str(source.get("data_readiness") or "ready")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _module_data_readiness(module_sources: list[dict[str, Any]]) -> str:
    if not module_sources:
        return "missing"
    if all(source.get("operationally_ready") for source in module_sources):
        return "ready"
    states = {str(source.get("data_readiness") or source.get("status") or "unavailable") for source in module_sources}
    for state in ("invalid_schema", "blocked", "no_permission", "unavailable", "missing", "stub", "partial", "empty"):
        if state in states:
            return state
    return "partial"

# Implementation modules bind their functions back into this module namespace.
# That keeps the historical `app.services.control_room_service.<name>` import
# and monkeypatch surface stable while the physical code is split by domain.
def _install_module_exports() -> None:
    from app.services.control_room import api as _api
    from app.services.control_room import state as _state
    from app.services.control_room import execution as _execution

    for _module in (_api, _state, _execution):
        for _name in getattr(_module, "__all__", ()):  # pragma: no branch - static tuple
            globals()[_name] = getattr(_module, _name)


_install_module_exports()

__all__ = tuple(
    _name
    for _name in globals()
    if not _name.startswith("__") and _name not in {"_install_module_exports"}
)
