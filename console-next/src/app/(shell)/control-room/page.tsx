"use client";

import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Filter,
  Gauge,
  Layers3,
  Loader2,
  Play,
  RefreshCcw,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  TrendingUp,
  UserPlus,
  Users,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { SuccessFactorsGoldPanel } from "@/components/control-room/SuccessFactorsGoldPanel";
import {
  MiniBar,
  OperationalNotice,
  ReadinessBadge,
  readinessLabels,
  readinessTone,
} from "@/components/control-room/StatusBadge";
import { api, isApiError } from "@/lib/api";
import {
  getControlRoomActivity,
  getControlRoomDashboard,
  getControlRoomImpact,
  getControlRoomLessons,
  getControlRoomThresholds,
  getSuccessFactorsDecisionModel,
  getSuccessFactorsGoldKpis,
} from "@/lib/control-room/client";
import type { ImpactPayload, SfDecisionModelPayload } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

type Severity = "critical" | "high" | "medium" | "low";
type SourceState = "ok" | "empty" | "missing" | "unavailable" | "invalid_schema" | "blocked" | "no_permission";
type DataReadiness = "ready" | "partial" | "stub" | "empty" | "missing" | "unavailable" | "invalid_schema" | "blocked" | "no_permission" | "error";
type SourceRollup = SourceState | "partial" | "stub" | "attention" | "inactive" | "no_sources" | "error";
type LoadState = "loading" | "ready" | "error";
type DetailMode = "auto" | "manual" | null;
type AlertOperation = "ack" | "snooze" | "assign" | "false-positive";

interface SourceStatus {
  dataset: string;
  cartridge: string;
  connector_id?: string;
  module_id?: string;
  domain: string;
  module: string;
  status: SourceState;
  count: number;
  data_readiness?: DataReadiness;
  operationally_ready?: boolean;
  readiness_reason?: string;
  readiness_blockers?: string[];
  contract_warnings?: string[];
  error?: string;
  checked_at?: string;
}

interface Kpi {
  label: string;
  value: string | number;
  tone: "neutral" | "attention";
  bad?: boolean;
}

interface DomainModule {
  id: string;
  connector_id?: string;
  label: string;
  domain: string;
  accent: string;
  description?: string;
  item_count: number;
  critical_count: number;
  source_status: SourceRollup;
  data_readiness?: DataReadiness;
  operationally_ready?: boolean;
  kpis: Kpi[];
}

interface Domain {
  id: string;
  label: string;
  accent: string;
  item_count: number;
  critical_count: number;
  cartridge_count: number;
  modules: DomainModule[];
}

interface Cartridge {
  id: string;
  connector_id?: string;
  connector_label?: string;
  label: string;
  domain: string;
  accent: string;
  description?: string;
  status: string;
  current_step?: string;
  active: boolean;
  operational: boolean;
  item_count: number;
  critical_count: number;
  source_status: SourceRollup;
  data_readiness?: DataReadiness;
  operationally_ready?: boolean;
  datasets: SourceStatus[];
}

interface OmegaOption {
  id: string;
  label: string;
  action?: string;
  money?: string;
  time?: string;
  score: number;
  risk: string;
  auto?: boolean;
  recommendation: string;
  selected: boolean;
}

interface WritebackCapability {
  supported: boolean;
  status: string;
  mode?: string;
  external?: boolean;
  reason?: string;
  description?: string;
}

interface ActionTemplate {
  template_id: string;
  label: string;
  description: string;
  risk_level: string;
  mode_default: string;
  requires_approval: boolean;
  writeback?: WritebackCapability;
}

interface ImpactDriver {
  label: string;
  value?: string | number | null;
  currency?: string;
  unit?: string;
  points?: number;
}

interface DetectionThreshold {
  id?: number;
  cartridge_id: string;
  anomaly_type: string;
  metric: string;
  warning_value?: number | null;
  critical_value?: number | null;
  currency?: string;
  source?: "workspace" | "default" | string;
  enabled?: boolean;
  metadata?: Record<string, unknown>;
}

interface ThresholdPayload {
  thresholds: DetectionThreshold[];
  summary?: {
    total: number;
    active: number;
    disabled: number;
  };
}

interface SfGoldWidgetRow {
  label?: string;
  id?: string | null;
  headcount?: number;
  [key: string]: unknown;
}

interface SfGoldWidget {
  id: string;
  title: string;
  value: number;
  dataset: string;
  rows: SfGoldWidgetRow[];
}

interface SfGoldKpisPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  widgets: SfGoldWidget[];
}

interface ThresholdCandidate extends DetectionThreshold {
  key: string;
  module: string;
  module_id?: string;
  domain: string;
  title?: string;
  item_count: number;
}

interface ThresholdDraft {
  cartridge_id: string;
  anomaly_type: string;
  metric: string;
  warning_value: string;
  critical_value: string;
  currency: string;
  enabled: boolean;
}

interface Lesson {
  id?: number;
  item_id: string;
  cartridge_id: string;
  anomaly_type: string;
  rule: string;
  source_decision_id?: number | null;
  confidence?: number | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
}

interface LessonPattern {
  cartridge_id: string;
  anomaly_type: string;
  count: number;
  avg_confidence?: number | null;
  latest_rule?: string;
  last_seen_at?: string;
}

interface LessonApplication {
  lesson_id?: number;
  rule: string;
  applied_at?: string;
  applied_by?: string;
  note?: string;
}

interface ActivityEntry {
  id: string;
  kind: "event" | "execution" | "decision_action";
  type: string;
  label: string;
  status?: string;
  actor?: string;
  at?: string;
  metadata?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string | null;
}

interface ActivityPayload {
  item_id: string;
  activity: ActivityEntry[];
  counts: {
    total: number;
  };
}

interface ControlChecklistItem {
  id: string;
  desc: string;
  owner: string;
  status?: "open" | "in_progress" | "closed" | "blocked" | string;
  st: string;
  impact: string;
  days: number;
  due_at?: string;
  note?: string;
}

interface Omega {
  investigation: {
    root_cause?: string;
    impact?: string;
    evidence?: Record<string, unknown>;
  };
  options: OmegaOption[];
  decision: {
    decision_id?: number | null;
    status: string;
    label: string;
  };
  execution: {
    status?: string;
    supported_writeback_templates?: string[];
    templates?: ActionTemplate[];
    actions: Array<{ id: string; label: string; done?: boolean; approved: boolean; auto?: boolean }>;
  };
  control: {
    owner?: string;
    cadence?: string;
    status?: string;
    items?: ControlChecklistItem[];
  };
  lessons: {
    rules: string[];
    applied?: LessonApplication[];
  };
  intelligence?: IntelligencePack;
}

interface IntelligencePack {
  baseline?: {
    method?: string;
    actual_value?: number;
    expected_value?: number;
    predicted_value?: number | null;
    prediction_horizon_days?: number | null;
    prediction_method?: string | null;
    sample_count?: number;
    confidence?: number;
  };
  signal?: {
    signal_id?: string;
    metric_name?: string;
    deviation_pct?: number;
    signal_type?: string;
    signal_subtype?: string;
    prediction_horizon_days?: number | null;
    predicted_value?: number | null;
    prediction_method?: string | null;
    confidence?: number;
    summary?: string;
  };
  evidence_pack?: {
    summary?: string;
    items?: Array<{ source_type?: string; source_ref?: string; supports_hypothesis?: string; strength?: number }>;
  };
  hypotheses?: Array<{ title?: string; rationale?: string; confidence?: number }>;
  options?: Array<{ option_id?: string; label?: string; score?: number; impact_expected?: number; score_explanation?: string }>;
  outcome?: { outcome_summary?: string; prediction_error?: number; actual_value?: number; predicted_value?: number } | null;
}

interface IntelligenceOutcomeDraft {
  option_id?: string;
  action_taken: string;
  actual_value?: number;
  predicted_value?: number;
  outcome_summary?: string;
  learned_rule?: string;
}

interface ControlItem {
  id: string;
  kind: "anomaly" | "control_item" | "source_state" | "intelligence_signal";
  domain: string;
  module: string;
  module_id?: string;
  cartridge: string;
  connector_id?: string;
  source_dataset: string;
  entity_kind: string;
  entity_id: string;
  entity_label: string;
  anomaly_type: string;
  severity: Severity;
  severity_weight: number;
  title: string;
  description: string;
  detected_at: string;
  recommendation: string;
  root_cause?: string;
  impact?: string;
  sql?: string;
  status: string;
  decision_id?: number | null;
  selected_option_id?: string;
  execution_status?: string;
  impact_estimate?: number | null;
  impact_currency?: string;
  confidence?: number | null;
  priority_score?: number | null;
  priority?: {
    score: number;
    band: Severity;
    formula?: string;
    drivers?: ImpactDriver[];
  };
  impact_drivers?: ImpactDriver[];
  thresholds_applied?: DetectionThreshold[];
  related_lessons?: Lesson[];
  lesson_count?: number;
  lesson_applications?: LessonApplication[];
  action_templates?: ActionTemplate[];
  intelligence?: IntelligencePack;
  omega: Omega;
}

interface ControlAlert {
  id: string;
  item_id: string;
  alert_type: string;
  severity: Severity;
  priority_score: number;
  domain: string;
  module: string;
  module_id?: string;
  cartridge: string;
  connector_id?: string;
  source_dataset: string;
  title: string;
  message: string;
  status: string;
  owner?: string | null;
  note?: string | null;
  snoozed_until?: string | null;
  threshold_state?: string;
  lesson_count?: number;
  impact_estimate?: number | null;
  impact_currency?: string;
  recommended_action?: string;
  drivers?: ImpactDriver[];
  push_ready?: boolean;
  delivery?: {
    status: string;
    channels?: string[];
    reason?: string;
  };
  created_at?: string;
}

interface Dashboard {
  meta?: {
    generated_at?: string;
    refresh_interval_seconds?: number;
    live_mode?: "polling" | string;
    version?: string;
    app_env?: string;
    execution_mode?: string;
    supervised_execution_enabled?: boolean;
    external_writeback_enabled?: boolean;
    write_back_enabled?: boolean;
  };
  workspace: {
    workspace_id: string;
  };
  period: string;
  omega_steps: Array<{ id: string; label: string }>;
  summary: {
    total_items: number;
    critical: number;
    attention: number;
    open_decisions: number;
    active_connectors?: number;
    active_modules?: number;
    active_cartridges: number;
    operational_cartridges: number;
    source_states: Partial<Record<SourceState, number>>;
    data_readiness?: Partial<Record<DataReadiness, number>>;
    data_ready_sources?: number;
    data_ready_modules?: number;
    partial_modules?: number;
    stub_modules?: number;
    cycle_counts?: Record<string, number>;
    thresholds?: {
      active: number;
      total: number;
      items_with_thresholds?: number;
    };
    lessons?: {
      total: number;
      recent: Lesson[];
      by_cartridge?: Record<string, number>;
      top_patterns?: LessonPattern[];
    };
    alerts?: {
      total: number;
      critical: number;
      high: number;
      medium: number;
      low: number;
      push_ready: number;
      by_type?: Record<string, number>;
      top?: ControlAlert[];
    };
  };
  domains: Domain[];
  cartridges: Cartridge[];
  sources: SourceStatus[];
  alerts?: ControlAlert[];
  items: ControlItem[];
}

interface LessonsPayload {
  lessons: Lesson[];
  summary: {
    total: number;
    recent: Lesson[];
    by_cartridge?: Record<string, number>;
    top_patterns?: LessonPattern[];
  };
}

interface ActiveContext {
  level: "portfolio" | "domain" | "module";
  title: string;
  eyebrow: string;
  subtitle: string;
  domainLabel?: string;
  moduleId?: string;
  moduleLabel?: string;
}

const severityLabels: Record<Severity, string> = {
  critical: "Crítica",
  high: "Alta",
  medium: "Atención",
  low: "Baja",
};

const sourceStateLabels = readinessLabels as Record<SourceState | SourceRollup | DataReadiness, string>;

const alertOperationLabels: Record<AlertOperation, string> = {
  ack: "Alerta reconocida y enviada a investigación.",
  snooze: "Alerta pospuesta 24h; queda visible con estado operativo.",
  assign: "Alerta asignada al usuario actual.",
  "false-positive": "Alerta cerrada como falso positivo.",
};

const defaultOmegaSteps = [
  { id: "signals", label: "Señales" },
  { id: "investigation", label: "Investigación" },
  { id: "options", label: "Opciones" },
  { id: "decision", label: "Decisión" },
  { id: "execution", label: "Ejecución" },
  { id: "control", label: "Control" },
  { id: "lessons", label: "Lecciones" },
];

const manualTabs = ["Investigación", "Opciones", "Decisión", "Ejecución", "Control", "Reglas"] as const;
const manualStepIds = ["investigation", "options", "decision", "execution", "control", "lessons"] as const;
const terminalStatuses = new Set(["approved", "dismissed", "resolved"]);
const DEFAULT_REFRESH_INTERVAL_SECONDS = 30;

function parseDate(value?: string): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function timeAgo(value: Date | null, tick = 0): string {
  void tick;
  if (!value) return "sin actualizar";
  const seconds = Math.max(0, Math.floor((Date.now() - value.getTime()) / 1000));
  if (seconds < 10) return "ahora";
  if (seconds < 60) return `hace ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `hace ${hours}h`;
}

function timeUntil(value: Date | null, tick = 0): string {
  void tick;
  if (!value) return "pendiente";
  const seconds = Math.max(0, Math.ceil((value.getTime() - Date.now()) / 1000));
  if (seconds <= 1) return "ahora";
  if (seconds < 60) return `en ${seconds}s`;
  return `en ${Math.ceil(seconds / 60)}m`;
}

function fmtDate(value?: string | null): string {
  const date = parseDate(value || undefined);
  return date ? date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" }) : "Sin fecha";
}

function fmtMoney(value?: number | null, currency = "USD"): string {
  const safe = Number(value || 0);
  const prefix = currency === "USD" ? "$" : `${currency} `;
  if (Math.abs(safe) >= 1_000_000) return `${prefix}${(safe / 1_000_000).toFixed(1)}M`;
  if (Math.abs(safe) >= 1_000) return `${prefix}${(safe / 1_000).toFixed(0)}K`;
  return `${prefix}${safe.toFixed(0)}`;
}

function thresholdKey(threshold: Pick<DetectionThreshold, "cartridge_id" | "anomaly_type" | "metric">): string {
  return `${threshold.cartridge_id}:${threshold.anomaly_type}:${threshold.metric}`;
}

function thresholdLabel(threshold: Pick<DetectionThreshold, "anomaly_type" | "metric">): string {
  return `${businessTextLabel(threshold.anomaly_type)} / ${businessTextLabel(threshold.metric)}`;
}

function businessTextLabel(value?: string | null, fallback = "Información"): string {
  const raw = String(value || "").trim();
  if (!raw) return fallback;
  const normalized = raw.toLowerCase();
  const known: Record<string, string> = {
    employees_anomalies: "Riesgos de personal",
    headcount_by_department: "Plantilla por departamento",
    headcount_by_location: "Plantilla por ubicación",
    headcount_by_company: "Plantilla por compañía",
    manager_hierarchy: "Jerarquía de supervisión",
    org_structure: "Estructura organizacional",
    employee_360: "Vista de personal",
    workforce_composition: "Composición de plantilla",
    turnover_by_period: "Rotación por periodo",
    recruitment_funnel: "Embudo de reclutamiento",
    recruitment_pipeline: "Avance de reclutamiento",
    sap_successfactors: "SuccessFactors",
    replicon: "Replicon",
    hubspot: "HubSpot",
    salesforce: "Salesforce",
    sap_hcm: "SAP HCM",
    sap_s4hana: "SAP S/4HANA",
    low_margin: "Margen bajo",
    missing_manager: "Sin responsable asignado",
    source_state: "Estado de información",
    intelligence_signal: "Señal inteligente",
    control_item: "Control operativo",
    row_count: "Registros considerados",
    status: "Estado",
    state: "Estado",
    score: "Prioridad",
    "employee central": "Personal",
    recruiting: "Reclutamiento",
    recruitment: "Reclutamiento",
    performance: "Desempeño",
    "estructura org": "Estructura organizacional",
    "recursos humanos": "Recursos Humanos",
  };
  if (known[normalized]) return known[normalized];
  const withoutPrefixes = normalized
    .replace(/^sap_successfactors_/, "")
    .replace(/^sap_/, "");
  if (known[withoutPrefixes]) return known[withoutPrefixes];
  return raw
    .replace(/sap_successfactors_/gi, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function businessAreaLabel(value?: string | null): string {
  return businessTextLabel(value, "Área de negocio");
}

function businessFrontLabel(value?: string | null): string {
  const raw = String(value || "").trim();
  const normalized = raw.toLowerCase();
  if (normalized === "employee central") return "Personal";
  if (normalized.includes("recruit")) return "Reclutamiento";
  if (normalized.includes("performance") || normalized.includes("desempen")) return "Desempeño";
  if (normalized.includes("estructura") || normalized.includes("org")) return "Estructura organizacional";
  if (normalized.includes("successfactors")) return "SuccessFactors";
  return businessTextLabel(raw, "Frente de negocio");
}

function businessPeriodLabel(value?: string | null): string {
  const raw = String(value || "").trim();
  if (!raw) return "Periodo operativo";
  const months: Record<string, string> = {
    january: "enero",
    february: "febrero",
    march: "marzo",
    april: "abril",
    may: "mayo",
    june: "junio",
    july: "julio",
    august: "agosto",
    september: "septiembre",
    october: "octubre",
    november: "noviembre",
    december: "diciembre",
  };
  return raw.replace(/\b(January|February|March|April|May|June|July|August|September|October|November|December)\b/gi, (match) => months[match.toLowerCase()] || match);
}

function sanitizeBusinessCopy(value?: string | null, fallback = "Información operativa"): string {
  const raw = String(value || "").trim();
  if (!raw) return fallback;
  return raw
    .replace(/sap_successfactors_[a-z0-9_]+/gi, (match) => businessTextLabel(match))
    .replace(/\bemployee_360\b/gi, "Vista de personal")
    .replace(/\borg_structure\b/gi, "Estructura organizacional")
    .replace(/\bmanager_hierarchy\b/gi, "Jerarquía de supervisión")
    .replace(/\bheadcount_by_department\b/gi, "Plantilla por departamento")
    .replace(/\bheadcount_by_location\b/gi, "Plantilla por ubicación")
    .replace(/\bheadcount_by_company\b/gi, "Plantilla por compañía")
    .replace(/\bGold DB\b/gi, "la base ejecutiva")
    .replace(/\bRefinement\b/gi, "el proceso de actualización")
    .replace(/\bdataset\b/gi, "información")
    .replace(/\bS3\b/g, "almacenamiento interno");
}

function activeOpen(item: ControlItem): boolean {
  return !terminalStatuses.has(item.status);
}

function errorMessage(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function executionTemplate(item: ControlItem): ActionTemplate | undefined {
  return (
    item.action_templates?.find((template) => template.writeback?.supported && template.writeback.external === false) ||
    item.action_templates?.find((template) => template.writeback?.supported && template.writeback.mode === "supervised_execution") ||
    item.action_templates?.find((template) => template.writeback?.supported) ||
    item.action_templates?.[0]
  );
}

function dedupeLessons(lessons: Lesson[]): Lesson[] {
  const seen = new Set<string>();
  const output: Lesson[] = [];
  lessons.forEach((lesson) => {
    const key = String(lesson.id || `${lesson.item_id}:${lesson.rule}`);
    if (seen.has(key)) return;
    seen.add(key);
    output.push(lesson);
  });
  return output;
}

function controlRoomUrl(nextDomain: string, nextModule: string): string {
  const params = new URLSearchParams();
  if (nextModule !== "all") params.set("module", nextModule);
  else if (nextDomain !== "all") params.set("domain", nextDomain);
  const query = params.toString();
  return query ? `/control-room?${query}` : "/control-room";
}

function pushControlRoomUrl(nextDomain: string, nextModule: string): void {
  if (typeof window === "undefined") return;
  window.history.pushState(null, "", controlRoomUrl(nextDomain, nextModule));
}

function activityDescription(entry: ActivityEntry): string {
  if (entry.error) return sanitizeBusinessCopy(entry.error, "No se pudo completar la actualización.");
  if (entry.result && typeof entry.result.message === "string") return sanitizeBusinessCopy(entry.result.message, "Actualización registrada.");
  if (entry.metadata && typeof entry.metadata.note === "string" && entry.metadata.note) return sanitizeBusinessCopy(entry.metadata.note, "Nota operativa registrada.");
  return sanitizeBusinessCopy(entry.label || entry.status || entry.type, "Actividad operativa");
}

function sourceStateTone(status: SourceState | SourceRollup | DataReadiness): string {
  return readinessTone(status);
}

function severityTone(severity: Severity): string {
  if (severity === "critical") return "border-destructive/40 bg-destructive/10 text-destructive";
  if (severity === "high") return "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300";
  if (severity === "medium") return "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
}

function businessStatusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    open: "abierta",
    in_review: "en revisión",
    approved: "aprobada",
    resolved: "resuelta",
    dismissed: "descartada",
    acknowledged: "reconocida",
    snoozed: "pospuesta",
    not_configured: "pendiente",
    not_started: "pendiente",
    running: "en ejecución",
    completed: "completada",
    failed: "requiere revisión",
    blocked: "bloqueada",
  };
  if (!status) return "pendiente";
  return labels[status] || status.replace(/_/g, " ");
}

function businessDatasetLabel(value?: string | null): string {
  const text = String(value || "").toLowerCase();
  if (text.includes("employee_360")) return "Vista de personal";
  if (text.includes("org_structure")) return "Estructura organizacional";
  if (text.includes("manager_hierarchy")) return "Jerarquía de supervisión";
  if (text.includes("headcount_by_location")) return "Plantilla por ubicación";
  if (text.includes("headcount_by_department")) return "Plantilla por departamento";
  if (text.includes("headcount_by_company")) return "Plantilla por compañía";
  if (text.includes("recruitment")) return "Reclutamiento";
  if (text.includes("turnover")) return "Rotación";
  if (text.includes("anomal")) return "Riesgos de personal";
  if (text.includes("person")) return "Personas";
  if (text.includes("job")) return "Puestos y asignaciones";
  if (text.includes("department")) return "Departamentos";
  if (text.includes("location")) return "Ubicaciones";
  if (text.includes("company")) return "Compañías";
  return "Información operativa";
}

function businessItemLabel(item: ControlItem): string {
  return businessDatasetLabel(item.source_dataset || item.entity_label || item.anomaly_type || item.kind);
}

function businessItemTitle(item: ControlItem): string {
  const label = businessItemLabel(item);
  const title = item.title || "";
  if (/fuente operativa no disponible/i.test(title) || /unavailable|missing|not found/i.test(title)) {
    return `${label}: información no disponible`;
  }
  return sanitizeBusinessCopy(title, label);
}

function businessItemDescription(item: ControlItem): string {
  const label = businessItemLabel(item);
  const description = item.description || "";
  if (/no pudo consultarse|not found|unavailable|missing/i.test(description)) {
    return `${label} no pudo actualizarse; esta decisión queda con información incompleta.`;
  }
  return sanitizeBusinessCopy(description, item.recommendation || "OMEGA requiere revisión operativa.");
}

function businessSourceIssue(source: SourceStatus): string {
  if (source.data_readiness === "no_permission" || source.status === "no_permission") return "Bloqueado por permisos.";
  if (source.data_readiness === "partial") return "Datos incompletos para una decisión automática.";
  if (source.data_readiness === "missing" || source.status === "missing") return "Actualización pendiente.";
  if (source.error) return "No se pudo actualizar esta información.";
  if (source.count === 0) return "Sin registros considerados.";
  return `${source.count.toLocaleString("es-MX")} registros considerados.`;
}

export default function ControlRoomPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [domain, setDomain] = useState("all");
  const [cartridge, setCartridge] = useState("all");
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [selectedId, setSelectedId] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailMode, setDetailMode] = useState<DetailMode>(null);
  const [manualTab, setManualTab] = useState(0);
  const [busyAction, setBusyAction] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [alertActionMessage, setAlertActionMessage] = useState("");
  const [alertActionError, setAlertActionError] = useState("");
  const [activityByItem, setActivityByItem] = useState<Record<string, ActivityPayload>>({});
  const [activityLoading, setActivityLoading] = useState("");
  const [activityError, setActivityError] = useState("");
  const [impactByItem, setImpactByItem] = useState<Record<string, ImpactPayload>>({});
  const [impactLoading, setImpactLoading] = useState("");
  const [impactError, setImpactError] = useState("");
  const [lessonsPayload, setLessonsPayload] = useState<LessonsPayload | null>(null);
  const [lessonsLoading, setLessonsLoading] = useState(false);
  const [lessonsError, setLessonsError] = useState("");
  const [thresholdsPayload, setThresholdsPayload] = useState<ThresholdPayload | null>(null);
  const [thresholdsLoading, setThresholdsLoading] = useState(false);
  const [thresholdsError, setThresholdsError] = useState("");
  const [thresholdSaving, setThresholdSaving] = useState(false);
  const [thresholdSaveMessage, setThresholdSaveMessage] = useState("");
  const [thresholdSaveError, setThresholdSaveError] = useState("");
  const [sfGoldKpis, setSfGoldKpis] = useState<SfGoldKpisPayload | null>(null);
  const [sfGoldLoading, setSfGoldLoading] = useState(false);
  const [sfGoldError, setSfGoldError] = useState("");
  const [sfDecisionModel, setSfDecisionModel] = useState<SfDecisionModelPayload | null>(null);
  const [sfDecisionModelLoading, setSfDecisionModelLoading] = useState(false);
  const [sfDecisionModelError, setSfDecisionModelError] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [nextRefreshAt, setNextRefreshAt] = useState<Date | null>(null);
  const [, setRefreshing] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [clockTick, setClockTick] = useState(0);
  const [urlHydrated, setUrlHydrated] = useState(false);

  const loadDashboard = useCallback(async (preferredId?: string, background = false) => {
    if (!background) setError("");
    setRefreshing(true);
    setState((current) => (current === "ready" || background ? current : "loading"));
    try {
      const nextDashboard = await getControlRoomDashboard();
      setDashboard(nextDashboard);
      setSelectedId((current) => preferredId || current || nextDashboard.items[0]?.id || "");
      const generatedAt = parseDate(nextDashboard.meta?.generated_at) || new Date();
      const refreshSeconds = nextDashboard.meta?.refresh_interval_seconds || DEFAULT_REFRESH_INTERVAL_SECONDS;
      setLastUpdatedAt(generatedAt);
      setNextRefreshAt(new Date(Date.now() + refreshSeconds * 1000));
      setSyncError("");
      setState("ready");
    } catch (err) {
      const message = errorMessage(err, "No se pudo cargar la Sala de Control");
      if (isApiError(err) && err.status === 401 && typeof window !== "undefined") {
        window.location.href = `/login?next=${encodeURIComponent("/control-room")}`;
      }
      if (background) {
        setSyncError(message);
        setState((current) => (current === "loading" ? "error" : current));
      } else {
        setError(message);
        setState("error");
      }
    } finally {
      setRefreshing(false);
    }
  }, []);

  const loadActivity = useCallback(async (itemId: string) => {
    if (!itemId) return;
    setActivityLoading(itemId);
    setActivityError("");
    try {
      const payload = await getControlRoomActivity(itemId);
      setActivityByItem((current) => ({ ...current, [itemId]: payload }));
    } catch (err) {
      setActivityError(errorMessage(err, "No se pudo cargar la bitacora operativa"));
    } finally {
      setActivityLoading((current) => (current === itemId ? "" : current));
    }
  }, []);

  const loadImpact = useCallback(async (itemId: string) => {
    if (!itemId) return;
    setImpactLoading(itemId);
    setImpactError("");
    try {
      const payload = await getControlRoomImpact(itemId);
      setImpactByItem((current) => ({ ...current, [itemId]: payload }));
    } catch (err) {
      setImpactError(errorMessage(err, "No se pudo cargar el impacto operativo"));
    } finally {
      setImpactLoading((current) => (current === itemId ? "" : current));
    }
  }, []);

  const loadLessons = useCallback(async () => {
    setLessonsLoading(true);
    setLessonsError("");
    try {
      const selectedModule = dashboard?.cartridges.find((item) => item.id === cartridge);
      const payload = await getControlRoomLessons(selectedModule?.connector_id);
      setLessonsPayload(payload);
    } catch (err) {
      setLessonsError(errorMessage(err, "No se pudieron cargar lecciones"));
    } finally {
      setLessonsLoading(false);
    }
  }, [cartridge, dashboard?.cartridges]);

  const loadThresholds = useCallback(async () => {
    setThresholdsLoading(true);
    setThresholdsError("");
    try {
      const payload = await getControlRoomThresholds();
      setThresholdsPayload(payload);
    } catch (err) {
      setThresholdsError(errorMessage(err, "No se pudieron cargar reglas de decisión"));
    } finally {
      setThresholdsLoading(false);
    }
  }, []);

  const loadSfGoldKpis = useCallback(async () => {
    setSfGoldLoading(true);
    setSfGoldError("");
    try {
      const payload = await getSuccessFactorsGoldKpis();
      setSfGoldKpis(payload);
    } catch (err) {
      setSfGoldError(errorMessage(err, "No se pudieron cargar indicadores ejecutivos de SuccessFactors"));
    } finally {
      setSfGoldLoading(false);
    }
  }, []);

  const loadSfDecisionModel = useCallback(async () => {
    setSfDecisionModelLoading(true);
    setSfDecisionModelError("");
    try {
      const payload = await getSuccessFactorsDecisionModel();
      setSfDecisionModel(payload);
    } catch (err) {
      setSfDecisionModelError(errorMessage(err, "No se pudo cargar el modelo de decisiones de SuccessFactors"));
    } finally {
      setSfDecisionModelLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadDashboard(undefined, false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadDashboard]);

  useEffect(() => {
    const timer = window.setInterval(() => setClockTick((current) => current + 1), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (state !== "ready") return undefined;
    const refreshSeconds = dashboard?.meta?.refresh_interval_seconds || DEFAULT_REFRESH_INTERVAL_SECONDS;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void loadDashboard(selectedId, true);
        void loadLessons();
        void loadThresholds();
        void loadSfGoldKpis();
        void loadSfDecisionModel();
      }
    }, Math.max(10, refreshSeconds) * 1000);
    return () => window.clearInterval(timer);
  }, [dashboard?.meta?.refresh_interval_seconds, loadDashboard, loadLessons, loadSfDecisionModel, loadSfGoldKpis, loadThresholds, selectedId, state]);

  useEffect(() => {
    if (state !== "ready") return;
    const timer = window.setTimeout(() => {
      void loadLessons();
      void loadThresholds();
      void loadSfGoldKpis();
      void loadSfDecisionModel();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [cartridge, domain, loadLessons, loadSfDecisionModel, loadSfGoldKpis, loadThresholds, state]);

  const domains = useMemo(() => dashboard?.domains ?? [], [dashboard]);
  const cartridges = useMemo(() => dashboard?.cartridges ?? [], [dashboard]);
  const items = useMemo(() => dashboard?.items ?? [], [dashboard]);
  const activeModules = cartridges.filter((item) => item.active && !item.operational);
  const activeConnectorCount = dashboard?.summary.active_connectors
    ?? new Set(activeModules.map((item) => item.connector_id || item.id)).size;
  const activeModuleCount = dashboard?.summary.active_modules ?? activeModules.length;
  const dataReadyModuleCount = dashboard?.summary.data_ready_modules
    ?? activeModules.filter((item) => item.operationally_ready).length;
  const partialModuleCount = dashboard?.summary.partial_modules
    ?? activeModules.filter((item) => item.data_readiness === "partial").length;
  const stubModuleCount = dashboard?.summary.stub_modules
    ?? activeModules.filter((item) => item.data_readiness === "stub").length;

  useEffect(() => {
    if (urlHydrated || !dashboard) return;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      const moduleParam = params.get("module");
      const domainParam = params.get("domain");
      if (moduleParam) {
        const selectedModule = cartridges.find((item) => item.id === moduleParam);
        if (selectedModule) {
          setDomain(selectedModule.domain);
          setCartridge(selectedModule.id);
        }
      } else if (domainParam && domains.some((item) => item.label === domainParam)) {
        setDomain(domainParam);
        setCartridge("all");
      }
      setUrlHydrated(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [cartridges, dashboard, domains, urlHydrated]);

  const filtered = useMemo(() => items.filter((item) => (
    (domain === "all" || item.domain === domain)
    && (severity === "all" || item.severity === severity)
    && (cartridge === "all" || (item.module_id || item.cartridge) === cartridge)
  )), [cartridge, domain, items, severity]);

  const groupedItems = useMemo(() => domains.map((group) => ({
    domain: group,
    items: filtered.filter((item) => item.domain === group.label),
  })).filter((group) => group.items.length > 0), [domains, filtered]);

  const contextSources = useMemo(() => (dashboard?.sources ?? []).filter((source) => (
    (domain === "all" || source.domain === domain)
    && (cartridge === "all" || source.module_id === cartridge)
  )), [cartridge, dashboard?.sources, domain]);

  const contextModules = useMemo(() => cartridges.filter((item) => (
    item.active
    && !item.operational
    && (domain === "all" || item.domain === domain)
    && (cartridge === "all" || item.id === cartridge)
  )), [cartridge, cartridges, domain]);

  const contextConnectorIds = useMemo(() => new Set(contextModules.map((item) => item.connector_id || item.id)), [contextModules]);
  const contextAlerts = useMemo(() => (dashboard?.alerts ?? []).filter((alert) => (
    (domain === "all" || alert.domain === domain)
    && (cartridge === "all" || alert.module_id === cartridge)
  )), [cartridge, dashboard?.alerts, domain]);

  const contextLessons = useMemo(() => {
    const itemIds = new Set(filtered.map((item) => item.id));
    const anomalyKeys = new Set(filtered.map((item) => `${item.cartridge}:${item.anomaly_type}`));
    const itemLessons = filtered.flatMap((item) => item.related_lessons || []);
    const apiLessons = lessonsPayload?.lessons || [];
    return dedupeLessons([...itemLessons, ...apiLessons]).filter((lesson) => {
      if (domain === "all" && cartridge === "all") return true;
      if (itemIds.has(lesson.item_id)) return true;
      const key = `${lesson.cartridge_id}:${lesson.anomaly_type}`;
      if (cartridge !== "all") return anomalyKeys.has(key);
      return contextConnectorIds.has(lesson.cartridge_id) && (anomalyKeys.size === 0 || anomalyKeys.has(key));
    });
  }, [cartridge, contextConnectorIds, domain, filtered, lessonsPayload?.lessons]);

  const contextThresholds = useMemo(() => {
    const thresholds = thresholdsPayload?.thresholds || [];
    if (domain === "all" && cartridge === "all") return thresholds;
    return thresholds.filter((threshold) => contextConnectorIds.has(threshold.cartridge_id));
  }, [cartridge, contextConnectorIds, domain, thresholdsPayload?.thresholds]);

  const contextThresholdCandidates = useMemo(() => {
    const byKey = new Map<string, ThresholdCandidate>();
    filtered.forEach((item) => {
      (item.thresholds_applied || []).forEach((threshold) => {
        const key = thresholdKey(threshold);
        const current = byKey.get(key);
        byKey.set(key, {
          ...threshold,
          key,
          module: item.module,
          module_id: item.module_id,
          domain: item.domain,
          title: current?.title || item.title,
          item_count: (current?.item_count || 0) + 1,
        });
      });
    });
    contextThresholds.forEach((threshold) => {
      const key = thresholdKey(threshold);
      if (byKey.has(key)) return;
      const relatedModule = contextModules.find((module) => module.connector_id === threshold.cartridge_id);
      byKey.set(key, {
        ...threshold,
        key,
        module: relatedModule?.label || threshold.cartridge_id,
        module_id: relatedModule?.id,
        domain: relatedModule?.domain || domain,
        item_count: 0,
      });
    });
    return [...byKey.values()].sort((left, right) => (
      left.domain.localeCompare(right.domain)
      || left.module.localeCompare(right.module)
      || left.anomaly_type.localeCompare(right.anomaly_type)
      || left.metric.localeCompare(right.metric)
    ));
  }, [contextModules, contextThresholds, domain, filtered]);

  const visibleDomains = useMemo(() => domains
    .filter((item) => item.modules.length > 0)
    .filter((item) => domain === "all" || item.label === domain)
    .map((item) => {
      if (cartridge === "all") return item;
      const modules = item.modules.filter((module) => module.id === cartridge);
      return {
        ...item,
        modules,
        item_count: modules.reduce((sum, module) => sum + module.item_count, 0),
        critical_count: modules.reduce((sum, module) => sum + module.critical_count, 0),
        cartridge_count: modules.length,
      };
    })
    .filter((item) => item.modules.length > 0), [cartridge, domain, domains]);

  const selectedDomain = domains.find((item) => item.label === domain);
  const selectedModule = cartridges.find((item) => item.id === cartridge);
  const activeContext: ActiveContext = selectedModule && cartridge !== "all" ? {
    level: "module",
    title: businessFrontLabel(selectedModule.label),
    eyebrow: "Frente operativo",
    subtitle: `${businessAreaLabel(selectedModule.domain)} · ${businessFrontLabel(selectedModule.connector_label || selectedModule.label)}`,
    domainLabel: businessAreaLabel(selectedModule.domain),
    moduleId: selectedModule.id,
    moduleLabel: businessFrontLabel(selectedModule.label),
  } : selectedDomain && domain !== "all" ? {
    level: "domain",
    title: businessAreaLabel(selectedDomain.label),
    eyebrow: "Área de negocio",
    subtitle: `${selectedDomain.cartridge_count} frentes con señales · ${filtered.length} señales`,
    domainLabel: businessAreaLabel(selectedDomain.label),
  } : {
    level: "portfolio",
    title: "Dashboard Operativo",
    eyebrow: "Sala de Control OMEGA",
    subtitle: `${activeConnectorCount} conectores activos · ${activeModuleCount} frentes con señales · ${dataReadyModuleCount} listos para decidir`,
  };

  const selected = filtered.find((item) => item.id === selectedId)
    || items.find((item) => item.id === selectedId)
    || filtered[0]
    || null;

  useEffect(() => {
    if (!detailOpen || !selected?.id) return;
    const timer = window.setTimeout(() => {
      void loadActivity(selected.id);
      void loadImpact(selected.id);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [detailOpen, loadActivity, loadImpact, selected?.id]);

  function mergeDashboardItem(nextItem: ControlItem) {
    setSelectedId(nextItem.id);
    setDashboard((current) => {
      if (!current) return current;
      const exists = current.items.some((item) => item.id === nextItem.id);
      return {
        ...current,
        items: exists
          ? current.items.map((item) => (item.id === nextItem.id ? nextItem : item))
          : [nextItem, ...current.items],
      };
    });
  }

  function refreshAfterMutation(nextItem: ControlItem) {
    mergeDashboardItem(nextItem);
    void loadDashboard(nextItem.id, true);
    void loadActivity(nextItem.id);
    void loadImpact(nextItem.id);
    void loadLessons();
    void loadThresholds();
  }

  function navigateAll() {
    setDomain("all");
    setCartridge("all");
    setDetailOpen(false);
    pushControlRoomUrl("all", "all");
  }

  function navigateDomain(nextDomain: string) {
    if (nextDomain === "all") {
      navigateAll();
      return;
    }
    setDomain(nextDomain);
    setCartridge("all");
    setDetailOpen(false);
    pushControlRoomUrl(nextDomain, "all");
  }

  function navigateModule(nextModule: string, nextDomain: string) {
    setDomain(nextDomain);
    setCartridge(nextModule);
    setDetailOpen(false);
    pushControlRoomUrl(nextDomain, nextModule);
  }

  function openItem(item: ControlItem) {
    setSelectedId(item.id);
    setDetailOpen(true);
    setDetailMode(null);
    setManualTab(0);
    setActionMessage("");
    setActionError("");
    void recordStep(item, "signals", "Señal abierta desde la Sala de Control", undefined, true);
  }

  function toggleDomain(domainId: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(domainId)) next.delete(domainId);
      else next.add(domainId);
      return next;
    });
  }

  async function mutateItem(
    key: string,
    fallback: string,
    request: () => Promise<ControlItem>,
    success: string,
  ) {
    setBusyAction(key);
    setActionError("");
    setActionMessage("");
    try {
      const item = await request();
      refreshAfterMutation(item);
      const displayMessage = success === "preview_generated" || success === "dry_run_validated"
        ? "Ejecución actualizada"
        : success;
      setActionMessage(displayMessage);
      toast.success("Acción completada");
    } catch (err) {
      const message = errorMessage(err, fallback);
      setActionError(message);
      toast.error(message);
    } finally {
      setBusyAction("");
    }
  }

  async function saveThreshold(draft: ThresholdDraft) {
    setThresholdSaving(true);
    setThresholdSaveError("");
    setThresholdSaveMessage("");
    try {
      const response = await api.patch<{ threshold: DetectionThreshold }>("/api/control-room/thresholds", {
        cartridge_id: draft.cartridge_id,
        anomaly_type: draft.anomaly_type,
        metric: draft.metric,
        warning_value: draft.warning_value === "" ? null : Number(draft.warning_value),
        critical_value: draft.critical_value === "" ? null : Number(draft.critical_value),
        currency: draft.currency || "USD",
        enabled: draft.enabled,
        metadata: { source: "control_room_ui" },
      });
      const message = `Regla guardada: ${thresholdLabel(response.data.threshold)}`;
      setThresholdSaveMessage(message);
      toast.success("Cambios guardados");
      void loadThresholds();
      void loadDashboard(selectedId, true);
    } catch (err) {
      const message = errorMessage(err, "No se pudo guardar la regla de decisión");
      setThresholdSaveError(message);
      toast.error(message);
    } finally {
      setThresholdSaving(false);
    }
  }

  async function recordStep(
    item: ControlItem,
    stepId: string,
    note = "",
    controlId?: string,
    silent = false,
  ) {
    const run = async () => {
      const response = await api.post<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/step`,
        { step_id: stepId, note, control_id: controlId || "" },
      );
      return response.data.item;
    };
    if (silent) {
      try {
        const nextItem = await run();
        mergeDashboardItem(nextItem);
        void loadActivity(nextItem.id);
      } catch {
        // Opening a detail panel must not fail because telemetry could not be recorded.
      }
      return;
    }
    await mutateItem(`step:${item.id}:${stepId}:${controlId || ""}`, "No se pudo registrar el paso OMEGA", run, stepId === "investigation" ? "Investigación revisada" : "Paso registrado");
  }

  async function selectOption(item: ControlItem, optionId: string) {
    await mutateItem(
      `option:${item.id}:${optionId}`,
      "No se pudo seleccionar la opción",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/option`,
          { option_id: optionId },
        );
        return response.data.item;
      },
      "Opción seleccionada",
    );
  }

  async function createDecision(item: ControlItem) {
    await mutateItem(
      `decision:${item.id}`,
      "No se pudo crear la decisión",
      async () => {
        const response = await api.post<{ item: ControlItem }>(`/api/control-room/items/${encodeURIComponent(item.id)}/decision`, {});
        return response.data.item;
      },
      "Decisión creada",
    );
  }

  async function previewAction(item: ControlItem) {
    const template = executionTemplate(item);
    await mutateItem(
      `preview:${item.id}`,
      "No se pudo generar la vista previa",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/action-preview`,
          { template_id: template?.template_id },
        );
        return response.data.item;
      },
      "preview_generated",
    );
  }

  async function dryRunAction(item: ControlItem) {
    const template = executionTemplate(item);
    await mutateItem(
      `dryrun:${item.id}`,
      "No se pudo validar antes de ejecutar",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/action-dry-run`,
          { template_id: template?.template_id },
        );
        return response.data.item;
      },
      "dry_run_validated",
    );
  }

  async function executeLive(item: ControlItem) {
    const template = executionTemplate(item);
    if (!template?.writeback?.supported) {
      const message = template?.writeback?.reason || "Sin ejecución supervisada para esta señal.";
      setActionError(message);
      return;
    }
    const confirmed = window.confirm("Registrar ejecución supervisada auditada. No se escribirá en ERP/SAP externo.");
    if (!confirmed) return;
    await mutateItem(
      `execute:${item.id}`,
      "Ejecución supervisada bloqueada",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/execute`,
          {
            template_id: template.template_id,
            confirm_execute: true,
            idempotency_key: `${item.id}:${template.template_id}`,
          },
        );
        return response.data.item;
      },
      "Ejecución supervisada registrada",
    );
  }

  async function runAuto(item: ControlItem) {
    await mutateItem(
      `auto:${item.id}`,
      "No se pudo completar el modo automático seguro",
      async () => {
        const response = await api.post<{ item: ControlItem }>(`/api/control-room/items/${encodeURIComponent(item.id)}/auto-run`, {});
        return response.data.item;
      },
      "Modo automático completado",
    );
  }

  async function approve(item: ControlItem) {
    await mutateItem(
      `approve:${item.id}`,
      "No se pudo aprobar la recomendación",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/approve`,
          item.decision_id ? { decision_id: item.decision_id } : {},
        );
        return response.data.item;
      },
      "Aprobación registrada",
    );
  }

  async function dismiss(item: ControlItem) {
    await mutateItem(
      `dismiss:${item.id}`,
      "No se pudo descartar la señal",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/dismiss`,
          { reason: "Descartado desde Sala de Control" },
        );
        return response.data.item;
      },
      "Señal descartada",
    );
  }

  async function updateControl(item: ControlItem, control: ControlChecklistItem, status: "in_progress" | "closed" | "blocked") {
    await mutateItem(
      `control:${item.id}:${control.id}:${status}`,
      "No se pudo actualizar el control",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/control/${encodeURIComponent(control.id)}`,
          {
            status,
            owner: control.owner,
            days: control.days || 7,
            note: status === "closed" ? `Control ${control.id} confirmado desde Sala de Control` : `Control ${control.id} actualizado desde Sala de Control`,
          },
        );
        return response.data.item;
      },
      status === "closed" ? "Control confirmado" : "Control actualizado",
    );
  }

  async function createLesson(item: ControlItem, rule: string) {
    await mutateItem(
      `lesson:${item.id}`,
      "No se pudo guardar la lección",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/lessons`,
          { rule },
        );
        return response.data.item;
      },
      "Lección registrada",
    );
  }

  async function recordIntelligenceOutcome(item: ControlItem, draft: IntelligenceOutcomeDraft) {
    await mutateItem(
      `intelOutcome:${item.id}`,
      "No se pudo registrar el resultado",
      async () => {
        const response = await api.post<{ outcome: NonNullable<IntelligencePack["outcome"]> }>(
          `/api/intelligence/signals/${encodeURIComponent(item.id)}/outcome`,
          draft,
        );
        const intelligence = item.intelligence || item.omega.intelligence || {};
        const nextIntelligence = { ...intelligence, outcome: response.data.outcome };
        return {
          ...item,
          intelligence: nextIntelligence,
          omega: { ...item.omega, intelligence: nextIntelligence },
        };
      },
      "Resultado registrado",
    );
  }

  async function applyLesson(item: ControlItem, lesson: Lesson) {
    if (!lesson.id) return;
    await mutateItem(
      `applyLesson:${item.id}:${lesson.id}`,
      "No se pudo aplicar la lección",
      async () => {
        const response = await api.post<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(item.id)}/lessons/${lesson.id}/apply`,
          { note: "Aplicada desde Sala de Control" },
        );
        return response.data.item;
      },
      "Lección aplicada",
    );
  }

  async function operateAlert(alert: ControlAlert, operation: AlertOperation) {
    const bodyByOperation: Record<AlertOperation, Record<string, unknown>> = {
      ack: { note: "Reconocida desde cola operativa" },
      snooze: { hours: 24, note: "Pospuesta 24h desde cola operativa" },
      assign: { note: "Asignada desde cola operativa" },
      "false-positive": { reason: "Marcado falso positivo desde Sala de Control" },
    };
    setBusyAction(`alert:${operation}:${alert.item_id}`);
    setAlertActionError("");
    setAlertActionMessage("");
    try {
      const response = await api.post<{ alert?: ControlAlert | null; item: ControlItem }>(
        `/api/control-room/alerts/${encodeURIComponent(alert.item_id)}/${operation}`,
        bodyByOperation[operation],
      );
      const message = alertOperationLabels[operation];
      setAlertActionMessage(message);
      toast.success("Alerta actualizada");
      refreshAfterMutation(response.data.item);
    } catch (err) {
      const message = errorMessage(err, "No se pudo operar la alerta");
      setAlertActionError(message);
      toast.error(message);
    } finally {
      setBusyAction("");
    }
  }

  const contextDataReadySources = useMemo(
    () => contextSources.filter((source) => source.operationally_ready).length,
    [contextSources],
  );

  const contextCycleCounts = useMemo(() => {
    if (domain === "all" && cartridge === "all" && severity === "all") return dashboard?.summary.cycle_counts;
    const counts = Object.fromEntries(defaultOmegaSteps.map((step) => [step.id, 0])) as Record<string, number>;
    filtered.forEach((item) => {
      counts.signals += 1;
      if (item.status === "open" || item.status === "in_review") counts.investigation += 1;
      if (item.status === "in_review" || item.selected_option_id) counts.options += 1;
      if (item.decision_id || ["decision_created", "approved", "resolved"].includes(item.status)) counts.decision += 1;
      if (item.execution_status && item.execution_status !== "not_started") counts.execution += 1;
      if (item.status === "approved" || item.status === "resolved") counts.control += 1;
      if ((item.lesson_count || 0) > 0) counts.lessons += 1;
    });
    return counts;
  }, [cartridge, dashboard?.summary.cycle_counts, domain, filtered, severity]);

  const contextCritical = filtered.filter((item) => item.severity === "critical").length;
  const contextAttention = filtered.filter((item) => item.severity === "high" || item.severity === "medium").length;
  const contextDecisionCount = domain === "all" && cartridge === "all" && severity === "all"
    ? dashboard?.summary.open_decisions ?? 0
    : filtered.filter((item) => Boolean(item.decision_id)).length;

  function refreshAll() {
    void Promise.allSettled([
      loadDashboard(selectedId, false),
      loadLessons(),
      loadThresholds(),
      loadSfGoldKpis(),
    ]);
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950 dark:bg-[#050a12] dark:text-slate-100">
      <div className="mx-auto max-w-[1900px] space-y-4 px-4 py-4 sm:px-5 lg:px-6">
        <Header
        context={activeContext}
        period={businessPeriodLabel(dashboard?.period || "Periodo operativo")}
        activeConnectors={activeConnectorCount}
        activeModules={activeModuleCount}
        dataReadyModules={dataReadyModuleCount}
        partialModules={partialModuleCount}
        stubModules={stubModuleCount}
        loading={state === "loading"}
        lastUpdated={timeAgo(lastUpdatedAt, clockTick)}
        nextRefresh={timeUntil(nextRefreshAt, clockTick)}
        syncError={syncError}
        liveMode={dashboard?.meta?.live_mode || "polling"}
        refreshSeconds={dashboard?.meta?.refresh_interval_seconds || DEFAULT_REFRESH_INTERVAL_SECONDS}
        version={dashboard?.meta?.version}
        appEnv={dashboard?.meta?.app_env}
        writeBackEnabled={dashboard?.meta?.write_back_enabled ?? false}
        onRefresh={refreshAll}
        onAll={navigateAll}
        onDomain={navigateDomain}
      />

      {state === "error" ? (
        <OperationalNotice tone="error" title="No se pudo cargar la Sala de Control">{error}</OperationalNotice>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[270px_minmax(0,1fr)]">
        <Sidebar
          domains={domains}
          cartridges={cartridges}
          totalItems={dashboard?.summary.total_items ?? 0}
          activeConnectors={activeConnectorCount}
          activeModules={activeModuleCount}
          dataReadyModules={dataReadyModuleCount}
          domain={domain}
          cartridge={cartridge}
          onAll={navigateAll}
          onDomain={navigateDomain}
          onCartridge={navigateModule}
        />

        {detailOpen && selected ? (
          <DetailPage
            item={selected}
            omegaSteps={dashboard?.omega_steps ?? defaultOmegaSteps}
            mode={detailMode}
            manualTab={manualTab}
            busyAction={busyAction}
            actionMessage={actionMessage}
            actionError={actionError}
            activity={activityByItem[selected.id]}
            activityLoading={activityLoading === selected.id}
            activityError={activityError}
            impact={impactByItem[selected.id]}
            impactLoading={impactLoading === selected.id}
            impactError={impactError}
            onBack={() => {
              setDetailOpen(false);
              setDetailMode(null);
              setManualTab(0);
            }}
            onMode={setDetailMode}
            onManualTab={setManualTab}
            onCreateDecision={createDecision}
            onSelectOption={selectOption}
            onRecordStep={recordStep}
            onUpdateControl={updateControl}
            onPreview={previewAction}
            onDryRun={dryRunAction}
            onExecute={executeLive}
            onRunAuto={runAuto}
            onApprove={approve}
            onDismiss={dismiss}
            onCreateLesson={createLesson}
            onRecordIntelligenceOutcome={recordIntelligenceOutcome}
            onApplyLesson={applyLesson}
          />
        ) : (
          <DashboardView
            dashboard={dashboard}
            state={state}
            allDomains={domains}
            visibleDomains={visibleDomains}
            context={activeContext}
            contextSources={contextSources}
            contextDataReadySources={contextDataReadySources}
            contextModules={contextModules}
            contextLessons={contextLessons}
            contextAlerts={contextAlerts}
            lessonsLoading={lessonsLoading}
            lessonsError={lessonsError}
            contextThresholds={contextThresholds}
            thresholdCandidates={contextThresholdCandidates}
            thresholdsLoading={thresholdsLoading}
            thresholdsError={thresholdsError}
            thresholdSaving={thresholdSaving}
            thresholdSaveError={thresholdSaveError}
            thresholdSaveMessage={thresholdSaveMessage}
            alertActionError={alertActionError}
            alertActionMessage={alertActionMessage}
            busyAction={busyAction}
            contextCycleCounts={contextCycleCounts}
            contextCritical={contextCritical}
            contextAttention={contextAttention}
            contextDecisionCount={contextDecisionCount}
            sfGoldKpis={sfGoldKpis}
            sfGoldLoading={sfGoldLoading}
            sfGoldError={sfGoldError}
            sfDecisionModel={sfDecisionModel}
            sfDecisionModelLoading={sfDecisionModelLoading}
            sfDecisionModelError={sfDecisionModelError}
            domain={domain}
            cartridge={cartridge}
            severity={severity}
            groupedItems={groupedItems}
            filteredCount={filtered.length}
            openCount={filtered.filter(activeOpen).length}
            collapsed={collapsed}
            onDomain={navigateDomain}
            onCartridge={navigateModule}
            onSeverity={setSeverity}
            onSaveThreshold={saveThreshold}
            onOperateAlert={operateAlert}
            onToggleDomain={toggleDomain}
            onOpenItem={openItem}
          />
        )}
      </div>
      </div>
    </main>
  );
}

function Header({
  context,
  period,
  activeConnectors,
  activeModules,
  dataReadyModules,
  partialModules,
  stubModules,
  loading,
  lastUpdated,
  nextRefresh,
  syncError,
  liveMode,
  refreshSeconds,
  version,
  appEnv,
  writeBackEnabled,
  onRefresh,
  onAll,
  onDomain,
}: {
  context: ActiveContext;
  period: string;
  activeConnectors: number;
  activeModules: number;
  dataReadyModules: number;
  partialModules: number;
  stubModules: number;
  loading: boolean;
  lastUpdated: string;
  nextRefresh: string;
  syncError: string;
  liveMode: string;
  refreshSeconds: number;
  version?: string;
  appEnv?: string;
  writeBackEnabled?: boolean;
  onRefresh: () => void;
  onAll: () => void;
  onDomain: (domain: string) => void;
}) {
  return (
    <header className="overflow-hidden rounded-xl border bg-card shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_40px_rgba(14,165,233,0.10)]">
      <div className="border-b bg-gradient-to-r from-slate-100 via-white to-slate-50 p-4 dark:border-sky-400/20 dark:from-[#0a192b] dark:via-[#081423] dark:to-[#07111e]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 space-y-2">
            <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">{context.eyebrow}</p>
            <h1 className="text-4xl font-semibold tracking-tight text-foreground dark:text-white">{context.title}</h1>
            <nav className="flex flex-wrap items-center gap-1 text-sm text-muted-foreground" aria-label="Ruta de navegación">
              <button type="button" className="rounded-md px-1.5 py-1 hover:bg-cyan-400/10 hover:text-cyan-700 dark:text-cyan-100" onClick={onAll}>
                Sala de Control
              </button>
              <span>/</span>
              {context.level === "portfolio" ? (
                <span className="rounded-md px-1.5 py-1 text-foreground dark:text-white" aria-current="page">Todos</span>
              ) : context.level === "domain" ? (
                <span className="rounded-md px-1.5 py-1 text-foreground dark:text-white" aria-current="page">{context.domainLabel}</span>
              ) : (
                <>
                  <button
                    type="button"
                    className="rounded-md px-1.5 py-1 hover:bg-cyan-400/10 hover:text-cyan-700 dark:text-cyan-100"
                    onClick={() => context.domainLabel && onDomain(context.domainLabel)}
                  >
                    {context.domainLabel}
                  </button>
                  <span>/</span>
                  <span className="rounded-md px-1.5 py-1 text-foreground dark:text-white" aria-current="page">{context.moduleLabel}</span>
                </>
              )}
              <span>· {period}</span>
            </nav>
            <p className="max-w-3xl text-sm text-muted-foreground dark:text-slate-300">{context.subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex min-h-[44px] shrink-0 items-center justify-center gap-2 rounded-md border border-cyan-300/30 bg-cyan-400/10 px-3 text-sm font-medium text-cyan-700 dark:text-cyan-100 shadow-[0_0_18px_rgba(34,211,238,0.12)] hover:bg-cyan-400/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:opacity-50"
          >
            {loading ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <RefreshCcw aria-hidden className="h-4 w-4" />}
            Refrescar
          </button>
        </div>
      </div>
      <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-5">
        <div className="rounded-md border bg-background dark:border-sky-400/20 dark:bg-[#07111e] p-3">
          <p className="text-xs font-semibold uppercase text-muted-foreground">Versión</p>
          <p className="mt-1 text-sm font-medium text-foreground dark:text-white">Beta{version ? ` ${version}` : ""}{appEnv ? ` · ${appEnv}` : ""}</p>
        </div>
        <div className="rounded-md border bg-background dark:border-sky-400/20 dark:bg-[#07111e] p-3">
          <p className="text-xs font-semibold uppercase text-muted-foreground">Ejecución</p>
          <ReadinessBadge status={writeBackEnabled ? "ready" : "partial"} label={writeBackEnabled ? "Ejecución externa habilitada" : "Supervisada"} compact className="mt-1" />
        </div>
        <div className="rounded-md border bg-background dark:border-sky-400/20 dark:bg-[#07111e] p-3">
          <p className="text-xs font-semibold uppercase text-muted-foreground">Actualización automática</p>
          <p className="mt-1 inline-flex items-center gap-1 text-sm font-medium text-foreground dark:text-white">
            <Activity aria-hidden className="h-3.5 w-3.5" />
            {syncError ? "Con alerta" : `${liveMode === "polling" ? "Vivo" : liveMode} ${refreshSeconds}s`}
          </p>
        </div>
        <div className="rounded-md border bg-background dark:border-sky-400/20 dark:bg-[#07111e] p-3">
          <p className="text-xs font-semibold uppercase text-muted-foreground">Actualización</p>
          <p className="mt-1 text-sm font-medium text-foreground dark:text-white">{lastUpdated} · siguiente {nextRefresh}</p>
        </div>
        <div className="rounded-md border bg-background dark:border-sky-400/20 dark:bg-[#07111e] p-3">
          <p className="text-xs font-semibold uppercase text-muted-foreground">Cobertura</p>
          <p className="mt-1 text-sm font-medium text-foreground dark:text-white">{activeConnectors} conectores · {activeModules} frentes con señales · {dataReadyModules} listos</p>
          {partialModules || stubModules ? <p className="mt-1 text-xs text-amber-600">{partialModules} incompletos · {stubModules} sin información suficiente</p> : null}
        </div>
      </div>
      {syncError ? <div className="border-t p-4"><OperationalNotice tone="warning" title="Última actualización fallida">{syncError}</OperationalNotice></div> : null}
    </header>
  );
}

function Sidebar({
  domains,
  cartridges,
  totalItems,
  activeConnectors,
  activeModules,
  dataReadyModules,
  domain,
  cartridge,
  onAll,
  onDomain,
  onCartridge,
}: {
  domains: Domain[];
  cartridges: Cartridge[];
  totalItems: number;
  activeConnectors: number;
  activeModules: number;
  dataReadyModules: number;
  domain: string;
  cartridge: string;
  onAll: () => void;
  onDomain: (domain: string) => void;
  onCartridge: (cartridge: string, domain: string) => void;
}) {
  return (
    <aside className="space-y-4 rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)] xl:sticky xl:top-20 xl:self-start" aria-label="Navegación operativa">
      <div>
        <p className="text-sm font-semibold text-foreground dark:text-white">OMEGA</p>
        <p className="text-xs text-cyan-700 dark:text-cyan-300/80">Sala de Control</p>
      </div>
      <button
        type="button"
        className={cn("flex min-h-[44px] w-full items-center justify-between rounded-md border px-3 text-sm font-medium", domain === "all" && cartridge === "all" ? "border-cyan-400/40 bg-cyan-500/15 text-cyan-950 dark:text-cyan-50" : "bg-background text-muted-foreground hover:bg-cyan-500/10 hover:text-cyan-950 dark:border-sky-400/15 dark:bg-[#07111e] dark:text-slate-300 dark:hover:text-cyan-50")}
        onClick={onAll}
      >
        <span className="flex items-center gap-2"><Gauge aria-hidden className="h-4 w-4" />Todos</span>
        <span>{totalItems}</span>
      </button>
      <div className="space-y-4">
        {domains.filter((group) => group.modules.length > 0).map((group) => (
          <section key={group.id} className="space-y-2">
            <button
              type="button"
              aria-label={`Área ${businessAreaLabel(group.label)} ${group.item_count}`}
              className={cn("flex min-h-[44px] w-full items-center justify-between rounded-md px-3 text-sm font-medium", domain === group.label ? "bg-cyan-400/15 text-cyan-950 dark:text-cyan-50" : "text-muted-foreground hover:bg-cyan-400/10 hover:text-cyan-950 dark:text-slate-300 dark:hover:text-cyan-50")}
              onClick={() => onDomain(group.label)}
            >
              <span>{businessAreaLabel(group.label)}</span>
              <span>{group.item_count}</span>
            </button>
            <div className="space-y-1">
              {group.modules.map((module) => {
                const installed = cartridges.find((item) => item.id === module.id);
                return (
                  <button
                    type="button"
                    key={`${group.id}-${module.id}`}
                    aria-label={`Frente ${businessFrontLabel(module.label)} ${module.item_count}`}
                    data-control-module-id={module.id}
                    className={cn("flex min-h-[40px] w-full items-center justify-between rounded-md px-3 text-left text-xs disabled:opacity-50", cartridge === module.id ? "bg-cyan-400/15 text-cyan-950 dark:text-cyan-50" : "text-muted-foreground hover:bg-cyan-400/10 hover:text-cyan-950 dark:text-slate-300 dark:hover:text-cyan-50")}
                    onClick={() => onCartridge(module.id, group.label)}
                    disabled={installed ? !installed.active : false}
                  >
                    <span>{businessFrontLabel(module.label)}</span>
                    <span>{module.item_count}</span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      <p className="border-t dark:border-sky-400/20 pt-3 text-xs text-muted-foreground">{activeConnectors} conectores · {activeModules} frentes con señales · {dataReadyModules} listos para decidir</p>
    </aside>
  );
}

function DashboardView({
  dashboard,
  state,
  allDomains,
  visibleDomains,
  context,
  contextSources,
  contextDataReadySources,
  contextModules,
  contextLessons,
  contextAlerts,
  lessonsLoading,
  lessonsError,
  contextThresholds,
  thresholdCandidates,
  thresholdsLoading,
  thresholdsError,
  thresholdSaving,
  thresholdSaveError,
  thresholdSaveMessage,
  alertActionError,
  alertActionMessage,
  busyAction,
  contextCycleCounts,
  contextCritical,
  contextAttention,
  contextDecisionCount,
  sfGoldKpis,
  sfGoldLoading,
  sfGoldError,
  sfDecisionModel,
  sfDecisionModelLoading,
  sfDecisionModelError,
  domain,
  cartridge,
  severity,
  groupedItems,
  filteredCount,
  openCount,
  collapsed,
  onDomain,
  onCartridge,
  onSeverity,
  onSaveThreshold,
  onOperateAlert,
  onToggleDomain,
  onOpenItem,
}: {
  dashboard: Dashboard | null;
  state: LoadState;
  allDomains: Domain[];
  visibleDomains: Domain[];
  context: ActiveContext;
  contextSources: SourceStatus[];
  contextDataReadySources: number;
  contextModules: Cartridge[];
  contextLessons: Lesson[];
  contextAlerts: ControlAlert[];
  lessonsLoading: boolean;
  lessonsError: string;
  contextThresholds: DetectionThreshold[];
  thresholdCandidates: ThresholdCandidate[];
  thresholdsLoading: boolean;
  thresholdsError: string;
  thresholdSaving: boolean;
  thresholdSaveError: string;
  thresholdSaveMessage: string;
  alertActionError: string;
  alertActionMessage: string;
  busyAction: string;
  contextCycleCounts?: Record<string, number>;
  contextCritical: number;
  contextAttention: number;
  contextDecisionCount: number;
  sfGoldKpis: SfGoldKpisPayload | null;
  sfGoldLoading: boolean;
  sfGoldError: string;
  sfDecisionModel: SfDecisionModelPayload | null;
  sfDecisionModelLoading: boolean;
  sfDecisionModelError: string;
  domain: string;
  cartridge: string;
  severity: Severity | "all";
  groupedItems: Array<{ domain: Domain; items: ControlItem[] }>;
  filteredCount: number;
  openCount: number;
  collapsed: Set<string>;
  onDomain: (domain: string) => void;
  onCartridge: (cartridge: string, domain: string) => void;
  onSeverity: (severity: Severity | "all") => void;
  onSaveThreshold: (draft: ThresholdDraft) => void;
  onOperateAlert: (alert: ControlAlert, operation: AlertOperation) => void;
  onToggleDomain: (domainId: string) => void;
  onOpenItem: (item: ControlItem) => void;
}) {
  const contextIsPortfolio = context.level === "portfolio" && severity === "all";
  const contextItems = groupedItems.flatMap((group) => group.items);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3 shadow-sm dark:border-sky-400/20 dark:bg-[#081423]" role="group" aria-label="Filtro por dominio">
        <button type="button" className={filterButtonClass(domain === "all")} onClick={() => onDomain("all")}>Todos</button>
        {allDomains.filter((item) => item.modules.length > 0).map((item) => (
          <button type="button" key={item.label} className={filterButtonClass(domain === item.label)} onClick={() => onDomain(item.label)}>
            {businessAreaLabel(item.label)} <span className="ml-1 text-muted-foreground">{item.item_count}</span>
          </button>
        ))}
        <label className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background dark:border-sky-400/20 dark:bg-[#07111e] px-3 text-sm text-foreground dark:text-slate-200">
          <Filter aria-hidden className="h-4 w-4" />
          <select value={severity} onChange={(event) => onSeverity(event.target.value as Severity | "all")} className="bg-transparent focus-visible:outline-none" aria-label="Severidad">
            <option value="all">Todas</option>
            <option value="critical">Crítica</option>
            <option value="high">Alta</option>
            <option value="medium">Atención</option>
            <option value="low">Baja</option>
          </select>
        </label>
      </div>

      <ExecutiveCommandStrip
        totalItems={contextIsPortfolio ? dashboard?.summary.total_items ?? 0 : filteredCount}
        critical={contextIsPortfolio ? dashboard?.summary.critical ?? 0 : contextCritical}
        attention={contextIsPortfolio ? dashboard?.summary.attention ?? 0 : contextAttention}
        decisionCount={contextDecisionCount}
        sources={contextSources}
        items={contextItems}
        sfGoldKpis={sfGoldKpis}
      />

      <section className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_390px]" aria-label="Centro de mando visual">
        <div className="space-y-4">
          <ContextPanel context={context} sourceCount={contextSources.length} moduleCount={contextModules.length} itemCount={filteredCount} openCount={openCount} severity={severity} />
          <ContextOperations context={context} modules={contextModules} sources={contextSources} items={contextItems} lessons={contextLessons} alerts={contextAlerts} onOpenItem={onOpenItem} onCartridge={onCartridge} />
          <SuccessFactorsGoldPanel
            payload={sfGoldKpis}
            loading={sfGoldLoading}
            error={sfGoldError}
            sources={contextSources}
            decisionModel={sfDecisionModel}
            decisionModelLoading={sfDecisionModelLoading}
            decisionModelError={sfDecisionModelError}
          />

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(360px,0.7fr)]" aria-label="Ciclo, salud y mapa operativo">
            <OmegaCycleBar steps={dashboard?.omega_steps ?? defaultOmegaSteps} counts={contextCycleCounts} />
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
              <SourceHealthPanel dataReadySources={contextDataReadySources} totalSources={contextSources.length} />
              <MiniPanel title="Reglas" value={`${dashboard?.summary.thresholds?.active ?? contextThresholds.length}/${dashboard?.summary.thresholds?.total ?? contextThresholds.length}`} detail="decisiones activas" />
            </div>
          </section>

          <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]" aria-label="Estado por dominio">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">{cartridge === "all" ? "Estado por dominio" : "Estado del frente"}</p>
                <h2 className="text-lg font-semibold text-foreground dark:text-white">Mapa operativo</h2>
              </div>
              <span className="text-sm text-muted-foreground">{visibleDomains.length} dominios</span>
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
              {visibleDomains.map((item) => (
                <DomainSection key={item.id} domain={item} collapsed={collapsed.has(item.id)} onToggle={() => onToggleDomain(item.id)} />
              ))}
            </div>
          </section>

          <SourceInventoryPanel context={context} sources={contextSources} />
        </div>

        <aside className="space-y-4 2xl:sticky 2xl:top-20 2xl:self-start" aria-label="Decisiones y actividad">
          <AlertQueuePanel context={context} alerts={contextAlerts} busyAction={busyAction} actionError={alertActionError} actionMessage={alertActionMessage} onOperateAlert={onOperateAlert} onOpenItem={onOpenItem} items={dashboard?.items ?? contextItems} />
          <LiveDataFeed items={contextItems} alerts={contextAlerts} />
          <MiniPanel title="Aprendizaje" value={lessonsLoading ? "..." : contextLessons.length} detail="reglas visibles" />
        </aside>
      </section>

      <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]" aria-label="Anomalías detectadas">
        <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Riesgos y decisiones</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">{filteredCount} señales priorizadas</h2>
        </div>
        <span className="text-sm text-muted-foreground">{openCount} abiertas</span>
      </div>
      {state === "loading" ? <StatePanel icon={Loader2} text="Cargando datos operativos..." spinning /> : null}
      {state === "ready" && groupedItems.length === 0 ? <StatePanel icon={CheckCircle2} text="No hay señales para estos filtros." /> : null}
        <div className="space-y-4">
          {groupedItems.map((group) => (
            <section key={group.domain.id} className="rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f]">
              <header className="flex items-center justify-between border-b dark:border-sky-400/15 px-4 py-3">
                <strong className="text-foreground dark:text-white">{businessAreaLabel(group.domain.label)}</strong>
                <span className="text-sm text-muted-foreground">{group.items.length} señales</span>
              </header>
              <div className="grid gap-3 p-3 lg:grid-cols-2 2xl:grid-cols-3">
                {group.items.map((item) => <AnomalyCard key={item.id} item={item} onOpen={() => onOpenItem(item)} />)}
              </div>
            </section>
          ))}
        </div>
      </section>

      <ThresholdRulesBoard context={context} thresholds={contextThresholds} candidates={thresholdCandidates} loading={thresholdsLoading} error={thresholdsError} saving={thresholdSaving} saveError={thresholdSaveError} saveMessage={thresholdSaveMessage} onSave={onSaveThreshold} />
      <LessonsBoard context={context} lessons={contextLessons} loading={lessonsLoading} error={lessonsError} />
    </div>
  );
}

function filterButtonClass(active: boolean): string {
  return cn(
    "inline-flex min-h-[44px] items-center justify-center rounded-md border px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300",
    active
      ? "border-cyan-400/40 bg-cyan-500/15 text-cyan-950 shadow-sm dark:text-cyan-50 dark:shadow-[0_0_18px_rgba(34,211,238,0.12)]"
      : "bg-background text-muted-foreground hover:bg-cyan-500/10 hover:text-cyan-950 dark:border-sky-400/20 dark:bg-[#07111e] dark:hover:text-cyan-50",
  );
}

function ExecutiveCommandStrip({
  totalItems,
  critical,
  attention,
  decisionCount,
  sources,
  items,
  sfGoldKpis,
}: {
  totalItems: number;
  critical: number;
  attention: number;
  decisionCount: number;
  sources: SourceStatus[];
  items: ControlItem[];
  sfGoldKpis: SfGoldKpisPayload | null;
}) {
  const impactValues = items
    .map((item) => item.impact_estimate)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const impactTotal = impactValues.reduce((sum, value) => sum + value, 0);
  const readySources = sources.filter((source) => source.operationally_ready || source.data_readiness === "ready").length;
  const sfRows = sfGoldKpis?.widgets.reduce((sum, widget) => sum + (Number.isFinite(widget.value) ? widget.value : 0), 0) ?? 0;
  const topDataset = sfGoldKpis?.widgets.find((widget) => widget.value > 0);
  const severitySeries = (["low", "medium", "high", "critical"] as Severity[]).map((level) => items.filter((item) => item.severity === level).length);
  const sourceSeries = [
    readySources,
    sources.filter((source) => source.data_readiness === "partial").length,
    sources.filter((source) => ["blocked", "no_permission", "unavailable", "missing", "error"].includes(source.data_readiness || source.status)).length,
    sources.length,
  ];
  const goldSeries = sfGoldKpis?.widgets.map((widget) => widget.value).filter((value) => Number.isFinite(value)).slice(0, 8) ?? [];
  const decisionSeries = [
    items.filter((item) => item.status === "open").length,
    items.filter((item) => item.status === "in_review").length,
    decisionCount,
    items.filter((item) => item.execution_status && item.execution_status !== "not_started").length,
  ];
  return (
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-6" aria-label="Números ejecutivos">
      <SummaryCard icon={Gauge} label="Señales activas" value={totalItems} detail={`${items.length} registros visibles`} series={severitySeries} />
      <SummaryCard icon={AlertTriangle} label="Críticas" value={critical} detail="riesgo máximo" tone="critical" series={[0, critical, attention, totalItems]} />
      <SummaryCard icon={Activity} label="Atención" value={attention} detail="alta y media" tone="attention" series={severitySeries} />
      <SummaryCard icon={ShieldCheck} label="Decisiones" value={decisionCount} detail="supervisadas" series={decisionSeries} />
      <SummaryCard icon={TrendingUp} label="Valor en riesgo" value={impactValues.length ? fmtMoney(impactTotal, items.find((item) => item.impact_currency)?.impact_currency || "USD") : "N/D"} detail={impactValues.length ? `${impactValues.length} impactos` : "sin impacto"} series={impactValues.slice(0, 8)} />
      <SummaryCard icon={Users} label="Plantilla y cobertura" value={sfRows ? sfRows.toLocaleString("es-MX") : `${readySources}/${sources.length}`} detail={topDataset ? businessDatasetLabel(topDataset.dataset) : "cobertura del contexto"} series={goldSeries.length ? goldSeries : sourceSeries} tone={readySources === sources.length && sources.length > 0 ? "neutral" : "attention"} />
    </section>
  );
}

function ContextPanel({ context, sourceCount, moduleCount, itemCount, openCount, severity }: { context: ActiveContext; sourceCount: number; moduleCount: number; itemCount: number; openCount: number; severity: Severity | "all" }) {
  const label = context.level === "portfolio" ? "Vista ejecutiva" : context.level === "domain" ? "Vista de área" : "Vista de frente";
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]" aria-label="Contexto activo">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">{label}</p>
          <h2 className="text-xl font-semibold text-foreground dark:text-white">{context.title}</h2>
          <p className="text-sm text-muted-foreground">{context.subtitle}</p>
        </div>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric label="Frentes con señales" value={moduleCount} />
          <Metric label="Información" value={sourceCount} />
          <Metric label="Señales" value={itemCount} />
          <Metric label="Abiertas" value={openCount} />
        </dl>
      </div>
      {severity !== "all" ? <p className="mt-3 text-sm text-muted-foreground">Filtro activo: {severityLabels[severity]}</p> : null}
    </section>
  );
}

function ContextOperations({ context, modules, sources, items, lessons, alerts, onOpenItem, onCartridge }: { context: ActiveContext; modules: Cartridge[]; sources: SourceStatus[]; items: ControlItem[]; lessons: Lesson[]; alerts: ControlAlert[]; onOpenItem: (item: ControlItem) => void; onCartridge: (cartridge: string, domain: string) => void }) {
  const topItem = [...items].sort((left, right) => (right.priority_score || 0) - (left.priority_score || 0) || right.severity_weight - left.severity_weight)[0];
  const sourceRisk = sources.filter((source) => source.status !== "ok").length;
  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]" aria-label="Panel operativo contextual">
      <article className="overflow-hidden rounded-xl border bg-card shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]">
        <div className="border-b dark:border-sky-400/20 p-4">
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Centro operativo</p>
          <h2 className="mt-1 text-lg font-semibold text-foreground dark:text-white">{context.level === "portfolio" ? "Vista general de decisiones" : context.title}</h2>
          <p className="mt-2 text-sm text-muted-foreground">
          {context.level === "portfolio"
            ? "Vista consolidada de áreas activas, señales, impacto y decisiones pendientes."
            : "Vista exclusiva del contexto seleccionado: solo muestra frentes, señales, decisiones y aprendizaje relacionados."}
          </p>
        </div>
        <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Metric label="Frentes con señales" value={modules.length} />
              <Metric label="Información en riesgo" value={sourceRisk} />
              <Metric label="Alertas" value={alerts.length} />
              <Metric label="Lecciones" value={lessons.length} />
            </div>
            <ModuleBarChart modules={modules} />
          </div>
          <div className="space-y-4">
            <SeverityTrend items={items} />
            <SourceHeatmap modules={modules} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2 border-t dark:border-sky-400/20 p-4">
          {modules.slice(0, 8).map((module) => (
            <button key={module.id} type="button" onClick={() => onCartridge(module.id, module.domain)} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border bg-background px-3 text-sm text-foreground hover:bg-cyan-500/10 hover:text-cyan-950 dark:border-sky-400/20 dark:bg-[#07111e] dark:text-slate-200 dark:hover:text-cyan-50">
              <Layers3 aria-hidden className="h-4 w-4 text-cyan-600 dark:text-cyan-300" />
              {businessFrontLabel(module.label)}
              <span className="text-muted-foreground">{module.item_count}</span>
            </button>
          ))}
        </div>
      </article>
      <article className="rounded-xl border bg-card p-4 shadow-sm dark:border-amber-400/30 dark:bg-[#11131f] dark:shadow-[0_0_26px_rgba(245,158,11,0.08)]">
        <p className="text-xs font-semibold uppercase text-amber-700 dark:text-amber-300/90">Decisión recomendada</p>
        {topItem ? (
          <div className="mt-3 space-y-3" data-control-item-id={topItem.id}>
            <span className={cn("inline-flex rounded-full border px-2.5 py-1 text-xs font-medium", severityTone(topItem.severity))}>{severityLabels[topItem.severity]}</span>
            <h3 className="text-xl font-semibold text-foreground dark:text-white">{businessItemTitle(topItem)}</h3>
            <p className="text-sm text-muted-foreground dark:text-slate-300">{businessItemDescription(topItem)}</p>
            <div className="grid grid-cols-2 gap-2">
              <Metric label="Prioridad" value={topItem.priority_score ?? topItem.priority?.score ?? topItem.severity_weight} />
              <Metric label="Impacto" value={topItem.impact_estimate ? fmtMoney(topItem.impact_estimate, topItem.impact_currency) : "N/D"} />
            </div>
            <button
              type="button"
              aria-label={`Abrir zona de decisión ${businessItemTitle(topItem)}`}
              className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-3 text-sm font-semibold text-slate-950 hover:bg-cyan-200"
              onClick={() => onOpenItem(topItem)}
            >
              Abrir zona de decisión
              <ChevronRight aria-hidden className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <StatePanel icon={CheckCircle2} text="Sin señales abiertas en este contexto." />
        )}
      </article>
    </section>
  );
}

function AlertQueuePanel({ context, alerts, items, busyAction, actionError, actionMessage, onOperateAlert, onOpenItem }: { context: ActiveContext; alerts: ControlAlert[]; items: ControlItem[]; busyAction: string; actionError: string; actionMessage: string; onOperateAlert: (alert: ControlAlert, operation: AlertOperation) => void; onOpenItem: (item: ControlItem) => void }) {
  const itemById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const topAlerts = alerts.slice(0, 6);
  const critical = alerts.filter((alert) => alert.severity === "critical").length;
  const pushReady = alerts.filter((alert) => alert.push_ready).length;
  const avgPriority = alerts.length ? Math.round(alerts.reduce((sum, alert) => sum + (alert.priority_score || 0), 0) / alerts.length) : 0;
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-red-400/20 dark:bg-[#0d111d] dark:shadow-[0_0_26px_rgba(248,113,113,0.08)]" aria-label="Cola de alertas operativas">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-red-700 dark:text-red-300/90">Zona de decisiones</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">{alerts.length} alertas activas</h2>
          <p className="text-sm text-muted-foreground">{context.title} · {pushReady} listas para atención · {critical} críticas · ejecución supervisada</p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full border border-red-400/20 bg-red-400/10 px-3 py-1.5 text-sm text-red-700 dark:text-red-100">
          <Bell aria-hidden className="h-4 w-4" />
          {pushReady > 0 ? "Listas para atención" : "Cola interna"}
        </span>
      </div>
      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <Metric label="Listas para atención" value={pushReady} />
        <Metric label="Críticas" value={critical} />
        <Metric label="Prioridad media" value={alerts.length ? avgPriority : "N/D"} />
      </div>
      <DecisionPulse alerts={alerts} />
      {actionMessage ? <p className="mb-3 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300" role="status">{actionMessage}</p> : null}
      {actionError ? <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{actionError}</p> : null}
      {topAlerts.length ? (
        <div className="grid gap-3">
          {topAlerts.map((alert) => {
            const item = itemById.get(alert.item_id);
            return (
              <article className="rounded-lg border bg-background p-4 shadow-sm dark:border-sky-400/15 dark:bg-[#07111e]" key={alert.id}>
                <div className="flex items-start justify-between gap-3">
                  <span className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", severityTone(alert.severity))}>{severityLabels[alert.severity]}</span>
                  <strong className="text-xs text-muted-foreground">Prioridad {alert.priority_score}</strong>
                </div>
                <h3 className="mt-3 font-semibold text-foreground dark:text-white">{sanitizeBusinessCopy(alert.title, "Alerta operativa")}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{sanitizeBusinessCopy(alert.message, "OMEGA requiere revisión operativa.")}</p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                  <Metric label="Frente" value={businessFrontLabel(alert.module)} />
                  <Metric label="Atención" value={businessStatusLabel(alert.delivery?.status || "not_configured")} />
                </dl>
                {alert.owner || alert.snoozed_until ? <p className="mt-2 text-xs text-muted-foreground">{alert.owner ? `Responsable: ${alert.owner}` : ""}{alert.owner && alert.snoozed_until ? " · " : ""}{alert.snoozed_until ? `Pospuesta hasta ${fmtDate(alert.snoozed_until)}` : ""}</p> : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  <ActionButton disabled={!item || busyAction.startsWith("alert:")} onClick={() => item && onOpenItem(item)} icon={Bell}>Abrir</ActionButton>
                  <ActionButton disabled={busyAction !== "" || alert.status === "acknowledged"} loading={busyAction === `alert:ack:${alert.item_id}`} onClick={() => onOperateAlert(alert, "ack")} icon={CheckCircle2}>Reconocer</ActionButton>
                  <ActionButton disabled={busyAction !== "" || alert.status === "snoozed"} loading={busyAction === `alert:snooze:${alert.item_id}`} onClick={() => onOperateAlert(alert, "snooze")} icon={Clock3}>Posponer 24h</ActionButton>
                  <ActionButton disabled={busyAction !== ""} loading={busyAction === `alert:assign:${alert.item_id}`} onClick={() => onOperateAlert(alert, "assign")} icon={UserPlus}>Asignarme</ActionButton>
                  <ActionButton disabled={busyAction !== ""} loading={busyAction === `alert:false-positive:${alert.item_id}`} onClick={() => onOperateAlert(alert, "false-positive")} icon={XCircle} tone="danger">Falso positivo</ActionButton>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <StatePanel icon={CheckCircle2} text="Sin alertas activas para este contexto." />
      )}
    </section>
  );
}

function DecisionPulse({ alerts }: { alerts: ControlAlert[] }) {
  const critical = alerts.filter((alert) => alert.severity === "critical").length;
  const high = alerts.filter((alert) => alert.severity === "high").length;
  const medium = alerts.filter((alert) => alert.severity === "medium").length;
  const low = alerts.filter((alert) => alert.severity === "low").length;
  const total = Math.max(1, alerts.length);
  const segments = [
    { label: "Críticas", value: critical, className: "bg-red-500" },
    { label: "Altas", value: high, className: "bg-orange-500" },
    { label: "Atención", value: medium, className: "bg-amber-500" },
    { label: "Bajas", value: low, className: "bg-cyan-500" },
  ];
  return (
    <div className="mb-4 rounded-lg border bg-background p-3 dark:border-red-400/15 dark:bg-[#07111e]" aria-label="Pulso de decisiones">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-semibold uppercase tracking-wide text-muted-foreground">Pulso de decisión</span>
        <span className="text-muted-foreground">{alerts.length} señales</span>
      </div>
      <div className="flex h-3 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        {segments.map((segment) => segment.value > 0 ? (
          <span key={segment.label} className={segment.className} style={{ width: `${(segment.value / total) * 100}%` }} title={`${segment.label}: ${segment.value}`} />
        ) : null)}
        {!alerts.length ? <span className="w-full bg-emerald-500" /> : null}
      </div>
      <div className="mt-2 grid grid-cols-4 gap-1 text-[11px] text-muted-foreground">
        {segments.map((segment) => <span key={segment.label}>{segment.label} · {segment.value}</span>)}
      </div>
    </div>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  detail,
  tone = "neutral",
  series = [],
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  detail?: string;
  tone?: "neutral" | "critical" | "attention";
  series?: number[];
}) {
  const toneClass = tone === "critical"
    ? "from-red-500/15 to-red-500/0 text-red-700 dark:text-red-200"
    : tone === "attention"
      ? "from-amber-500/15 to-amber-500/0 text-amber-700 dark:text-amber-200"
      : "from-cyan-500/15 to-emerald-500/0 text-cyan-700 dark:text-cyan-200";
  return (
    <article className="group min-h-[150px] overflow-hidden rounded-xl border bg-card shadow-sm transition-colors dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_24px_rgba(14,165,233,0.08)]">
      <div className={cn("flex h-full flex-col bg-gradient-to-br p-4", toneClass)}>
        <div className="flex items-start justify-between gap-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
          <span className="grid h-9 w-9 place-items-center rounded-md border bg-background/70 text-current dark:border-current/20 dark:bg-white/5">
            <Icon aria-hidden className="h-4 w-4" />
          </span>
        </div>
        <strong className="mt-3 block text-3xl font-semibold tracking-tight text-foreground dark:text-white">{value}</strong>
        {detail ? <p className="mt-1 truncate text-sm text-muted-foreground">{detail}</p> : null}
        <MiniSparkline values={series} tone={tone} className="mt-auto pt-4" />
      </div>
    </article>
  );
}

function MiniSparkline({ values, tone, className }: { values: number[]; tone: "neutral" | "critical" | "attention"; className?: string }) {
  const clean = values.filter((value) => typeof value === "number" && Number.isFinite(value));
  const max = Math.max(1, ...clean);
  const min = Math.min(0, ...clean);
  const points = clean.length > 1
    ? clean.map((value, index) => {
      const x = 4 + (index / (clean.length - 1)) * 112;
      const y = 34 - ((value - min) / Math.max(1, max - min)) * 28;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ")
    : "";
  const color = tone === "critical" ? "#ef4444" : tone === "attention" ? "#f59e0b" : "#06b6d4";
  return (
    <div className={cn("min-h-[42px]", className)} aria-hidden>
      {clean.length > 1 ? (
        <svg viewBox="0 0 120 40" className="h-10 w-full overflow-visible">
          <defs>
            <linearGradient id={`spark-${tone}`} x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor={color} stopOpacity="0.25" />
              <stop offset="100%" stopColor={color} />
            </linearGradient>
          </defs>
          {[10, 22, 34].map((y) => (
            <line key={y} x1="4" x2="116" y1={y} y2={y} stroke="currentColor" strokeOpacity="0.1" />
          ))}
          <polyline points={points} fill="none" stroke={`url(#spark-${tone})`} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          {points.split(" ").map((point, index) => {
            const [x, y] = point.split(",");
            return <circle key={`${point}-${index}`} cx={x} cy={y} r={index === points.split(" ").length - 1 ? "3.5" : "2.5"} fill={color} />;
          })}
        </svg>
      ) : (
        <div className="grid h-10 grid-cols-8 items-end gap-1">
          {Array.from({ length: 8 }).map((_, index) => (
            <span
              key={index}
              className="rounded-t bg-current opacity-20"
              style={{ height: `${12 + (index % 4) * 7}px` }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function OmegaCycleBar({ steps, counts }: { steps: Array<{ id: string; label: string }>; counts?: Record<string, number> }) {
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]">
      <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Ciclo OMEGA</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((step) => (
          <div key={step.id} className="rounded-md border bg-background p-3 dark:border-sky-400/15 dark:bg-[#07111e]">
            <span className="text-xs text-muted-foreground">{step.label}</span>
            <strong className="block text-xl text-foreground dark:text-white">{counts?.[step.id] ?? 0}</strong>
          </div>
        ))}
      </div>
    </article>
  );
}

function SourceHealthPanel({ dataReadySources, totalSources }: { dataReadySources: number; totalSources: number }) {
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]">
      <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Cobertura de información</p>
      <strong className="mt-2 block text-2xl text-foreground dark:text-white">{dataReadySources}/{totalSources}</strong>
      <p className="text-sm text-muted-foreground">listas para decidir</p>
      <div className="mt-3">
        <MiniBar value={dataReadySources} max={totalSources || 1} tone={dataReadySources === totalSources && totalSources > 0 ? "good" : "warning"} />
      </div>
    </article>
  );
}

function MiniPanel({ title, value, detail }: { title: string; value: string | number; detail: string }) {
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]">
      <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">{title}</p>
      <strong className="mt-2 block text-2xl text-foreground dark:text-white">{value}</strong>
      <p className="text-sm text-muted-foreground">{detail}</p>
    </article>
  );
}

function DomainSection({ domain, collapsed, onToggle }: { domain: Domain; collapsed: boolean; onToggle: () => void }) {
  return (
    <section className="rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f]">
      <button type="button" onClick={onToggle} className="flex min-h-[44px] w-full items-center justify-between px-4 text-left">
        <span className="flex items-center gap-2">
          {collapsed ? <ChevronRight aria-hidden className="h-4 w-4" /> : <ChevronDown aria-hidden className="h-4 w-4" />}
          <strong className="text-foreground dark:text-white">{businessAreaLabel(domain.label)}</strong>
        </span>
        <span className="text-sm text-muted-foreground">{domain.item_count} señales · {domain.critical_count} críticas</span>
      </button>
      {!collapsed ? (
        <div className="grid gap-2 border-t dark:border-sky-400/15 p-3 md:grid-cols-2">
          {domain.modules.map((module) => (
            <article key={module.id} className="rounded-md border bg-card dark:border-sky-400/15 dark:bg-[#081423] p-3">
              <div className="flex items-center justify-between gap-3">
                <strong className="text-sm text-foreground dark:text-white">{businessFrontLabel(module.label)}</strong>
                <ReadinessBadge status={module.source_status} compact />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{module.item_count} señales · {module.critical_count} críticas</p>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SourceInventoryPanel({ context, sources }: { context: ActiveContext; sources: SourceStatus[] }) {
  const readinessStates: DataReadiness[] = ["ready", "partial", "stub", "empty", "missing", "invalid_schema", "unavailable", "blocked", "no_permission"];
  const readinessCounts = readinessStates.reduce((acc, state) => {
    acc[state] = sources.filter((source) => (source.data_readiness || "ready") === state).length;
    return acc;
  }, {} as Record<DataReadiness, number>);
  const sortedSources = [...sources].sort((left, right) => left.module.localeCompare(right.module) || left.dataset.localeCompare(right.dataset));
  return (
    <details className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]">
      <summary className="cursor-pointer list-none">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Diagnóstico interno</p>
            <h2 className="text-lg font-semibold text-foreground dark:text-white">Diagnóstico interno de {context.title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">Visible solo para revisar causas; la decisión principal ya está traducida arriba.</p>
          </div>
          <span className="text-sm text-muted-foreground">{sources.length} insumos</span>
        </div>
      </summary>
      <div className="mt-4">
        <div className="mb-4 flex flex-wrap gap-2" aria-label="Estados internos del contexto">
          {readinessStates.map((state) => (
            <span className={cn("inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs", sourceStateTone(state))} key={state}>
              {sourceStateLabels[state]} <strong>{readinessCounts[state]}</strong>
            </span>
          ))}
        </div>
        <div className="space-y-2">
          {sortedSources.length ? sortedSources.map((source) => (
            <article className="grid gap-2 rounded-md border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-3 text-sm md:grid-cols-[1fr_150px_130px_150px]" key={`${source.module_id}-${source.dataset}`}>
              <div>
                <strong className="text-foreground dark:text-white">{businessDatasetLabel(source.dataset)}</strong>
                <p className="text-xs text-muted-foreground">{businessFrontLabel(source.module)} · {businessSourceIssue(source)}</p>
              </div>
              <ReadinessBadge status={source.status} compact />
              <ReadinessBadge status={source.data_readiness || "ready"} compact />
              <span className="text-muted-foreground">{source.checked_at ? `Revisada ${timeAgo(parseDate(source.checked_at), 0)}` : "Sin revisión"}</span>
              <details className="md:col-span-4 text-xs text-muted-foreground">
                <summary className="cursor-pointer">Detalle técnico</summary>
                <div className="mt-2 rounded-md border bg-card p-2 dark:border-sky-400/15 dark:bg-[#081423]">
                  <p>Identificador interno: {source.dataset}</p>
                  <p>Cartucho: {source.cartridge}</p>
                  <p>Registros considerados: {source.count.toLocaleString("es-MX")}</p>
                  {source.readiness_reason ? <p className="text-amber-700 dark:text-amber-300">{source.readiness_reason}</p> : null}
                  {source.readiness_blockers?.length ? <p>{source.readiness_blockers.join(" · ")}</p> : null}
                  {source.error ? <p className="text-destructive">{source.error}</p> : null}
                </div>
              </details>
            </article>
          )) : <StatePanel icon={AlertTriangle} text="No hay información visible para este contexto." />}
        </div>
      </div>
    </details>
  );
}

function ThresholdRulesBoard({ context, thresholds, candidates, loading, error, saving, saveError, saveMessage, onSave }: { context: ActiveContext; thresholds: DetectionThreshold[]; candidates: ThresholdCandidate[]; loading: boolean; error: string; saving: boolean; saveError: string; saveMessage: string; onSave: (draft: ThresholdDraft) => void }) {
  const [selectedKey, setSelectedKey] = useState("");
  const [draftEdits, setDraftEdits] = useState<Record<string, ThresholdDraft>>({});
  const overridesByKey = useMemo(() => new Map(thresholds.map((threshold) => [thresholdKey(threshold), threshold])), [thresholds]);
  const effectiveSelectedKey = selectedKey && candidates.some((candidate) => candidate.key === selectedKey) ? selectedKey : candidates[0]?.key || "";
  const selected = candidates.find((candidate) => candidate.key === effectiveSelectedKey);
  const selectedOverride = selected ? overridesByKey.get(selected.key) : undefined;
  const selectedSource = selected ? selectedOverride || selected : undefined;
  const baseDraft: ThresholdDraft = selected && selectedSource ? {
    cartridge_id: selected.cartridge_id,
    anomaly_type: selected.anomaly_type,
    metric: selected.metric,
    warning_value: selectedSource.warning_value === null || selectedSource.warning_value === undefined ? "" : String(selectedSource.warning_value),
    critical_value: selectedSource.critical_value === null || selectedSource.critical_value === undefined ? "" : String(selectedSource.critical_value),
    currency: selectedSource.currency || "USD",
    enabled: selectedSource.enabled !== false,
  } : {
    cartridge_id: "",
    anomaly_type: "",
    metric: "",
    warning_value: "",
    critical_value: "",
    currency: "USD",
    enabled: true,
  };
  const draft = effectiveSelectedKey ? draftEdits[effectiveSelectedKey] || baseDraft : baseDraft;

  function updateDraft(patch: Partial<ThresholdDraft>) {
    if (!selected || !effectiveSelectedKey) return;
    setDraftEdits((current) => ({
      ...current,
      [effectiveSelectedKey]: { ...draft, ...patch },
    }));
  }

  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]" aria-label="Reglas de decisión del contexto">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Reglas de decisión</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">{context.title}</h2>
        </div>
        <span className="text-sm text-muted-foreground">{loading ? "cargando" : `${thresholds.filter((item) => item.enabled !== false).length} activos`}</span>
      </div>
      {error ? <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <article className="space-y-3 rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Regla base</span>
              <select aria-label="Regla de decisión" value={effectiveSelectedKey} onChange={(event) => setSelectedKey(event.target.value)} disabled={!candidates.length || saving} className="min-h-[44px] w-full rounded-md border bg-background px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {candidates.map((candidate) => (
                  <option value={candidate.key} key={candidate.key}>{businessFrontLabel(candidate.module)} · {thresholdLabel(candidate)}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Moneda</span>
              <input aria-label="Moneda de la regla" value={draft.currency} maxLength={8} onChange={(event) => updateDraft({ currency: event.target.value.toUpperCase() })} disabled={!selected || saving} className="min-h-[44px] w-full rounded-md border bg-background px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Advertencia</span>
              <input aria-label="Valor de advertencia" type="number" step="0.01" value={draft.warning_value} onChange={(event) => updateDraft({ warning_value: event.target.value })} disabled={!selected || saving} className="min-h-[44px] w-full rounded-md border bg-background px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Crítico</span>
              <input aria-label="Valor crítico" type="number" step="0.01" value={draft.critical_value} onChange={(event) => updateDraft({ critical_value: event.target.value })} disabled={!selected || saving} className="min-h-[44px] w-full rounded-md border bg-background px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            </label>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.enabled} onChange={(event) => updateDraft({ enabled: event.target.checked })} disabled={!selected || saving} className="h-4 w-4" />
            Regla activa
          </label>
          <button type="button" onClick={() => onSave(draft)} disabled={!selected || saving} className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            {saving ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <SlidersHorizontal aria-hidden className="h-4 w-4" />}
            Guardar regla
          </button>
          {saveMessage ? <p className="text-sm text-emerald-700 dark:text-emerald-300" role="status">{saveMessage}</p> : null}
          {saveError ? <p className="text-sm text-destructive" role="alert">{saveError}</p> : null}
        </article>
        <aside className="rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-4" aria-label="Reglas de decisión visibles">
          <p className="text-sm font-semibold text-foreground dark:text-white">Reglas de decisión visibles</p>
          <div className="mt-3 space-y-2">
            {candidates.slice(0, 8).map((candidate) => (
              <div key={candidate.key} className="rounded-md border bg-card dark:border-sky-400/15 dark:bg-[#081423] p-3 text-sm">
                <strong className="text-foreground dark:text-white">{businessTextLabel(candidate.anomaly_type)}</strong>
                <p className="text-xs text-muted-foreground">{businessFrontLabel(candidate.module)} · {businessTextLabel(candidate.metric)}</p>
              </div>
            ))}
            {!candidates.length ? <p className="text-sm text-muted-foreground">Sin reglas visibles.</p> : null}
          </div>
        </aside>
      </div>
    </section>
  );
}

function LessonsBoard({ context, lessons, loading, error }: { context: ActiveContext; lessons: Lesson[]; loading: boolean; error: string }) {
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(14,165,233,0.08)]" aria-label="Lecciones aprendidas del contexto">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Lecciones aprendidas del contexto</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">{context.title}</h2>
        </div>
        <span className="text-sm text-muted-foreground">{loading ? "cargando" : `${lessons.length} reglas visibles`}</span>
      </div>
      {error ? <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}
      <div className="grid gap-2 md:grid-cols-2">
        {lessons.slice(0, 8).map((lesson) => (
          <article key={`${lesson.id || lesson.item_id}-${lesson.rule}`} className="rounded-md border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-3 text-sm">
            <strong className="text-foreground dark:text-white">{sanitizeBusinessCopy(lesson.rule, "Lección operativa")}</strong>
            <p className="mt-1 text-xs text-muted-foreground">{businessTextLabel(lesson.cartridge_id)} · {businessTextLabel(lesson.anomaly_type)}</p>
          </article>
        ))}
        {!lessons.length && !loading ? <p className="text-sm text-muted-foreground">Sin lecciones visibles.</p> : null}
      </div>
    </section>
  );
}

function AnomalyCard({ item, onOpen }: { item: ControlItem; onOpen: () => void }) {
  return (
    <article
      className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/15 dark:bg-[#081423] dark:shadow-[0_0_20px_rgba(14,165,233,0.05)]"
      data-control-item-id={item.id}
    >
      <div className="flex items-start justify-between gap-3">
        <span className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", severityTone(item.severity))}>{severityLabels[item.severity]}</span>
        <span className="text-xs text-muted-foreground">Prioridad {item.priority_score ?? item.priority?.score ?? item.severity_weight}</span>
      </div>
      <h3 className="mt-3 font-semibold text-foreground dark:text-white">{businessItemTitle(item)}</h3>
      <p className="mt-1 text-sm text-muted-foreground">{businessItemLabel(item)}</p>
      <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">{businessItemDescription(item)}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
        <span className="rounded-full border border-sky-400/20 px-2 py-1">{businessFrontLabel(item.module)}</span>
        <span className="rounded-full border border-sky-400/20 px-2 py-1">{businessStatusLabel(item.status)}</span>
        {item.impact_estimate ? <span className="rounded-full border border-emerald-400/30 px-2 py-1 text-emerald-700 dark:text-emerald-200">{fmtMoney(item.impact_estimate, item.impact_currency)}</span> : null}
      </div>
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Revisar decisión ${businessItemTitle(item)}`}
        className="mt-4 inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-3 text-sm font-semibold text-slate-950 hover:bg-cyan-200"
      >
        <Play aria-hidden className="h-4 w-4" />
        Revisar decisión
      </button>
    </article>
  );
}

function DetailPage({
  item,
  omegaSteps,
  mode,
  manualTab,
  busyAction,
  actionMessage,
  actionError,
  activity,
  activityLoading,
  activityError,
  impact,
  impactLoading,
  impactError,
  onBack,
  onMode,
  onManualTab,
  onCreateDecision,
  onSelectOption,
  onRecordStep,
  onUpdateControl,
  onPreview,
  onDryRun,
  onExecute,
  onRunAuto,
  onApprove,
  onDismiss,
  onCreateLesson,
  onRecordIntelligenceOutcome,
  onApplyLesson,
}: {
  item: ControlItem;
  omegaSteps: Array<{ id: string; label: string }>;
  mode: DetailMode;
  manualTab: number;
  busyAction: string;
  actionMessage: string;
  actionError: string;
  activity?: ActivityPayload;
  activityLoading: boolean;
  activityError: string;
  impact?: ImpactPayload;
  impactLoading: boolean;
  impactError: string;
  onBack: () => void;
  onMode: (mode: DetailMode) => void;
  onManualTab: (tab: number) => void;
  onCreateDecision: (item: ControlItem) => void;
  onSelectOption: (item: ControlItem, optionId: string) => void;
  onRecordStep: (item: ControlItem, stepId: string, note?: string, controlId?: string, silent?: boolean) => void;
  onUpdateControl: (item: ControlItem, control: ControlChecklistItem, status: "in_progress" | "closed" | "blocked") => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onRunAuto: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
  onCreateLesson: (item: ControlItem, rule: string) => void;
  onRecordIntelligenceOutcome: (item: ControlItem, draft: IntelligenceOutcomeDraft) => void;
  onApplyLesson: (item: ControlItem, lesson: Lesson) => void;
}) {
  return (
    <section className="space-y-4">
      <div className="rounded-lg border bg-card p-4">
        <button type="button" onClick={onBack} className="inline-flex min-h-[44px] items-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/10">
          Volver
        </button>
        <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <span className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", severityTone(item.severity))}>{severityLabels[item.severity]}</span>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight">{businessItemTitle(item)}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{businessFrontLabel(item.module)} · {businessItemLabel(item)}</p>
            <p className="mt-3 max-w-3xl text-sm text-muted-foreground">{businessItemDescription(item)}</p>
            <details className="mt-3 text-xs text-muted-foreground">
              <summary className="cursor-pointer">Diagnóstico técnico</summary>
              <p className="mt-1">Identificador interno: {item.source_dataset}</p>
            </details>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => onMode("auto")} className={filterButtonClass(mode === "auto")}>Modo automático</button>
            <button type="button" onClick={() => onMode("manual")} className={filterButtonClass(mode === "manual")}>Modo manual</button>
          </div>
        </div>
      </div>

      <ImpactSnapshot item={item} impact={impact} loading={impactLoading} error={impactError} />

      {mode === "auto" ? (
        <section className="rounded-lg border bg-card p-4" aria-label="Modo automático">
          <h3 className="text-lg font-semibold">Modo automático seguro</h3>
          <p className="mt-1 text-sm text-muted-foreground">Completa investigación, opción, decisión y validación previa; registra seguimiento supervisado.</p>
          <button type="button" onClick={() => onRunAuto(item)} disabled={busyAction !== "" || terminalStatuses.has(item.status)} className="mt-4 inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            {busyAction === `auto:${item.id}` ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Play aria-hidden className="h-4 w-4" />}
            Ejecutar modo automático
          </button>
        </section>
      ) : null}

      {(mode === "manual" || mode === null) ? (
        <section className="space-y-4 rounded-lg border bg-card p-4">
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Pasos manuales OMEGA">
            {manualTabs.map((tab, index) => (
              <button
                type="button"
                role="tab"
                aria-selected={manualTab === index}
                key={tab}
                onClick={() => onManualTab(index)}
                className={filterButtonClass(manualTab === index)}
              >
                {tab}
              </button>
            ))}
          </div>
          <ManualStep
            item={item}
            manualTab={manualTab}
            omegaSteps={omegaSteps}
            busyAction={busyAction}
            onCreateDecision={onCreateDecision}
            onSelectOption={onSelectOption}
            onRecordStep={onRecordStep}
            onUpdateControl={onUpdateControl}
            onPreview={onPreview}
            onDryRun={onDryRun}
            onExecute={onExecute}
            onCreateLesson={onCreateLesson}
            onRecordIntelligenceOutcome={onRecordIntelligenceOutcome}
            onApplyLesson={onApplyLesson}
          />
          <div className="flex flex-wrap gap-2 border-t pt-4">
            <button
              type="button"
              onClick={() => onApprove(item)}
              disabled={busyAction !== "" || item.status === "approved"}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {busyAction === `approve:${item.id}` ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <CheckCircle2 aria-hidden className="h-4 w-4" />}
              {item.status === "approved" ? "Recomendación aprobada" : "Aprobar recomendación"}
            </button>
            <button type="button" onClick={() => onDismiss(item)} disabled={busyAction !== "" || terminalStatuses.has(item.status)} className="inline-flex min-h-[44px] items-center gap-2 rounded-md border border-destructive/40 px-3 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50">
              {busyAction === `dismiss:${item.id}` ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <XCircle aria-hidden className="h-4 w-4" />}
              Descartar
            </button>
          </div>
        </section>
      ) : null}

      {actionMessage ? <p className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300" role="status">{actionMessage}</p> : null}
      {actionError ? <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{actionError}</p> : null}
      <ActivityTrail activity={activity} loading={activityLoading} error={activityError} currentMessage={actionMessage} />
    </section>
  );
}

function ImpactSnapshot({
  item,
  impact,
  loading,
  error,
}: {
  item: ControlItem;
  impact?: ImpactPayload;
  loading: boolean;
  error: string;
}) {
  const drivers = impact?.drivers || item.impact_drivers || [];
  const estimate = impact?.estimate ?? item.impact_estimate ?? null;
  const currency = impact?.currency || item.impact_currency || "USD";
  const confidence = typeof impact?.confidence === "number" ? `${Math.round(impact.confidence * 100)}%` : "N/D";
  return (
    <section className="rounded-lg border bg-card p-4" aria-label="Impacto operativo">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-muted-foreground">Impacto</p>
          <h3 className="text-lg font-semibold">{estimate ? fmtMoney(estimate, currency) : "Impacto sin estimación"}</h3>
          <p className="mt-1 text-sm text-muted-foreground">{impact?.explanation || item.impact || item.recommendation}</p>
        </div>
        <div className="grid min-w-[260px] grid-cols-2 gap-2">
          <InfoBlock label="Confianza" value={confidence} />
          <InfoBlock label="Prioridad" value={impact?.priority_score ?? item.priority_score ?? item.priority?.score ?? "N/D"} />
        </div>
      </div>
      {loading ? <div className="mt-3"><StatePanel icon={Loader2} text="Cargando impacto operativo..." spinning /></div> : null}
      {error ? <p className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}
      {impact?.formula ? <p className="mt-3 rounded-md border bg-background p-3 text-xs text-muted-foreground">Fórmula: {impact.formula}</p> : null}
      {drivers.length ? (
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          {drivers.slice(0, 6).map((driver, index) => (
            <InfoBlock
              key={`${driver.label}:${index}`}
              label={driver.label}
              value={[
                driver.value === null || driver.value === undefined ? "" : String(driver.value),
                driver.unit,
                driver.currency,
                typeof driver.points === "number" ? `${driver.points} pts` : "",
              ].filter(Boolean).join(" ") || "N/D"}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ManualStep({
  item,
  manualTab,
  omegaSteps,
  busyAction,
  onCreateDecision,
  onSelectOption,
  onRecordStep,
  onUpdateControl,
  onPreview,
  onDryRun,
  onExecute,
  onCreateLesson,
  onRecordIntelligenceOutcome,
  onApplyLesson,
}: {
  item: ControlItem;
  manualTab: number;
  omegaSteps: Array<{ id: string; label: string }>;
  busyAction: string;
  onCreateDecision: (item: ControlItem) => void;
  onSelectOption: (item: ControlItem, optionId: string) => void;
  onRecordStep: (item: ControlItem, stepId: string, note?: string, controlId?: string, silent?: boolean) => void;
  onUpdateControl: (item: ControlItem, control: ControlChecklistItem, status: "in_progress" | "closed" | "blocked") => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onCreateLesson: (item: ControlItem, rule: string) => void;
  onRecordIntelligenceOutcome: (item: ControlItem, draft: IntelligenceOutcomeDraft) => void;
  onApplyLesson: (item: ControlItem, lesson: Lesson) => void;
}) {
  const stepId = manualStepIds[manualTab];
  const stepLabel = omegaSteps.find((step) => step.id === stepId)?.label || manualTabs[manualTab];
  return (
    <section role="tabpanel" aria-label={stepLabel} className="rounded-lg border bg-background p-4">
      <p className="text-xs font-semibold uppercase text-muted-foreground">Paso {manualTab + 1} de 6</p>
      <h3 className="mt-1 text-lg font-semibold">{stepLabel}</h3>
      {manualTab === 0 ? <InvestigationStep item={item} busyAction={busyAction} onRecordStep={onRecordStep} onRecordIntelligenceOutcome={onRecordIntelligenceOutcome} /> : null}
      {manualTab === 1 ? <OptionsStep item={item} busyAction={busyAction} onSelectOption={onSelectOption} /> : null}
      {manualTab === 2 ? <DecisionStep item={item} busyAction={busyAction} onCreateDecision={onCreateDecision} /> : null}
      {manualTab === 3 ? <ExecutionStep item={item} busyAction={busyAction} onPreview={onPreview} onDryRun={onDryRun} onExecute={onExecute} /> : null}
      {manualTab === 4 ? <ControlStep item={item} busyAction={busyAction} onUpdateControl={onUpdateControl} /> : null}
      {manualTab === 5 ? <LessonsStep item={item} busyAction={busyAction} onCreateLesson={onCreateLesson} onApplyLesson={onApplyLesson} /> : null}
    </section>
  );
}

function InvestigationStep({
  item,
  busyAction,
  onRecordStep,
  onRecordIntelligenceOutcome,
}: {
  item: ControlItem;
  busyAction: string;
  onRecordStep: (item: ControlItem, stepId: string, note?: string) => void;
  onRecordIntelligenceOutcome: (item: ControlItem, draft: IntelligenceOutcomeDraft) => void;
}) {
  const intelligence = item.intelligence || item.omega.intelligence;
  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        <InfoBlock label="Causa probable" value={item.root_cause || item.omega.investigation.root_cause || "Pendiente"} />
        <InfoBlock label="Impacto" value={sanitizeBusinessCopy(item.impact || item.omega.investigation.impact || item.recommendation, "Pendiente")} />
      </div>
      {intelligence ? <IntelligencePanel item={item} intelligence={intelligence} busyAction={busyAction} onRecordOutcome={onRecordIntelligenceOutcome} /> : null}
      <button type="button" onClick={() => onRecordStep(item, "investigation", "Investigación revisada")} disabled={busyAction !== ""} className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
        {busyAction.startsWith(`step:${item.id}:investigation`) ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <CheckCircle2 aria-hidden className="h-4 w-4" />}
        Registrar investigación revisada
      </button>
    </div>
  );
}

function IntelligencePanel({
  item,
  intelligence,
  busyAction,
  onRecordOutcome,
}: {
  item: ControlItem;
  intelligence: IntelligencePack;
  busyAction: string;
  onRecordOutcome: (item: ControlItem, draft: IntelligenceOutcomeDraft) => void;
}) {
  const baseline = intelligence.baseline || {};
  const signal = intelligence.signal || {};
  const evidence = intelligence.evidence_pack || {};
  const hypothesis = intelligence.hypotheses?.[0];
  const option = intelligence.options?.[0];
  const [actionTaken, setActionTaken] = useState(option?.label || "");
  const [actualValue, setActualValue] = useState("");
  const [outcomeSummary, setOutcomeSummary] = useState("");
  const externalEvidenceCount = (evidence.items || []).filter((evidenceItem) => evidenceItem.source_type === "external").length;
  const topOptions = (intelligence.options || []).slice(0, 3);
  const predictionHorizon = signal.prediction_horizon_days || baseline.prediction_horizon_days;
  const predictedValue = signal.predicted_value ?? baseline.predicted_value;
  const canSubmitOutcome = actionTaken.trim().length > 0 && busyAction === "";
  function submitOutcome() {
    if (!canSubmitOutcome) return;
    onRecordOutcome(item, {
      option_id: option?.option_id,
      action_taken: actionTaken.trim(),
      actual_value: actualValue.trim() ? Number(actualValue) : undefined,
      predicted_value: typeof predictedValue === "number" ? predictedValue : undefined,
      outcome_summary: outcomeSummary.trim() || undefined,
    });
  }
  return (
    <section className="rounded-lg border bg-card p-4" aria-label="Inteligencia operativa">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold uppercase text-muted-foreground">Inteligencia operativa</p>
        <h4 className="text-base font-semibold">{signal.summary || signal.metric_name || "Señal con referencia"}</h4>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-4">
        <InfoBlock label="Real" value={formatIntelligenceNumber(baseline.actual_value)} />
        <InfoBlock label="Esperado" value={formatIntelligenceNumber(baseline.expected_value)} />
        <InfoBlock label="Desviación" value={formatIntelligencePercent(signal.deviation_pct)} />
        <InfoBlock label="Confianza" value={formatIntelligencePercent(signal.confidence)} />
      </div>
      {predictionHorizon || typeof predictedValue === "number" ? (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <InfoBlock label="Tipo" value={signal.signal_subtype === "observed" ? "Observada" : "Predictiva"} />
          <InfoBlock label="Horizonte" value={predictionHorizon ? `${predictionHorizon} días` : "N/D"} />
          <InfoBlock label="Predicción" value={formatIntelligenceNumber(predictedValue ?? undefined)} />
        </div>
      ) : null}
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <InfoBlock label="Evidencia" value={evidence.summary || `${evidence.items?.length || 0} elementos`} />
        <InfoBlock label="Hipótesis" value={hypothesis?.title || "Pendiente"} />
        <InfoBlock label="Mejor opción" value={option ? `${option.label || "Opción"} · prioridad ${option.score ?? "N/D"}` : "Pendiente"} />
      </div>
      <div className="mt-3 rounded-md border bg-background p-3">
        <p className="text-xs font-semibold uppercase text-muted-foreground">Evidencia considerada</p>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {(evidence.items || []).slice(0, 4).map((evidenceItem, index) => (
            <div key={`${evidenceItem.source_ref || "referencia"}:${index}`} className="rounded-md border p-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{sanitizeBusinessCopy(evidenceItem.source_ref, "Referencia")}</span>
              <span> · {evidenceItem.supports_hypothesis || "referencia"}</span>
              <span> · fuerza {formatIntelligencePercent(evidenceItem.strength)}</span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{externalEvidenceCount ? `${externalEvidenceCount} referencia(s) externas consideradas.` : "Sin contexto externo configurado para esta señal."}</p>
      </div>
      {topOptions.length ? (
        <div className="mt-3 grid gap-2 lg:grid-cols-3">
          {topOptions.map((candidate) => (
            <div key={candidate.option_id || candidate.label} className="rounded-md border bg-background p-3">
              <p className="text-sm font-semibold">{candidate.label || "Opción"}</p>
              <p className="mt-1 text-xs text-muted-foreground">Impacto {formatIntelligenceNumber(candidate.impact_expected)} · prioridad {candidate.score ?? "N/D"}</p>
            </div>
          ))}
        </div>
      ) : null}
      {hypothesis?.rationale ? <p className="mt-3 text-sm text-muted-foreground">{hypothesis.rationale}</p> : null}
      {option?.score_explanation ? <p className="mt-2 text-xs text-muted-foreground">{option.score_explanation}</p> : null}
      <div className="mt-4 rounded-md border bg-background p-3">
        <p className="text-sm font-semibold">Registrar resultado</p>
        <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1.2fr)_160px_minmax(0,1fr)_auto]">
          <input value={actionTaken} onChange={(event) => setActionTaken(event.target.value)} className="min-h-[44px] rounded-md border bg-card px-3 text-sm" placeholder="Acción tomada" />
          <input value={actualValue} onChange={(event) => setActualValue(event.target.value)} className="min-h-[44px] rounded-md border bg-card px-3 text-sm" inputMode="decimal" placeholder="Valor real" />
          <input value={outcomeSummary} onChange={(event) => setOutcomeSummary(event.target.value)} className="min-h-[44px] rounded-md border bg-card px-3 text-sm" placeholder="Resumen del resultado" />
          <button type="button" onClick={submitOutcome} disabled={!canSubmitOutcome} className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            {busyAction === `intelOutcome:${item.id}` ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <CheckCircle2 aria-hidden className="h-4 w-4" />}
            Guardar
          </button>
        </div>
        {intelligence.outcome ? (
          <p className="mt-2 text-xs text-muted-foreground">Último resultado: {intelligence.outcome.outcome_summary || "registrado"} · desviación {formatIntelligenceNumber(intelligence.outcome.prediction_error)}</p>
        ) : null}
      </div>
    </section>
  );
}

function OptionsStep({ item, busyAction, onSelectOption }: { item: ControlItem; busyAction: string; onSelectOption: (item: ControlItem, optionId: string) => void }) {
  return (
    <div className="mt-4 grid gap-3 md:grid-cols-2">
      {(item.omega.options || []).map((option) => {
        const selected = item.selected_option_id === option.id || option.selected;
        return (
          <button key={option.id} type="button" aria-pressed={selected} onClick={() => onSelectOption(item, option.id)} disabled={busyAction !== "" || terminalStatuses.has(item.status)} className={cn("min-h-[110px] rounded-lg border bg-card p-4 text-left hover:bg-accent/10 disabled:opacity-50", selected ? "ring-2 ring-primary" : "")}>
            <span className="text-sm font-semibold">{option.label}</span>
            <p className="mt-2 text-xs text-muted-foreground">{option.recommendation}</p>
            <span className="mt-3 inline-flex rounded-full border px-2 py-0.5 text-xs">Prioridad {option.score}</span>
          </button>
        );
      })}
    </div>
  );
}

function DecisionStep({ item, busyAction, onCreateDecision }: { item: ControlItem; busyAction: string; onCreateDecision: (item: ControlItem) => void }) {
  const decisionId = item.decision_id || item.omega.decision.decision_id;
  return (
    <div className="mt-4 space-y-3">
      <p className="text-sm text-muted-foreground">{decisionId ? "Decisión operativa creada y auditada." : "Crea una decisión operativa ligada a esta señal antes de aprobar."}</p>
      <button type="button" onClick={() => !decisionId && onCreateDecision(item)} disabled={busyAction !== "" || Boolean(decisionId)} className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-70">
        {busyAction === `decision:${item.id}` ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <ShieldCheck aria-hidden className="h-4 w-4" />}
        {decisionId ? `Decisión #${decisionId}` : "Crear decisión"}
      </button>
    </div>
  );
}

function ExecutionStep({ item, busyAction, onPreview, onDryRun, onExecute }: { item: ControlItem; busyAction: string; onPreview: (item: ControlItem) => void; onDryRun: (item: ControlItem) => void; onExecute: (item: ControlItem) => void }) {
  const template = executionTemplate(item);
  return (
    <div className="mt-4 space-y-4">
      <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
        OMEGA registra ejecución supervisada; {template?.writeback?.supported ? "seguimiento auditado disponible." : "sin ejecución disponible para esta señal."}
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <InfoBlock label="Acción" value={template?.label || "Sin acción configurada"} />
        <InfoBlock label="Riesgo" value={template?.risk_level || "N/D"} />
        <InfoBlock label="Estado de ejecución" value={businessStatusLabel(item.execution_status || item.omega.execution.status || "not_started")} />
      </div>
      <div className="flex flex-wrap gap-2">
        <ActionButton loading={busyAction === `preview:${item.id}`} disabled={busyAction !== ""} onClick={() => onPreview(item)} icon={Play}>Revisar antes de ejecutar</ActionButton>
        <ActionButton loading={busyAction === `dryrun:${item.id}`} disabled={busyAction !== ""} onClick={() => onDryRun(item)} icon={CheckCircle2}>Validar antes de ejecutar</ActionButton>
        <ActionButton loading={busyAction === `execute:${item.id}`} disabled={busyAction !== "" || !template?.writeback?.supported} onClick={() => onExecute(item)} icon={Activity}>Registrar seguimiento</ActionButton>
      </div>
    </div>
  );
}

function ControlStep({ item, busyAction, onUpdateControl }: { item: ControlItem; busyAction: string; onUpdateControl: (item: ControlItem, control: ControlChecklistItem, status: "in_progress" | "closed" | "blocked") => void }) {
  const controls = item.omega.control.items || [];
  return (
    <div className="mt-4 space-y-3">
      {controls.map((control) => (
        <article key={control.id} className="rounded-lg border bg-card p-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <strong>{control.desc}</strong>
              <p className="text-sm text-muted-foreground">Responsable {control.owner} · estado {businessStatusLabel(control.st || control.status || "open")} · {control.due_at ? fmtDate(control.due_at) : `${control.days || 0} días`}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <ActionButton loading={busyAction === `control:${item.id}:${control.id}:in_progress`} disabled={busyAction !== ""} onClick={() => onUpdateControl(item, control, "in_progress")} icon={Clock3}>Seguimiento</ActionButton>
              <ActionButton loading={busyAction === `control:${item.id}:${control.id}:closed`} disabled={busyAction !== ""} onClick={() => onUpdateControl(item, control, "closed")} icon={CheckCircle2}>Confirmar control</ActionButton>
            </div>
          </div>
        </article>
      ))}
      {!controls.length ? <StatePanel icon={CheckCircle2} text="Sin controles pendientes para esta señal." /> : null}
    </div>
  );
}

function LessonsStep({ item, busyAction, onCreateLesson, onApplyLesson }: { item: ControlItem; busyAction: string; onCreateLesson: (item: ControlItem, rule: string) => void; onApplyLesson: (item: ControlItem, lesson: Lesson) => void }) {
  const [lessonDraft, setLessonDraft] = useState("");
  const lessons = dedupeLessons([...(item.related_lessons || []), ...(item.omega.lessons.rules || []).map((rule) => ({
    item_id: item.id,
    cartridge_id: item.cartridge,
    anomaly_type: item.anomaly_type,
    rule,
  }))]).sort((left, right) => Number(!left.id) - Number(!right.id));
  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-[1fr_auto]">
        <label className="space-y-1 text-sm">
          <span className="font-medium">Nueva lección persistida</span>
          <input value={lessonDraft} onChange={(event) => setLessonDraft(event.target.value)} aria-label="Nueva lección persistida" className="min-h-[44px] w-full rounded-md border bg-background px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        </label>
        <button type="button" onClick={() => {
          if (lessonDraft.trim().length >= 8) {
            onCreateLesson(item, lessonDraft.trim());
            setLessonDraft("");
          }
        }} disabled={busyAction !== "" || lessonDraft.trim().length < 8} className="self-end inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          {busyAction === `lesson:${item.id}` ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <CheckCircle2 aria-hidden className="h-4 w-4" />}
          Guardar lección
        </button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {lessons.map((lesson) => (
          <article key={`${lesson.id || lesson.rule}`} className="rounded-lg border bg-card p-4">
            <strong className="text-sm">{sanitizeBusinessCopy(lesson.rule, "Lección operativa")}</strong>
            <div className="mt-3">
              {lesson.id ? (
                <ActionButton loading={busyAction === `applyLesson:${item.id}:${lesson.id}`} disabled={busyAction !== ""} onClick={() => onApplyLesson(item, lesson)} icon={CheckCircle2}>Aplicar lección</ActionButton>
              ) : (
                <span className="inline-flex min-h-[44px] items-center rounded-md border px-3 text-sm text-muted-foreground">
                  Lección pendiente de persistencia
                </span>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function ActivityTrail({ activity, loading, error, currentMessage }: { activity?: ActivityPayload; loading: boolean; error: string; currentMessage: string }) {
  const entries = activity?.activity || [];
  const normalizedCurrentMessage = currentMessage.trim().toLocaleLowerCase();
  const visibleText = (value: string) => (
    normalizedCurrentMessage && value.trim().toLocaleLowerCase() === normalizedCurrentMessage
      ? "Evento registrado"
      : value
  );
  return (
    <section className="rounded-lg border bg-card p-4" aria-label="Bitácora operativa">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold">Bitácora operativa</h3>
        <span className="text-sm text-muted-foreground">{loading ? "cargando" : `${activity?.counts.total ?? entries.length} eventos`}</span>
      </div>
      {error ? <p className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}
      <div className="mt-3 space-y-2">
        {entries.slice(0, 12).map((entry) => (
          <article key={entry.id} className="rounded-md border bg-background p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong>{visibleText(entry.label || entry.type)}</strong>
              <span className="text-xs text-muted-foreground">{entry.at ? fmtDate(entry.at) : entry.status || entry.kind}</span>
            </div>
            <p className="mt-1 text-muted-foreground">{visibleText(activityDescription(entry))}</p>
          </article>
        ))}
        {!entries.length && !loading ? <p className="text-sm text-muted-foreground">Sin actividad registrada todavía.</p> : null}
      </div>
    </section>
  );
}

function ModuleBarChart({ modules }: { modules: Cartridge[] }) {
  const visible = [...modules]
    .sort((left, right) => right.item_count - left.item_count)
    .slice(0, 7);
  const max = Math.max(1, ...visible.map((module) => module.item_count));
  return (
    <section className="rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-4" aria-label="Gráfica por frente">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground dark:text-white">Señales por frente</p>
        <BarChart3 aria-hidden className="h-4 w-4 text-cyan-600 dark:text-cyan-300" />
      </div>
      <div className="space-y-3">
        {visible.map((module) => {
          const pct = Math.max(3, (module.item_count / max) * 100);
          return (
            <div className="grid grid-cols-[130px_1fr_42px] items-center gap-3 text-xs" key={module.id}>
              <span className="truncate text-muted-foreground dark:text-slate-300">{businessFrontLabel(module.label)}</span>
              <div className="h-3 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                <span className="block h-full rounded-full bg-cyan-300 shadow-[0_0_12px_rgba(34,211,238,0.5)]" style={{ width: `${pct}%` }} />
              </div>
              <strong className="text-right text-foreground dark:text-white">{module.item_count}</strong>
            </div>
          );
        })}
        {!visible.length ? <p className="text-sm text-muted-foreground">Sin frentes visibles para graficar.</p> : null}
      </div>
    </section>
  );
}

function SeverityTrend({ items }: { items: ControlItem[] }) {
  const buckets = ["low", "medium", "high", "critical"] as Severity[];
  const values = buckets.map((severity) => items.filter((item) => item.severity === severity).length);
  const max = Math.max(1, ...values);
  const points = values.map((value, index) => {
    const x = 12 + index * 58;
    const y = 82 - (value / max) * 60;
    return `${x},${y}`;
  }).join(" ");
  return (
    <section className="rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-4" aria-label="Tendencia por severidad">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground dark:text-white">Tendencia de riesgo</p>
        <TrendingUp aria-hidden className="h-4 w-4 text-emerald-300" />
      </div>
      <svg viewBox="0 0 200 96" className="h-28 w-full" role="img" aria-label="Línea de severidad">
        <defs>
          <linearGradient id="risk-line" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="100%" stopColor="#fb7185" />
          </linearGradient>
        </defs>
        {[20, 40, 60, 80].map((line) => (
          <line key={line} x1="8" x2="192" y1={line} y2={line} stroke="rgba(148,163,184,0.18)" strokeWidth="1" />
        ))}
        <polyline points={points} fill="none" stroke="url(#risk-line)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        {points.split(" ").map((point, index) => {
          const [x, y] = point.split(",");
          return <circle key={point} cx={x} cy={y} r="4" fill={index === points.split(" ").length - 1 ? "#fb7185" : "#22d3ee"} />;
        })}
      </svg>
      <div className="grid grid-cols-4 gap-2 text-center text-[11px] text-muted-foreground">
        {buckets.map((severity, index) => (
          <span key={severity}>{severityLabels[severity]} · {values[index]}</span>
        ))}
      </div>
    </section>
  );
}

function SourceHeatmap({ modules }: { modules: Cartridge[] }) {
  const visible = modules.slice(0, 8);
  const states: Array<SourceRollup | DataReadiness | SourceState> = ["ready", "partial", "blocked", "error"];
  return (
    <section className="rounded-xl border bg-background dark:border-sky-400/15 dark:bg-[#06111f] p-4" aria-label="Mapa de cobertura">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground dark:text-white">Cobertura por frente</p>
        <Target aria-hidden className="h-4 w-4 text-amber-600 dark:text-amber-300" />
      </div>
      <div className="grid gap-2">
        {visible.map((module) => (
          <div key={module.id} className="grid grid-cols-[120px_repeat(4,minmax(0,1fr))] items-center gap-1 text-[11px]">
            <span className="truncate text-muted-foreground dark:text-slate-300">{businessFrontLabel(module.label)}</span>
            {states.map((state) => {
              const active = module.data_readiness === state || module.source_status === state;
              return (
                <span
                  key={state}
                  title={`${businessFrontLabel(module.label)}: ${readinessLabels[state] || state}`}
                  className={cn(
                    "h-7 rounded border text-center leading-7",
                    active ? readinessTone(state) : "border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-900/80 text-slate-400 dark:text-slate-600",
                  )}
                >
                  {active ? "●" : ""}
                </span>
              );
            })}
          </div>
        ))}
        {!visible.length ? <p className="text-sm text-muted-foreground">Sin frentes activos.</p> : null}
      </div>
    </section>
  );
}

function LiveDataFeed({ items, alerts }: { items: ControlItem[]; alerts: ControlAlert[] }) {
  const feed = [
    ...alerts.map((alert) => ({
      id: `alert:${alert.id}`,
      at: alert.created_at,
      title: sanitizeBusinessCopy(alert.title, "Alerta operativa"),
      detail: sanitizeBusinessCopy(alert.message, "OMEGA requiere revisión operativa."),
      tone: alert.severity,
    })),
    ...items.map((item) => ({
      id: `item:${item.id}`,
      at: item.detected_at,
      title: businessItemTitle(item),
      detail: businessItemLabel(item),
      tone: item.severity,
    })),
  ].sort((left, right) => (parseDate(right.at)?.getTime() || 0) - (parseDate(left.at)?.getTime() || 0)).slice(0, 8);
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-cyan-300/20 dark:bg-[#081423] dark:shadow-[0_0_26px_rgba(34,211,238,0.08)]" aria-label="Cambios recientes">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Cambios recientes</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">Actividad reciente</h2>
        </div>
        <Activity aria-hidden className="h-4 w-4 text-cyan-600 dark:text-cyan-300" />
      </div>
      <div className="space-y-2">
        {feed.map((event) => (
          <article key={event.id} className="grid grid-cols-[72px_1fr] gap-3 rounded-md border bg-background p-3 text-xs dark:border-sky-400/10 dark:bg-[#06111f]">
            <span className="text-muted-foreground">{timeAgo(parseDate(event.at), 0)}</span>
            <div>
              <p className="font-medium text-foreground dark:text-white">{event.title}</p>
              <p className="line-clamp-2 text-muted-foreground">{event.detail}</p>
              <span className={cn("mt-2 inline-flex rounded-full border px-2 py-0.5", severityTone(event.tone))}>{severityLabels[event.tone]}</span>
            </div>
          </article>
        ))}
        {!feed.length ? <p className="text-sm text-muted-foreground">Sin eventos recientes visibles.</p> : null}
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border bg-background p-3 dark:border-sky-400/15 dark:bg-[#07111e]">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm font-semibold text-foreground dark:text-white">{value}</dd>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border bg-background p-3 dark:border-sky-400/15 dark:bg-[#07111e]">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm text-foreground dark:text-white">{value}</p>
    </div>
  );
}

function formatIntelligenceNumber(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "N/D";
  return new Intl.NumberFormat("es-MX", { maximumFractionDigits: 2 }).format(value);
}

function formatIntelligencePercent(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "N/D";
  return `${(value * 100).toFixed(1)}%`;
}

function StatePanel({ icon: Icon, text, spinning = false }: { icon: LucideIcon; text: string; spinning?: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border bg-background p-4 text-sm text-muted-foreground">
      <Icon aria-hidden className={cn("h-4 w-4", spinning ? "animate-spin" : "")} />
      <span>{text}</span>
    </div>
  );
}

function ActionButton({ children, disabled, loading, onClick, icon: Icon, tone = "normal" }: { children: React.ReactNode; disabled?: boolean; loading?: boolean; onClick: () => void; icon: LucideIcon; tone?: "normal" | "danger" }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        tone === "danger" ? "border-destructive/40 text-destructive hover:bg-destructive/10" : "",
      )}
    >
      {loading ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Icon aria-hidden className="h-4 w-4" />}
      {children}
    </button>
  );
}
