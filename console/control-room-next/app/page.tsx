"use client";

import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Bell,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  CircleDollarSign,
  Clock3,
  ClipboardCheck,
  FileCheck2,
  Filter,
  Gauge,
  Layers3,
  Loader2,
  Play,
  RefreshCcw,
  ShieldCheck,
  Send,
  UserPlus,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";

type Severity = "critical" | "high" | "medium" | "low";
type SourceState = "ok" | "empty" | "missing" | "unavailable" | "invalid_schema" | "blocked" | "no_permission";
type SourceRollup = SourceState | "attention" | "inactive" | "no_sources";
type LoadState = "loading" | "ready" | "error";
type DetailMode = "auto" | "manual" | null;
type RefreshReason = "initial" | "manual" | "poll" | "mutation";

interface SourceStatus {
  dataset: string;
  cartridge: string;
  connector_id?: string;
  module_id?: string;
  domain: string;
  module: string;
  status: SourceState;
  count: number;
  error?: string;
  checked_at?: string;
}

interface Kpi {
  label: string;
  value: string | number;
  tone: "neutral" | "attention";
  bad?: boolean;
  sql?: string;
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

interface OmegaAction {
  id: string;
  sys?: string;
  act?: string;
  label: string;
  done?: boolean;
  approved: boolean;
  auto?: boolean;
}

interface ActionTemplate {
  template_id: string;
  cartridge_id: string;
  label: string;
  description: string;
  action_kind: string;
  risk_level: string;
  mode_default: string;
  requires_approval: boolean;
}

interface ImpactDriver {
  label: string;
  value?: string | number | null;
  currency?: string;
  unit?: string;
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
  created_at?: string;
  updated_at?: string;
}

interface ThresholdPayload {
  thresholds: DetectionThreshold[];
  summary?: {
    total: number;
    active: number;
    disabled: number;
    by_cartridge?: Record<string, number>;
    by_anomaly_type?: Record<string, number>;
    recent?: DetectionThreshold[];
  };
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
  source_decision_id?: number | null;
  cartridge_id?: string;
  anomaly_type?: string;
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
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string | null;
}

interface ActivityPayload {
  item_id: string;
  activity: ActivityEntry[];
  counts: {
    events: number;
    executions: number;
    decision_actions: number;
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
  updated_at?: string;
  updated_by?: string;
}

interface Omega {
  signals: Record<string, unknown>;
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
    actions: OmegaAction[];
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
}

interface ControlItem {
  id: string;
  kind: "anomaly" | "control_item" | "source_state";
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
  impact_status?: "ok" | "unavailable" | "missing" | "empty";
  confidence?: number | null;
  priority_score?: number | null;
  priority?: {
    score: number;
    band: Severity;
    formula?: string;
    drivers?: Array<ImpactDriver & { points?: number }>;
  };
  impact_drivers?: ImpactDriver[];
  impact_formula?: string;
  impact_explanation?: string;
  thresholds_applied?: DetectionThreshold[];
  threshold_state?: "critical" | "warning" | "default" | string;
  related_lessons?: Lesson[];
  lesson_count?: number;
  lesson_applications?: LessonApplication[];
  action_templates?: ActionTemplate[];
  omega: Omega;
}

interface ControlAlert {
  id: string;
  item_id: string;
  alert_type: "source_health" | "threshold_breach" | "learned_pattern" | "critical_signal" | "watchlist" | string;
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
  reason?: string | null;
  acknowledged_at?: string | null;
  assigned_at?: string | null;
  snoozed_until?: string | null;
  threshold_state?: string;
  lesson_count?: number;
  impact_estimate?: number | null;
  impact_currency?: string;
  recommended_action?: string;
  drivers?: Array<ImpactDriver & { points?: number }>;
  route_key?: string;
  push_ready?: boolean;
  delivery?: {
    status: string;
    channels?: string[];
    reason?: string;
  };
  created_at?: string;
  updated_at?: string;
}

interface Dashboard {
  meta?: {
    generated_at?: string;
    refresh_interval_seconds?: number;
    live_mode?: "polling" | string;
    source_count?: number;
    item_count?: number;
  };
  workspace: {
    tenant_id?: string;
    workspace_id: string;
  };
  period: string;
  omega_steps: Array<{ id: string; label: string }>;
  summary: {
    total_items: number;
    total_anomalies: number;
    control_items: number;
    by_severity: Record<Severity, number>;
    by_cartridge: Record<string, number>;
    by_domain: Record<string, number>;
    critical: number;
    attention: number;
    open_decisions: number;
    active_connectors?: number;
    active_modules?: number;
    active_cartridges: number;
    operational_cartridges: number;
    source_states: Record<SourceState, number>;
    cycle_counts?: Record<string, number>;
    financial?: FinancialSummary;
    thresholds?: {
      active: number;
      total: number;
      by_cartridge?: Record<string, number>;
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
      by_domain?: Record<string, number>;
      by_severity?: Record<string, number>;
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

interface FinancialRisk {
  label: string;
  owner: string;
  margin_pct?: number | null;
  wip_usd: number;
  margin_usd: number;
}

interface FinancialSummary {
  status: "ok" | "empty" | "missing" | "unavailable" | "not_installed";
  revenue_usd: number;
  billed_usd: number;
  wip_usd: number;
  cost_usd: number;
  margin_usd: number;
  margin_pct?: number | null;
  sales_revenue: number;
  backlog_value: number;
  open_orders: number;
  oldest_backlog_days: number;
  purchase_spend: number;
  risk_projects: FinancialRisk[];
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
  critical: "Critica",
  high: "Alta",
  medium: "Atencion",
  low: "Baja",
};

const statusLabels: Record<string, string> = {
  open: "Abierto",
  in_review: "En revision",
  decision_created: "Decision",
  approved: "Aprobado",
  dismissed: "Descartado",
  resolved: "Resuelto",
};

const manualTabs = ["Investigacion", "Opciones", "Decision", "Ejecucion", "Control", "Reglas"];
const manualStepIds = ["investigation", "options", "decision", "execution", "control", "lessons"];
const DEFAULT_REFRESH_INTERVAL_SECONDS = 30;
const terminalStatuses = new Set(["approved", "dismissed", "resolved"]);
const defaultOmegaSteps = [
  { id: "signals", label: "Senales" },
  { id: "investigation", label: "Investigacion" },
  { id: "options", label: "Opciones" },
  { id: "decision", label: "Decision" },
  { id: "execution", label: "Ejecucion" },
  { id: "control", label: "Control" },
  { id: "lessons", label: "Lecciones" },
];

const sourceStateLabels: Record<SourceState | SourceRollup, string> = {
  ok: "Operativa",
  empty: "Vacia",
  missing: "Faltante",
  unavailable: "No disponible",
  invalid_schema: "Schema invalido",
  blocked: "Bloqueada",
  no_permission: "Sin permiso",
  attention: "Atencion",
  inactive: "Inactiva",
  no_sources: "Sin fuentes",
};

const alertTypeLabels: Record<string, string> = {
  source_health: "Salud de fuente",
  threshold_breach: "Umbral excedido",
  learned_pattern: "Patron aprendido",
  critical_signal: "Senal critica",
  watchlist: "Watchlist",
};

const alertStatusLabels: Record<string, string> = {
  open: "Abierta",
  acknowledged: "Reconocida",
  snoozed: "Pospuesta",
  assigned: "Asignada",
  false_positive: "Falso positivo",
};

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(init?.method && init.method !== "GET" ? { "X-CSRF-Token": csrfToken() } : {}),
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (response.status === 401) {
    window.location.href = `/login?next=${encodeURIComponent("/control-room")}`;
    throw new Error("Sesion requerida");
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the generic HTTP detail.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function fmtDate(value: string): string {
  if (!value) return "Sin timestamp";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function fmtMoney(value?: number | null): string {
  const safe = Number(value || 0);
  if (Math.abs(safe) >= 1_000_000) return `$${(safe / 1_000_000).toFixed(1)}M`;
  if (Math.abs(safe) >= 1_000) return `$${(safe / 1_000).toFixed(0)}K`;
  return `$${safe.toFixed(0)}`;
}

function fmtPct(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/D";
  return `${Number(value).toFixed(1)}%`;
}

function activeOpen(item: ControlItem): boolean {
  return !terminalStatuses.has(item.status);
}

function metadataText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return `${value.length} registros`;
  }
  return "Evidencia guardada";
}

function activityDescription(entry: ActivityEntry): string {
  if (entry.error) return entry.error;
  const message = entry.result?.message;
  if (typeof message === "string" && message) return message;
  if (entry.metadata?.note) return metadataText(entry.metadata.note);
  if (entry.metadata?.option_id) return `Opcion ${metadataText(entry.metadata.option_id)}`;
  if (entry.metadata?.decision_id) return `Decision #${metadataText(entry.metadata.decision_id)}`;
  if (entry.metadata?.template_id) return `Template ${metadataText(entry.metadata.template_id)}`;
  if (entry.metadata?.lessons) return `Lecciones: ${metadataText(entry.metadata.lessons)}`;
  if (entry.metadata?.lesson_id) return `Leccion #${metadataText(entry.metadata.lesson_id)} aplicada`;
  return entry.status || entry.type;
}

function timeAgo(value: Date | null, tick = 0): string {
  void tick;
  if (!value) return "Sin actualizar";
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

function parseDate(value?: string): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function thresholdKey(threshold: Pick<DetectionThreshold, "cartridge_id" | "anomaly_type" | "metric">): string {
  return `${threshold.cartridge_id}:${threshold.anomaly_type}:${threshold.metric}`;
}

function thresholdLabel(threshold: Pick<DetectionThreshold, "anomaly_type" | "metric">): string {
  return `${threshold.anomaly_type} / ${threshold.metric}`;
}

function dedupeLessons(lessons: Lesson[]): Lesson[] {
  const seen = new Set<string>();
  const unique: Lesson[] = [];
  lessons.forEach((lesson) => {
    const key = String(lesson.id || `${lesson.item_id}:${lesson.cartridge_id}:${lesson.anomaly_type}:${lesson.rule}`);
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(lesson);
  });
  return unique;
}

function controlRoomUrl(nextDomain: string, nextModule: string): string {
  const params = new URLSearchParams();
  if (nextModule !== "all") {
    params.set("module", nextModule);
  } else if (nextDomain !== "all") {
    params.set("domain", nextDomain);
  }
  const query = params.toString();
  return query ? `/control-room?${query}` : "/control-room";
}

function pushControlRoomUrl(nextDomain: string, nextModule: string): void {
  if (typeof window === "undefined") return;
  window.history.pushState(null, "", controlRoomUrl(nextDomain, nextModule));
}

function cycleCountsForItems(items: ControlItem[]): Record<string, number> {
  const counts = Object.fromEntries(defaultOmegaSteps.map((step) => [step.id, 0])) as Record<string, number>;
  items.forEach((item) => {
    counts.signals += 1;
    if (item.status === "open" || item.status === "in_review") counts.investigation += 1;
    if (item.status === "in_review" || item.selected_option_id) counts.options += 1;
    if (item.decision_id || ["decision_created", "approved", "resolved"].includes(item.status)) counts.decision += 1;
    if (item.status === "approved" || item.status === "resolved") {
      counts.execution += 1;
      counts.control += 1;
      counts.lessons += 1;
    } else if (item.status === "dismissed") {
      counts.control += 1;
    }
  });
  return counts;
}

export default function ControlRoomPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailMode, setDetailMode] = useState<DetailMode>(null);
  const [manualTab, setManualTab] = useState(0);
  const [domain, setDomain] = useState("all");
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [cartridge, setCartridge] = useState("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [busyAction, setBusyAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [activityByItem, setActivityByItem] = useState<Record<string, ActivityPayload>>({});
  const [activityLoading, setActivityLoading] = useState("");
  const [activityError, setActivityError] = useState("");
  const [urlHydrated, setUrlHydrated] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [nextRefreshAt, setNextRefreshAt] = useState<Date | null>(null);
  const [syncError, setSyncError] = useState("");
  const [clockTick, setClockTick] = useState(0);
  const [lessonsPayload, setLessonsPayload] = useState<LessonsPayload | null>(null);
  const [lessonsLoading, setLessonsLoading] = useState(false);
  const [lessonsError, setLessonsError] = useState("");
  const [thresholdsPayload, setThresholdsPayload] = useState<ThresholdPayload | null>(null);
  const [thresholdsLoading, setThresholdsLoading] = useState(false);
  const [thresholdsError, setThresholdsError] = useState("");
  const [thresholdSaving, setThresholdSaving] = useState(false);
  const [thresholdSaveError, setThresholdSaveError] = useState("");
  const [thresholdSaveMessage, setThresholdSaveMessage] = useState("");
  const [alertActionError, setAlertActionError] = useState("");
  const [alertActionMessage, setAlertActionMessage] = useState("");

  const load = useCallback(async (
    preferredId?: string,
    options: { background?: boolean; reason?: RefreshReason } = {},
  ) => {
    const background = Boolean(options.background);
    void options.reason;
    if (!background) setError("");
    setRefreshing(true);
    setState((current) => (current === "ready" || background ? current : "loading"));
    try {
      const nextDashboard = await apiJson<Dashboard>("/api/control-room/dashboard", { cache: "no-store" });
      setDashboard(nextDashboard);
      setSelectedId((current) => preferredId || current || nextDashboard.items[0]?.id || "");
      const generatedAt = parseDate(nextDashboard.meta?.generated_at) || new Date();
      const refreshSeconds = nextDashboard.meta?.refresh_interval_seconds || DEFAULT_REFRESH_INTERVAL_SECONDS;
      setLastUpdatedAt(generatedAt);
      setNextRefreshAt(new Date(Date.now() + refreshSeconds * 1000));
      setSyncError("");
      setState("ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : "No se pudo cargar la sala de control";
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
    setActivityError("");
    setActivityLoading(itemId);
    try {
      const payload = await apiJson<ActivityPayload>(
        `/api/control-room/items/${encodeURIComponent(itemId)}/activity`,
      );
      setActivityByItem((current) => ({ ...current, [itemId]: payload }));
    } catch (err) {
      setActivityError(err instanceof Error ? err.message : "No se pudo cargar la bitacora operativa");
    } finally {
      setActivityLoading((current) => (current === itemId ? "" : current));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load(undefined, { reason: "initial" });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setClockTick((current) => current + 1);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, []);

  const cartridges = useMemo(() => dashboard?.cartridges ?? [], [dashboard]);
  const domains = useMemo(() => dashboard?.domains ?? [], [dashboard]);
  const items = useMemo(() => dashboard?.items ?? [], [dashboard]);
  const activeModules = cartridges.filter((item) => item.active && !item.operational);
  const activeConnectorCount = dashboard?.summary.active_connectors
    ?? new Set(activeModules.map((item) => item.connector_id || item.id)).size;
  const activeModuleCount = dashboard?.summary.active_modules ?? activeModules.length;

  useEffect(() => {
    if (urlHydrated || !dashboard) return undefined;
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
  const contextSourceStates = useMemo(() => {
    const states = {
      ok: 0,
      empty: 0,
      missing: 0,
      unavailable: 0,
      invalid_schema: 0,
      blocked: 0,
      no_permission: 0,
    } satisfies Record<SourceState, number>;
    contextSources.forEach((source) => {
      states[source.status] = (states[source.status] ?? 0) + 1;
    });
    return states;
  }, [contextSources]);

  const groupedItems = useMemo(() => domains.map((group) => ({
    domain: group,
    items: filtered.filter((item) => item.domain === group.label),
  })).filter((group) => group.items.length > 0), [domains, filtered]);

  const visibleDomains = useMemo(() => domains
    .filter((item) => item.modules.length > 0)
    .filter((item) => domain === "all" || item.label === domain)
    .map((item) => {
      const modules = cartridge === "all" ? item.modules : item.modules.filter((module) => module.id === cartridge);
      if (cartridge === "all") return item;
      return {
        ...item,
        modules,
        item_count: modules.reduce((sum, module) => sum + module.item_count, 0),
        critical_count: modules.reduce((sum, module) => sum + module.critical_count, 0),
        cartridge_count: modules.length,
      };
    })
    .filter((item) => item.modules.length > 0), [cartridge, domain, domains]);

  const contextCycleCounts = useMemo(() => (
    domain === "all" && cartridge === "all" && severity === "all"
      ? dashboard?.summary.cycle_counts
      : cycleCountsForItems(filtered)
  ), [cartridge, dashboard?.summary.cycle_counts, domain, filtered, severity]);

  const contextCritical = filtered.filter((item) => item.severity === "critical").length;
  const contextAttention = filtered.filter((item) => item.severity === "high" || item.severity === "medium").length;
  const contextDecisionCount = domain === "all" && cartridge === "all" && severity === "all"
    ? dashboard?.summary.open_decisions ?? 0
    : filtered.filter((item) => Boolean(item.decision_id)).length;
  const selectedDomain = domains.find((item) => item.label === domain);
  const selectedModule = cartridges.find((item) => item.id === cartridge);
  const activeContext: ActiveContext = selectedModule && cartridge !== "all" ? {
    level: "module",
    title: selectedModule.label,
    eyebrow: "Modulo operativo",
    subtitle: `${selectedModule.domain} · ${selectedModule.connector_label || selectedModule.connector_id || selectedModule.id}`,
    domainLabel: selectedModule.domain,
    moduleId: selectedModule.id,
    moduleLabel: selectedModule.label,
  } : selectedDomain && domain !== "all" ? {
    level: "domain",
    title: selectedDomain.label,
    eyebrow: "Dominio operativo",
    subtitle: `${selectedDomain.cartridge_count} modulos · ${filtered.length} senales`,
    domainLabel: selectedDomain.label,
  } : {
    level: "portfolio",
    title: "Dashboard Operativo",
    eyebrow: "Sala de Control OMEGA",
    subtitle: `${activeConnectorCount} conectores activos · ${activeModuleCount} modulos operativos`,
  };

  const selected = filtered.find((item) => item.id === selectedId)
    || items.find((item) => item.id === selectedId)
    || filtered[0]
    || null;
  const selectedConnectorId = selectedModule?.connector_id || "";

  const loadLessons = useCallback(async () => {
    setLessonsLoading(true);
    setLessonsError("");
    try {
      const params = new URLSearchParams();
      if (selectedConnectorId) {
        params.set("cartridge_id", selectedConnectorId);
      }
      const query = params.toString();
      const payload = await apiJson<LessonsPayload>(
        `/api/control-room/lessons${query ? `?${query}` : ""}`,
        { cache: "no-store" },
      );
      setLessonsPayload(payload);
    } catch (err) {
      setLessonsError(err instanceof Error ? err.message : "No se pudieron cargar lecciones");
    } finally {
      setLessonsLoading(false);
    }
  }, [selectedConnectorId]);

  const loadThresholds = useCallback(async () => {
    setThresholdsLoading(true);
    setThresholdsError("");
    try {
      const payload = await apiJson<ThresholdPayload>("/api/control-room/thresholds", { cache: "no-store" });
      setThresholdsPayload(payload);
    } catch (err) {
      setThresholdsError(err instanceof Error ? err.message : "No se pudieron cargar umbrales");
    } finally {
      setThresholdsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (state !== "ready") return;
    const timer = window.setTimeout(() => {
      void loadLessons();
      void loadThresholds();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [domain, cartridge, loadLessons, loadThresholds, state]);

  useEffect(() => {
    if (state !== "ready") return undefined;
    const refreshSeconds = dashboard?.meta?.refresh_interval_seconds || DEFAULT_REFRESH_INTERVAL_SECONDS;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void load(selectedId, { background: true, reason: "poll" });
        void loadLessons();
        void loadThresholds();
      }
    }, Math.max(10, refreshSeconds) * 1000);
    return () => window.clearInterval(timer);
  }, [dashboard?.meta?.refresh_interval_seconds, load, loadLessons, loadThresholds, selectedId, state]);

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

  const contextAlerts = useMemo(() => {
    const alerts = dashboard?.alerts || [];
    if (domain === "all" && cartridge === "all") return alerts;
    return alerts.filter((alert) => (
      (domain === "all" || alert.domain === domain)
      && (cartridge === "all" || alert.module_id === cartridge)
    ));
  }, [cartridge, dashboard?.alerts, domain]);

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
        source: "workspace",
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

  useEffect(() => {
    if (!detailOpen || !selected?.id) return undefined;
    const timer = window.setTimeout(() => {
      void loadActivity(selected.id);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [detailOpen, loadActivity, selected?.id]);

  function openItem(item: ControlItem) {
    setSelectedId(item.id);
    setDetailOpen(true);
    setDetailMode(null);
    setManualTab(0);
    setActionError("");
    void recordStep(item, "signals", "Senal abierta desde el dashboard", undefined, true);
  }

  function toggleDomain(domainId: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(domainId)) next.delete(domainId);
      else next.add(domainId);
      return next;
    });
  }

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
    void load(nextItem.id, { background: true, reason: "mutation" });
    void loadActivity(nextItem.id);
    void loadLessons();
    void loadThresholds();
  }

  async function saveThreshold(draft: ThresholdDraft) {
    setThresholdSaving(true);
    setThresholdSaveError("");
    setThresholdSaveMessage("");
    try {
      const payload = await apiJson<{ threshold: DetectionThreshold }>(
        "/api/control-room/thresholds",
        {
          method: "PATCH",
          body: JSON.stringify({
            cartridge_id: draft.cartridge_id,
            anomaly_type: draft.anomaly_type,
            metric: draft.metric,
            warning_value: draft.warning_value === "" ? null : Number(draft.warning_value),
            critical_value: draft.critical_value === "" ? null : Number(draft.critical_value),
            currency: draft.currency || "USD",
            enabled: draft.enabled,
            metadata: { source: "control_room_ui" },
          }),
        },
      );
      setThresholdSaveMessage(`Umbral guardado: ${thresholdLabel(payload.threshold)}`);
      void loadThresholds();
      void load(selectedId, { background: true, reason: "mutation" });
    } catch (err) {
      setThresholdSaveError(err instanceof Error ? err.message : "No se pudo guardar el umbral");
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
    if (!silent) {
      setBusyAction(`step:${item.id}:${stepId}:${controlId || ""}`);
      setActionError("");
    }
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/step`,
        {
          method: "POST",
          body: JSON.stringify({ step_id: stepId, note, control_id: controlId || "" }),
        },
      );
      mergeDashboardItem(payload.item);
      void loadActivity(item.id);
    } catch (err) {
      if (!silent) {
        setActionError(err instanceof Error ? err.message : "No se pudo registrar el paso OMEGA");
      }
    } finally {
      if (!silent) setBusyAction("");
    }
  }

  async function updateControl(
    item: ControlItem,
    control: ControlChecklistItem,
    status: "in_progress" | "closed" | "blocked",
  ) {
    setBusyAction(`control:${item.id}:${control.id}:${status}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem; control: ControlChecklistItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/control/${encodeURIComponent(control.id)}`,
        {
          method: "POST",
          body: JSON.stringify({
            status,
            owner: control.owner,
            note: status === "closed"
              ? `Control ${control.id} confirmado desde Sala de Control`
              : `Control ${control.id} actualizado desde Sala de Control`,
          }),
        },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo actualizar el control");
    } finally {
      setBusyAction("");
    }
  }

  async function createLesson(item: ControlItem, rule: string) {
    setBusyAction(`lesson:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/lessons`,
        { method: "POST", body: JSON.stringify({ rule }) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo guardar la leccion");
    } finally {
      setBusyAction("");
    }
  }

  async function applyLesson(item: ControlItem, lesson: Lesson) {
    if (!lesson.id) return;
    setBusyAction(`applyLesson:${item.id}:${lesson.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/lessons/${lesson.id}/apply`,
        { method: "POST", body: JSON.stringify({ note: "Aplicada desde Sala de Control" }) },
      );
      refreshAfterMutation(payload.item);
      void loadLessons();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo aplicar la leccion");
    } finally {
      setBusyAction("");
    }
  }

  async function createDecision(item: ControlItem) {
    setBusyAction(`decision:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ decision: { id: number }; item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/decision`,
        { method: "POST", body: JSON.stringify({}) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo crear la decision");
    } finally {
      setBusyAction("");
    }
  }

  async function previewAction(item: ControlItem) {
    setBusyAction(`preview:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/action-preview`,
        { method: "POST", body: JSON.stringify({}) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo generar el preview");
    } finally {
      setBusyAction("");
    }
  }

  async function dryRunAction(item: ControlItem) {
    setBusyAction(`dryrun:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/action-dry-run`,
        { method: "POST", body: JSON.stringify({}) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo validar el dry-run");
    } finally {
      setBusyAction("");
    }
  }

  async function executeLive(item: ControlItem) {
    setBusyAction(`execute:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/execute`,
        { method: "POST", body: JSON.stringify({}) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Write-back externo bloqueado para V1");
    } finally {
      setBusyAction("");
    }
  }

  async function runAuto(item: ControlItem) {
    setBusyAction(`auto:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/auto-run`,
        { method: "POST", body: JSON.stringify({}) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo completar el modo automatico seguro");
    } finally {
      setBusyAction("");
    }
  }

  async function selectOption(item: ControlItem, optionId: string) {
    if (terminalStatuses.has(item.status)) return;
    setBusyAction(`option:${item.id}:${optionId}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/option`,
        { method: "POST", body: JSON.stringify({ option_id: optionId }) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo seleccionar la opcion");
    } finally {
      setBusyAction("");
    }
  }

  async function approve(item: ControlItem) {
    setBusyAction(`approve:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/approve`,
        { method: "POST", body: JSON.stringify(item.decision_id ? { decision_id: item.decision_id } : {}) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo aprobar la recomendacion");
    } finally {
      setBusyAction("");
    }
  }

  async function dismiss(item: ControlItem) {
    setBusyAction(`dismiss:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/dismiss`,
        { method: "POST", body: JSON.stringify({ reason: "Descartado desde Sala de Control" }) },
      );
      refreshAfterMutation(payload.item);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo descartar el item");
    } finally {
      setBusyAction("");
    }
  }

  async function operateAlert(
    alert: ControlAlert,
    operation: "ack" | "snooze" | "assign" | "false-positive",
  ) {
    const labels = {
      ack: "Alerta reconocida y enviada a investigacion.",
      snooze: "Alerta pospuesta 24h; queda visible con estado operativo.",
      assign: "Alerta asignada al usuario actual.",
      "false-positive": "Alerta cerrada como falso positivo.",
    };
    const bodyByOperation = {
      ack: { note: "Reconocida desde cola operativa" },
      snooze: { hours: 24, note: "Pospuesta 24h desde cola operativa" },
      assign: { note: "Asignada desde cola operativa" },
      "false-positive": { reason: "Marcado falso positivo desde Sala de Control" },
    };
    setBusyAction(`alert:${operation}:${alert.item_id}`);
    setAlertActionError("");
    setAlertActionMessage("");
    try {
      const payload = await apiJson<{ alert?: ControlAlert | null; item: ControlItem }>(
        `/api/control-room/alerts/${encodeURIComponent(alert.item_id)}/${operation}`,
        {
          method: "POST",
          body: JSON.stringify(bodyByOperation[operation]),
        },
      );
      setAlertActionMessage(labels[operation]);
      refreshAfterMutation(payload.item);
    } catch (err) {
      setAlertActionError(err instanceof Error ? err.message : "No se pudo operar la alerta");
    } finally {
      setBusyAction("");
    }
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

  function refreshAll() {
    void load(selectedId, { reason: "manual" });
    void loadLessons();
    void loadThresholds();
  }

  return (
    <main className="app-shell">
      <Sidebar
        domains={domains}
        cartridges={cartridges}
        totalItems={dashboard?.summary.total_items ?? 0}
        activeConnectors={activeConnectorCount}
        activeModules={activeModuleCount}
        domain={domain}
        cartridge={cartridge}
        onAll={navigateAll}
        onDomain={navigateDomain}
        onCartridge={navigateModule}
      />

      <section className="main-surface">
        <Header
          context={activeContext}
          period={dashboard?.period || "Periodo operativo"}
          activeConnectors={activeConnectorCount}
          activeModules={activeModuleCount}
          loading={state === "loading" || refreshing}
          lastUpdated={timeAgo(lastUpdatedAt, clockTick)}
          nextRefresh={timeUntil(nextRefreshAt, clockTick)}
          syncError={syncError}
          liveMode={dashboard?.meta?.live_mode || "polling"}
          refreshSeconds={dashboard?.meta?.refresh_interval_seconds || DEFAULT_REFRESH_INTERVAL_SECONDS}
          onRefresh={refreshAll}
        />

        {state === "error" ? (
          <section className="state-panel error-state" role="alert">
            <AlertTriangle aria-hidden />
            <div>
              <h2>No se pudo cargar la Sala de Control</h2>
              <p>{error}</p>
            </div>
          </section>
        ) : null}

        {detailOpen && selected ? (
          <DetailPage
            item={selected}
            omegaSteps={dashboard?.omega_steps ?? defaultOmegaSteps}
            mode={detailMode}
            manualTab={manualTab}
            busyAction={busyAction}
            actionError={actionError}
            activity={activityByItem[selected.id]}
            activityLoading={activityLoading === selected.id}
            activityError={activityError}
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
            contextSourceStates={contextSourceStates}
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
      </section>
    </main>
  );
}

function Sidebar({
  domains,
  cartridges,
  totalItems,
  activeConnectors,
  activeModules,
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
  domain: string;
  cartridge: string;
  onAll: () => void;
  onDomain: (domain: string) => void;
  onCartridge: (cartridge: string, domain: string) => void;
}) {
  return (
    <aside className="sidebar" aria-label="Navegacion operativa">
      <div className="sidebar-logo">
        <strong>OMEGA</strong>
        <span>by EPI-USE</span>
      </div>

      <button type="button" className={`sidebar-all ${domain === "all" && cartridge === "all" ? "active" : ""}`} onClick={onAll}>
        <CircleDot aria-hidden />
        <span>Todos</span>
        <em>{totalItems}</em>
      </button>

      {domains.filter((group) => group.modules.length > 0).map((group) => (
        <section className="sidebar-section" key={group.id}>
          <button
            type="button"
            className={`sidebar-section-label ${domain === group.label ? "active" : ""}`}
            onClick={() => onDomain(group.label)}
            style={{ "--accent": group.accent } as CSSProperties}
          >
            {group.label}
            <em>{group.item_count}</em>
          </button>
          <div className="sidebar-items">
            {group.modules.map((module) => {
              const installed = cartridges.find((item) => item.id === module.id);
              return (
                <button
                  type="button"
                  key={`${group.id}-${module.id}`}
                  className={`sidebar-item ${cartridge === module.id ? "active" : ""}`}
                  onClick={() => onCartridge(module.id, group.label)}
                  disabled={installed ? !installed.active : false}
                  style={{ "--accent": module.accent } as CSSProperties}
                >
                  <span className="sidebar-dot" aria-hidden />
                  <span>{module.label}</span>
                  <em>{module.item_count}</em>
                </button>
              );
            })}
          </div>
        </section>
      ))}

      <footer className="sidebar-footer">
        {activeConnectors} conectores · {activeModules} modulos
      </footer>
    </aside>
  );
}

function Header({
  context,
  period,
  activeConnectors,
  activeModules,
  loading,
  lastUpdated,
  nextRefresh,
  syncError,
  liveMode,
  refreshSeconds,
  onRefresh,
}: {
  context: ActiveContext;
  period: string;
  activeConnectors: number;
  activeModules: number;
  loading: boolean;
  lastUpdated: string;
  nextRefresh: string;
  syncError: string;
  liveMode: string;
  refreshSeconds: number;
  onRefresh: () => void;
}) {
  return (
    <header className="header">
      <div>
        <span className="header-eyebrow">{context.eyebrow}</span>
        <h1>{context.title}</h1>
        <p>Sala de Control / {context.level === "portfolio" ? "Todos" : context.title} · {period}</p>
      </div>
      <div className="header-actions">
        <span className={`live-pill ${syncError ? "warning" : ""}`}>
          <Activity aria-hidden />
          {syncError ? "Sync con alerta" : `${liveMode === "polling" ? "Vivo" : liveMode} ${refreshSeconds}s`}
        </span>
        <span className="freshness-pill">Actualizado {lastUpdated}</span>
        <span className="next-refresh-pill">Siguiente {nextRefresh}</span>
        <span>{activeConnectors} conectores · {activeModules} modulos operativos</span>
        <button className="tool-button" type="button" onClick={onRefresh} disabled={loading}>
          {loading ? <Loader2 aria-hidden className="spin" /> : <RefreshCcw aria-hidden />}
          Refrescar
        </button>
      </div>
      {syncError ? <p className="sync-warning" role="status">Ultimo refresh fallido: {syncError}</p> : null}
    </header>
  );
}

function DashboardView({
  dashboard,
  state,
  allDomains,
  visibleDomains,
  context,
  contextSources,
  contextSourceStates,
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
  contextSourceStates: Record<SourceState, number>;
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
  onOperateAlert: (alert: ControlAlert, operation: "ack" | "snooze" | "assign" | "false-positive") => void;
  onToggleDomain: (domainId: string) => void;
  onOpenItem: (item: ControlItem) => void;
}) {
  const contextIsPortfolio = context.level === "portfolio" && severity === "all";
  return (
    <div className="content">
      <div className="tabs" role="group" aria-label="Filtro por dominio">
        <button type="button" className={domain === "all" ? "active" : ""} onClick={() => onDomain("all")}>
          Todos
        </button>
        {allDomains.filter((item) => item.modules.length > 0).map((item) => (
          <button type="button" key={item.label} className={domain === item.label ? "active" : ""} onClick={() => onDomain(item.label)}>
            {item.label} <em>{item.item_count}</em>
          </button>
        ))}
        <label className="severity-filter">
          <Filter aria-hidden />
          <select value={severity} onChange={(event) => onSeverity(event.target.value as Severity | "all")}>
            <option value="all">Todas</option>
            <option value="critical">Critica</option>
            <option value="high">Alta</option>
            <option value="medium">Atencion</option>
            <option value="low">Baja</option>
          </select>
        </label>
      </div>

      <ContextPanel
        context={context}
        sourceCount={contextSources.length}
        moduleCount={contextModules.length}
        itemCount={filteredCount}
        openCount={openCount}
        severity={severity}
      />

      <ContextOperations
        context={context}
        modules={contextModules}
        sources={contextSources}
        items={groupedItems.flatMap((group) => group.items)}
        lessons={contextLessons}
        alerts={contextAlerts}
        onOpenItem={onOpenItem}
        onCartridge={onCartridge}
      />

      <AlertQueuePanel
        context={context}
        alerts={contextAlerts}
        busyAction={busyAction}
        actionError={alertActionError}
        actionMessage={alertActionMessage}
        onOperateAlert={onOperateAlert}
        onOpenItem={onOpenItem}
        items={dashboard?.items ?? groupedItems.flatMap((group) => group.items)}
      />

      <section className="summary-row" aria-label="Resumen ejecutivo">
        <SummaryCard icon={Gauge} label="Senales" value={contextIsPortfolio ? dashboard?.summary.total_items ?? "..." : filteredCount} />
        <SummaryCard icon={AlertTriangle} label="Criticas" value={contextIsPortfolio ? dashboard?.summary.critical ?? 0 : contextCritical} tone="critical" />
        <SummaryCard icon={Activity} label="Atencion" value={contextIsPortfolio ? dashboard?.summary.attention ?? 0 : contextAttention} tone="attention" />
        <SummaryCard icon={FileCheck2} label={contextIsPortfolio ? "Decisiones abiertas" : "Con decision"} value={contextDecisionCount} />
      </section>

      <section className="operations-strip" aria-label="Ciclo y salud operativa">
        <OmegaCycleBar
          steps={dashboard?.omega_steps ?? defaultOmegaSteps}
          counts={contextCycleCounts}
        />
        <SourceHealthPanel
          sourceStates={contextSourceStates}
          totalSources={contextSources.length}
        />
        <ThresholdPanel thresholds={dashboard?.summary.thresholds} />
        <LearningPanel lessons={dashboard?.summary.lessons} contextLessons={contextLessons} loading={lessonsLoading} />
      </section>

      <ContextInsightPanel
        context={context}
        modules={contextModules}
        sources={contextSources}
        items={groupedItems.flatMap((group) => group.items)}
        financial={dashboard?.summary.financial}
      />

      <section className="section-block" aria-label="Estado por dominio">
        <p className="section-kicker">{cartridge === "all" ? "Estado por dominio" : "Estado del modulo"}</p>
        <div className="domain-list">
          {visibleDomains.filter((item) => item.modules.length > 0).map((item) => (
            <DomainSection
              key={item.id}
              domain={item}
              collapsed={collapsed.has(item.id)}
              onToggle={() => onToggleDomain(item.id)}
            />
          ))}
        </div>
      </section>

      <SourceInventoryPanel context={context} sources={contextSources} />

      <ThresholdRulesBoard
        context={context}
        thresholds={contextThresholds}
        candidates={thresholdCandidates}
        loading={thresholdsLoading}
        error={thresholdsError}
        saving={thresholdSaving}
        saveError={thresholdSaveError}
        saveMessage={thresholdSaveMessage}
        onSave={onSaveThreshold}
      />

      <LessonsBoard
        context={context}
        lessons={contextLessons}
        loading={lessonsLoading}
        error={lessonsError}
      />

      <section className="section-block" aria-label="Anomalias detectadas">
        <div className="anomaly-heading">
          <div>
            <p className="section-kicker">Anomalias detectadas</p>
            <h2>{filteredCount} senales priorizadas</h2>
          </div>
          <span>{openCount} abiertas</span>
        </div>

        {state === "loading" ? (
          <div className="state-panel">
            <Loader2 aria-hidden className="spin" />
            <span>Cargando datos operativos...</span>
          </div>
        ) : null}

        {state === "ready" && groupedItems.length === 0 ? (
          <div className="state-panel">
            <CheckCircle2 aria-hidden />
            <span>No hay senales para estos filtros.</span>
          </div>
        ) : null}

        <div className="anomaly-groups">
          {groupedItems.map((group) => (
            <section className="anomaly-domain-group" key={group.domain.id}>
              <header className="anomaly-domain-header" style={{ "--accent": group.domain.accent } as CSSProperties}>
                <span className="domain-color" aria-hidden />
                <strong>{group.domain.label}</strong>
                <em>{group.items.length} items</em>
              </header>
              <div className="anomaly-stack">
                {group.items.map((item) => (
                  <AnomalyCard key={item.id} item={item} onOpen={() => onOpenItem(item)} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </section>
    </div>
  );
}

function ContextPanel({
  context,
  sourceCount,
  moduleCount,
  itemCount,
  openCount,
  severity,
}: {
  context: ActiveContext;
  sourceCount: number;
  moduleCount: number;
  itemCount: number;
  openCount: number;
  severity: Severity | "all";
}) {
  const label = context.level === "portfolio"
    ? "Vista portfolio"
    : context.level === "domain"
      ? "Vista de dominio"
      : "Vista de modulo";
  return (
    <section className={`context-panel ${context.level}`} aria-label="Contexto activo">
      <div>
        <p className="section-kicker">{label}</p>
        <h2>{context.title}</h2>
        <span>{context.subtitle}</span>
      </div>
      <dl>
        <div><dt>Modulos</dt><dd>{moduleCount}</dd></div>
        <div><dt>Fuentes</dt><dd>{sourceCount}</dd></div>
        <div><dt>Senales</dt><dd>{itemCount}</dd></div>
        <div><dt>Abiertas</dt><dd>{openCount}</dd></div>
      </dl>
      {severity !== "all" ? <em>Filtro activo: {severityLabels[severity]}</em> : null}
    </section>
  );
}

function ContextOperations({
  context,
  modules,
  sources,
  items,
  lessons,
  alerts,
  onOpenItem,
  onCartridge,
}: {
  context: ActiveContext;
  modules: Cartridge[];
  sources: SourceStatus[];
  items: ControlItem[];
  lessons: Lesson[];
  alerts: ControlAlert[];
  onOpenItem: (item: ControlItem) => void;
  onCartridge: (cartridge: string, domain: string) => void;
}) {
  const topItem = [...items].sort((left, right) => (
    (right.priority_score || 0) - (left.priority_score || 0)
    || right.severity_weight - left.severity_weight
  ))[0];
  const sourceRisk = sources.filter((source) => source.status !== "ok").length;
  return (
    <section className="context-ops" aria-label="Panel operativo contextual">
      <article className="context-ops-main">
        <p className="section-kicker">Centro operativo</p>
        <h2>{context.level === "portfolio" ? "Portfolio completo" : context.title}</h2>
        <p>
          {context.level === "portfolio"
            ? "Vista consolidada de todos los dominios activos, con fuentes reales y estados operativos."
            : "Vista exclusiva del contexto seleccionado: solo muestra modulos, fuentes, senales y aprendizaje relacionados."}
        </p>
        <div className="context-mini-grid">
          <InfoBlock label="Modulos en vista" value={`${modules.length}`} />
          <InfoBlock label="Fuentes con riesgo" value={`${sourceRisk}`} />
          <InfoBlock label="Alertas" value={`${alerts.length}`} />
          <InfoBlock label="Lecciones" value={`${lessons.length}`} />
        </div>
      </article>

      <article className="priority-card">
        <p className="section-kicker">Siguiente accion</p>
        {topItem ? (
          <>
            <span className={`severity-pill ${topItem.severity}`}>{severityLabels[topItem.severity]}</span>
            <h3>{topItem.title}</h3>
            <p>{topItem.description}</p>
            <button type="button" className="primary-action" onClick={() => onOpenItem(topItem)}>
              Investigar senal
              <ArrowRight aria-hidden />
            </button>
          </>
        ) : (
          <>
            <CheckCircle2 aria-hidden />
            <h3>Sin senales abiertas</h3>
            <p>El contexto no tiene anomalías/control items visibles con los datos actuales.</p>
          </>
        )}
      </article>

      <article className="module-rail" aria-label="Modulos del contexto">
        <p className="section-kicker">Modulos</p>
        <div>
          {modules.slice(0, 6).map((module) => (
            <button
              type="button"
              key={module.id}
              onClick={() => onCartridge(module.id, module.domain)}
              style={{ "--accent": module.accent } as CSSProperties}
            >
              <span className="sidebar-dot" aria-hidden />
              <strong>{module.label}</strong>
              <em>{module.item_count} senales</em>
            </button>
          ))}
        </div>
      </article>
    </section>
  );
}

function AlertQueuePanel({
  context,
  alerts,
  items,
  busyAction,
  actionError,
  actionMessage,
  onOperateAlert,
  onOpenItem,
}: {
  context: ActiveContext;
  alerts: ControlAlert[];
  items: ControlItem[];
  busyAction: string;
  actionError: string;
  actionMessage: string;
  onOperateAlert: (alert: ControlAlert, operation: "ack" | "snooze" | "assign" | "false-positive") => void;
  onOpenItem: (item: ControlItem) => void;
}) {
  const itemById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const topAlerts = alerts.slice(0, 6);
  const critical = alerts.filter((alert) => alert.severity === "critical").length;
  const pushReady = alerts.filter((alert) => alert.push_ready).length;
  return (
    <section className="alert-queue-panel" aria-label="Cola de alertas operativas">
      <div className="alert-queue-header">
        <div>
          <p className="section-kicker">Alertas y prioridad</p>
          <h2>{alerts.length} alertas activas para {context.title}</h2>
          <span>{pushReady} listas para ruteo · {critical} criticas · sin push externo en V1</span>
        </div>
        <div className="alert-route-pill">
          <Send aria-hidden />
          Push-ready
        </div>
      </div>
      {actionMessage ? <p className="alert-action-message" role="status">{actionMessage}</p> : null}
      {actionError ? <p className="alert-action-message error" role="alert">{actionError}</p> : null}
      {topAlerts.length ? (
        <div className="alert-grid">
          {topAlerts.map((alert) => {
            const item = itemById.get(alert.item_id);
            const busyPrefix = `alert:`;
            const snoozedUntil = alert.snoozed_until ? fmtDate(alert.snoozed_until) : "";
            return (
              <article className={`alert-card ${alert.severity}`} key={alert.id}>
                <div className="alert-card-top">
                  <span className={`severity-pill ${alert.severity}`}>{severityLabels[alert.severity]}</span>
                  <strong>Prioridad {alert.priority_score}</strong>
                </div>
                <span className={`alert-status-pill ${alert.status}`}>
                  {alertStatusLabels[alert.status] || alert.status}
                </span>
                <h3>{alert.title}</h3>
                <p>{alert.message}</p>
                <dl>
                  <div><dt>Tipo</dt><dd>{alertTypeLabels[alert.alert_type] || alert.alert_type}</dd></div>
                  <div><dt>Modulo</dt><dd>{alert.module}</dd></div>
                  <div><dt>Entrega</dt><dd>{alert.delivery?.status || "not_configured"}</dd></div>
                </dl>
                {alert.owner || snoozedUntil ? (
                  <p className="alert-meta-line">
                    {alert.owner ? `Owner: ${alert.owner}` : ""}
                    {alert.owner && snoozedUntil ? " · " : ""}
                    {snoozedUntil ? `Pospuesta hasta ${snoozedUntil}` : ""}
                  </p>
                ) : null}
                <div className="alert-driver-row">
                  {(alert.drivers || []).slice(0, 3).map((driver) => (
                    <span key={`${alert.id}-${driver.label}`}>
                      {driver.label}
                      {driver.points !== undefined ? ` +${driver.points}` : ""}
                    </span>
                  ))}
                </div>
                <div className="alert-card-actions">
                  <button
                    type="button"
                    className="tool-button"
                    disabled={!item || busyAction.startsWith(busyPrefix)}
                    onClick={() => item && onOpenItem(item)}
                  >
                    <Bell aria-hidden />
                    Abrir
                  </button>
                  <button
                    type="button"
                    className="tool-button"
                    disabled={busyAction !== "" || alert.status === "acknowledged"}
                    onClick={() => onOperateAlert(alert, "ack")}
                  >
                    {busyAction === `alert:ack:${alert.item_id}` ? <Loader2 aria-hidden className="spin" /> : <CheckCircle2 aria-hidden />}
                    Reconocer
                  </button>
                  <button
                    type="button"
                    className="tool-button"
                    disabled={busyAction !== "" || alert.status === "snoozed"}
                    onClick={() => onOperateAlert(alert, "snooze")}
                  >
                    {busyAction === `alert:snooze:${alert.item_id}` ? <Loader2 aria-hidden className="spin" /> : <Clock3 aria-hidden />}
                    Posponer 24h
                  </button>
                  <button
                    type="button"
                    className="tool-button"
                    disabled={busyAction !== ""}
                    onClick={() => onOperateAlert(alert, "assign")}
                  >
                    {busyAction === `alert:assign:${alert.item_id}` ? <Loader2 aria-hidden className="spin" /> : <UserPlus aria-hidden />}
                    Asignarme
                  </button>
                  <button
                    type="button"
                    className="tool-button danger"
                    disabled={busyAction !== ""}
                    onClick={() => onOperateAlert(alert, "false-positive")}
                  >
                    {busyAction === `alert:false-positive:${alert.item_id}` ? <Loader2 aria-hidden className="spin" /> : <XCircle aria-hidden />}
                    Falso positivo
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="state-panel">
          <CheckCircle2 aria-hidden />
          <span>Sin alertas activas para este contexto.</span>
        </div>
      )}
    </section>
  );
}

function ContextInsightPanel({
  context,
  modules,
  sources,
  items,
  financial,
}: {
  context: ActiveContext;
  modules: Cartridge[];
  sources: SourceStatus[];
  items: ControlItem[];
  financial?: FinancialSummary;
}) {
  const moneyContext = context.level === "portfolio"
    || context.domainLabel === "Finanzas"
    || context.domainLabel === "Compras"
    || context.domainLabel === "Ventas"
    || context.domainLabel === "Presupuestos"
    || context.moduleId === "replicon_finance";
  if (moneyContext) {
    return <FinancialPanel financial={financial} />;
  }
  const openItems = items.filter(activeOpen).length;
  const readySources = sources.filter((source) => source.status === "ok").length;
  const totalRows = sources.reduce((sum, source) => sum + Number(source.count || 0), 0);
  const maxModuleItems = Math.max(...modules.map((module) => module.item_count), 1);
  return (
    <section className="domain-insight-panel" aria-label="Lectura operativa del contexto">
      <div className="domain-insight-header">
        <div>
          <p className="section-kicker">Lectura operativa</p>
          <h2>{context.title}</h2>
          <span>{readySources}/{sources.length} fuentes operativas · {openItems} senales abiertas</span>
        </div>
        <div className="financial-margin">
          <span>Registros</span>
          <strong>{totalRows}</strong>
        </div>
      </div>
      <div className="module-bars">
        {modules.map((module) => (
          <div className="module-bar-row" key={module.id}>
            <span>{module.label}</span>
            <div aria-hidden>
              <em style={{ width: `${Math.max(4, Math.round((module.item_count / maxModuleItems) * 100))}%`, background: module.accent }} />
            </div>
            <strong>{module.item_count}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function SourceInventoryPanel({
  context,
  sources,
}: {
  context: ActiveContext;
  sources: SourceStatus[];
}) {
  const stateOrder: SourceState[] = ["ok", "empty", "missing", "invalid_schema", "unavailable", "blocked", "no_permission"];
  const counts = stateOrder.reduce((acc, state) => {
    acc[state] = sources.filter((source) => source.status === state).length;
    return acc;
  }, {} as Record<SourceState, number>);
  const sortedSources = [...sources].sort((left, right) => (
    stateOrder.indexOf(left.status) - stateOrder.indexOf(right.status)
    || left.module.localeCompare(right.module)
    || left.dataset.localeCompare(right.dataset)
  ));
  return (
    <section className="section-block" aria-label="Inventario de fuentes">
      <div className="anomaly-heading">
        <div>
          <p className="section-kicker">Fuentes del contexto</p>
          <h2>{context.title}</h2>
        </div>
        <span>{sources.length} datasets</span>
      </div>
      <div className="source-state-chips" aria-label="Estados de fuentes del contexto">
        {stateOrder.map((state) => (
          <span className={state} key={state}>
            {sourceStateLabels[state]} <strong>{counts[state]}</strong>
          </span>
        ))}
      </div>
      <div className="source-inventory">
        {sortedSources.length ? sortedSources.map((source) => (
          <article className={`source-row ${source.status}`} key={`${source.module_id}-${source.dataset}`}>
            <div>
              <span className={`source-dot ${source.status}`} aria-hidden />
              <strong>{source.dataset}</strong>
              <em>{source.module} · {source.cartridge}</em>
            </div>
            <span>{sourceStateLabels[source.status]}</span>
            <strong>{source.count} filas</strong>
            <small>{source.checked_at ? `Revisada ${timeAgo(parseDate(source.checked_at), 0)}` : "Sin revision"}</small>
            {source.error ? <p>{source.error}</p> : null}
          </article>
        )) : (
          <div className="state-panel">
            <AlertTriangle aria-hidden />
            <span>No hay fuentes visibles para este contexto.</span>
          </div>
        )}
      </div>
    </section>
  );
}

function ThresholdRulesBoard({
  context,
  thresholds,
  candidates,
  loading,
  error,
  saving,
  saveError,
  saveMessage,
  onSave,
}: {
  context: ActiveContext;
  thresholds: DetectionThreshold[];
  candidates: ThresholdCandidate[];
  loading: boolean;
  error: string;
  saving: boolean;
  saveError: string;
  saveMessage: string;
  onSave: (draft: ThresholdDraft) => void;
}) {
  const [selectedKey, setSelectedKey] = useState("");
  const [draftEdits, setDraftEdits] = useState<Record<string, ThresholdDraft>>({});
  const overridesByKey = useMemo(() => new Map(
    thresholds.map((threshold) => [thresholdKey(threshold), threshold]),
  ), [thresholds]);
  const effectiveSelectedKey = selectedKey && candidates.some((candidate) => candidate.key === selectedKey)
    ? selectedKey
    : candidates[0]?.key || "";
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
  const activeOverrides = thresholds.filter((threshold) => threshold.enabled !== false).length;
  const disabledOverrides = thresholds.length - activeOverrides;

  function updateDraft(patch: Partial<ThresholdDraft>) {
    if (!selected || !effectiveSelectedKey) return;
    setDraftEdits((current) => ({
      ...current,
      [effectiveSelectedKey]: { ...draft, ...patch },
    }));
  }

  return (
    <section className="section-block threshold-rules-board" aria-label="Umbrales configurables del contexto">
      <div className="anomaly-heading">
        <div>
          <p className="section-kicker">Umbrales configurables</p>
          <h2>{context.title}</h2>
        </div>
        <span>
          {activeOverrides} activos
          {disabledOverrides ? ` · ${disabledOverrides} inactivos` : ""}
        </span>
      </div>
      {error ? <p className="activity-error" role="alert">{error}</p> : null}
      <div className="threshold-workbench">
        <article className="threshold-editor" aria-label="Editor de umbral">
          <div>
            <label>
              Regla base
              <select
                aria-label="Regla de umbral"
                value={effectiveSelectedKey}
                onChange={(event) => setSelectedKey(event.target.value)}
                disabled={!candidates.length || saving}
              >
                {candidates.map((candidate) => (
                  <option value={candidate.key} key={candidate.key}>
                    {candidate.module} · {thresholdLabel(candidate)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Warning
              <input
                aria-label="Valor warning"
                type="number"
                step="0.01"
                value={draft.warning_value}
                onChange={(event) => updateDraft({ warning_value: event.target.value })}
                disabled={!selected || saving}
              />
            </label>
            <label>
              Critico
              <input
                aria-label="Valor critico"
                type="number"
                step="0.01"
                value={draft.critical_value}
                onChange={(event) => updateDraft({ critical_value: event.target.value })}
                disabled={!selected || saving}
              />
            </label>
            <label>
              Moneda
              <input
                aria-label="Moneda del umbral"
                value={draft.currency}
                maxLength={8}
                onChange={(event) => updateDraft({ currency: event.target.value.toUpperCase() })}
                disabled={!selected || saving}
              />
            </label>
          </div>
          <div className="threshold-editor-footer">
            <label className="threshold-toggle">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => updateDraft({ enabled: event.target.checked })}
                disabled={!selected || saving}
              />
              Activo para este workspace
            </label>
            <button
              type="button"
              className="primary-action"
              disabled={!selected || saving}
              onClick={() => onSave(draft)}
            >
              {saving ? <Loader2 aria-hidden className="spin" /> : <ShieldCheck aria-hidden />}
              Guardar umbral
            </button>
          </div>
          {selected ? (
            <p className="threshold-editor-help">
              {selectedOverride ? "Override workspace activo para esta regla." : "Esta regla usa default trazable; al guardar queda como override workspace auditado."}
            </p>
          ) : (
            <p className="threshold-editor-help">No hay reglas detectadas en este contexto todavia.</p>
          )}
          {saveError ? <p className="activity-error" role="alert">{saveError}</p> : null}
          {saveMessage ? <p className="activity-success" role="status">{saveMessage}</p> : null}
        </article>

        <article className="threshold-rule-list" aria-label="Reglas de umbral visibles">
          <div className="threshold-rule-header">
            <strong>Reglas del contexto</strong>
            <span>{loading ? "Actualizando" : `${candidates.length} reglas`}</span>
          </div>
          {candidates.length ? candidates.slice(0, 8).map((candidate) => {
            const override = overridesByKey.get(candidate.key);
            const source = override || candidate;
            return (
              <button
                type="button"
                key={candidate.key}
                className={`threshold-rule-card ${candidate.key === selected?.key ? "active" : ""}`}
                onClick={() => setSelectedKey(candidate.key)}
              >
                <span>{candidate.domain} · {candidate.module}</span>
                <strong>{thresholdLabel(candidate)}</strong>
                <em>
                  W {source.warning_value ?? "N/D"}
                  {source.critical_value !== null && source.critical_value !== undefined ? ` · C ${source.critical_value}` : ""}
                  {" · "}
                  {override ? "Workspace" : "Default"}
                </em>
                <small>{candidate.item_count} senales usando esta regla</small>
              </button>
            );
          }) : (
            <div className="state-panel">
              <ShieldCheck aria-hidden />
              <span>No hay umbrales trazables para este contexto.</span>
            </div>
          )}
        </article>
      </div>
    </section>
  );
}

function LessonsBoard({
  context,
  lessons,
  loading,
  error,
}: {
  context: ActiveContext;
  lessons: Lesson[];
  loading: boolean;
  error: string;
}) {
  return (
    <section className="section-block" aria-label="Lecciones aprendidas del contexto">
      <div className="anomaly-heading">
        <div>
          <p className="section-kicker">Lecciones aprendidas</p>
          <h2>{lessons.length} reglas visibles para {context.title}</h2>
        </div>
        <span>{loading ? "Actualizando" : "Memoria operativa"}</span>
      </div>
      {error ? <p className="activity-error" role="alert">{error}</p> : null}
      <div className="lessons-grid">
        {lessons.slice(0, 6).map((lesson) => (
          <article className="lesson-card" key={`${lesson.id || lesson.item_id}-${lesson.rule}`}>
            <div>
              <BookOpen aria-hidden />
              <span>{lesson.cartridge_id} · {lesson.anomaly_type}</span>
            </div>
            <strong>{lesson.rule}</strong>
            <dl>
              <div><dt>Decision</dt><dd>{lesson.source_decision_id ? `#${lesson.source_decision_id}` : "N/D"}</dd></div>
              <div><dt>Confianza</dt><dd>{Math.round((lesson.confidence || 0) * 100)}%</dd></div>
              <div><dt>Fecha</dt><dd>{fmtDate(lesson.created_at || "")}</dd></div>
            </dl>
          </article>
        ))}
        {!loading && !lessons.length ? (
          <div className="state-panel">
            <BookOpen aria-hidden />
            <span>Sin lecciones persistidas para este contexto todavia.</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  tone?: "neutral" | "critical" | "attention";
}) {
  return (
    <article className={`summary-card ${tone}`}>
      <span><Icon aria-hidden /></span>
      <p>{label}</p>
      <strong>{value}</strong>
    </article>
  );
}

function OmegaCycleBar({
  steps,
  activeStep,
  counts,
  onStep,
}: {
  steps: Array<{ id: string; label: string }>;
  activeStep?: string;
  counts?: Record<string, number>;
  onStep?: (stepId: string) => void;
}) {
  const cycleSteps = steps.length > 0 ? steps : defaultOmegaSteps;
  return (
    <section className="cycle-panel" aria-label="Ciclo OMEGA de 7 pasos">
      <p className="section-kicker">Ciclo OMEGA</p>
      <div className="omega-cycle">
        {cycleSteps.map((step, index) => {
          const active = activeStep === step.id;
          const label = step.id === "lessons" ? "Lecciones" : step.label;
          const content = (
            <>
              <span>{index + 1}</span>
              <strong title={step.label}>{label}</strong>
              {counts ? <em>{counts[step.id] ?? 0}</em> : null}
            </>
          );
          return onStep ? (
            <button
              type="button"
              className={active ? "active" : ""}
              aria-current={active ? "step" : undefined}
              onClick={() => onStep(step.id)}
              key={step.id}
            >
              {content}
            </button>
          ) : (
            <div className={active ? "active" : ""} key={step.id}>
              {content}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function SourceHealthPanel({
  sourceStates,
  totalSources,
}: {
  sourceStates?: Record<SourceState, number>;
  totalSources: number;
}) {
  const states: Array<{ key: SourceState; label: string; tone: string }> = [
    { key: "ok", label: "Operativas", tone: "ok" },
    { key: "empty", label: "Vacias", tone: "empty" },
    { key: "missing", label: "Faltantes", tone: "missing" },
    { key: "invalid_schema", label: "Invalidas", tone: "invalid" },
    { key: "unavailable", label: "No disponibles", tone: "unavailable" },
    { key: "blocked", label: "Bloqueadas", tone: "blocked" },
    { key: "no_permission", label: "Sin permiso", tone: "blocked" },
  ];
  const total = Math.max(
    totalSources,
    states.reduce((sum, state) => sum + (sourceStates?.[state.key] ?? 0), 0),
    1,
  );
  return (
    <section className="source-health-panel" aria-label="Salud de fuentes">
      <div className="source-health-title">
        <p className="section-kicker">Salud de fuentes</p>
        <strong>{totalSources} datasets</strong>
      </div>
      <div className="source-health-bars">
        {states.map((state) => {
          const count = sourceStates?.[state.key] ?? 0;
          const width = `${Math.max(2, Math.round((count / total) * 100))}%`;
          return (
            <div className="source-health-row" key={state.key}>
              <span>{state.label}</span>
              <div className="source-health-track" aria-hidden>
                <em className={state.tone} style={{ width }} />
              </div>
              <strong>{count}</strong>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ThresholdPanel({
  thresholds,
}: {
  thresholds?: Dashboard["summary"]["thresholds"];
}) {
  const active = thresholds?.active ?? 0;
  const affected = thresholds?.items_with_thresholds ?? 0;
  const byCartridge = Object.entries(thresholds?.by_cartridge || {}).slice(0, 3);
  return (
    <section className="threshold-panel" aria-label="Umbrales de deteccion">
      <div className="threshold-title">
        <p className="section-kicker">Umbrales</p>
        <strong>{active} activos</strong>
      </div>
      <div className="threshold-meter" aria-hidden>
        <em style={{ width: `${Math.min(100, Math.max(6, affected * 14))}%` }} />
      </div>
      <p>{affected} senales usando reglas configuradas o defaults trazables.</p>
      {byCartridge.length ? (
        <div className="threshold-tags">
          {byCartridge.map(([key, value]) => (
            <span key={key}>{key}: {value}</span>
          ))}
        </div>
      ) : (
        <span className="threshold-empty">Sin overrides por workspace</span>
      )}
    </section>
  );
}

function LearningPanel({
  lessons,
  contextLessons,
  loading,
}: {
  lessons?: Dashboard["summary"]["lessons"];
  contextLessons: Lesson[];
  loading: boolean;
}) {
  const total = lessons?.total ?? 0;
  const topPattern = lessons?.top_patterns?.[0];
  const recent = contextLessons[0] || lessons?.recent?.[0];
  return (
    <section className="learning-panel" aria-label="Lecciones aprendidas">
      <div className="learning-title">
        <p className="section-kicker">Aprendizaje</p>
        <strong><BookOpen aria-hidden /> {contextLessons.length || total}</strong>
      </div>
      <p>
        {loading
          ? "Actualizando memoria operativa..."
          : contextLessons.length
            ? `${contextLessons.length} lecciones visibles en este contexto`
            : topPattern
          ? `${topPattern.cartridge_id} · ${topPattern.anomaly_type} (${topPattern.count})`
          : "Sin lecciones persistidas todavia"}
      </p>
      {recent ? (
        <article>
          <span>Decision #{recent.source_decision_id || "N/D"}</span>
          <strong>{recent.rule}</strong>
        </article>
      ) : (
        <span className="learning-empty">Se llenara al aprobar recomendaciones</span>
      )}
    </section>
  );
}

function FinancialPanel({ financial }: { financial?: FinancialSummary }) {
  const status = financial?.status || "empty";
  const hasData = status === "ok";
  const bars = [
    { label: "Revenue", value: financial?.revenue_usd || financial?.sales_revenue || 0, tone: "ok" },
    { label: "Facturado", value: financial?.billed_usd || 0, tone: "blue" },
    { label: "WIP", value: Math.abs(financial?.wip_usd || 0), tone: (financial?.wip_usd || 0) >= 0 ? "attention" : "critical" },
    { label: "Backlog", value: financial?.backlog_value || 0, tone: "attention" },
    { label: "Compras", value: financial?.purchase_spend || 0, tone: "neutral" },
  ];
  const max = Math.max(...bars.map((bar) => bar.value), 1);
  const statusCopy: Record<string, string> = {
    ok: "Datos financieros materializados",
    empty: "Sin filas financieras para este workspace",
    missing: "Fuentes financieras no registradas",
    unavailable: "Fuentes financieras no disponibles",
    not_installed: "Cartuchos financieros no instalados",
  };

  return (
    <section className={`financial-panel ${hasData ? "ready" : "muted"}`} aria-label="Impacto financiero">
      <div className="financial-header">
        <div>
          <p className="section-kicker">Impacto financiero</p>
          <h2>{hasData ? fmtMoney(financial?.margin_usd) : "Sin lectura monetaria"}</h2>
          <span>{statusCopy[status] || "Estado financiero pendiente"}</span>
        </div>
        <div className="financial-margin">
          <span>Margen</span>
          <strong>{fmtPct(financial?.margin_pct)}</strong>
        </div>
      </div>

      <div className="money-grid">
        <MoneyCard label="Revenue P&L" value={fmtMoney(financial?.revenue_usd)} />
        <MoneyCard label="Facturacion" value={fmtMoney(financial?.billed_usd)} />
        <MoneyCard label="WIP" value={fmtMoney(financial?.wip_usd)} tone={(financial?.wip_usd || 0) < 0 ? "critical" : "attention"} />
        <MoneyCard label="Backlog ventas" value={fmtMoney(financial?.backlog_value)} sub={`${financial?.open_orders || 0} pedidos · ${financial?.oldest_backlog_days || 0} dias`} />
        <MoneyCard label="Compras" value={fmtMoney(financial?.purchase_spend)} />
      </div>

      <div className="money-bars">
        {bars.map((bar) => (
          <div className="money-bar-row" key={bar.label}>
            <span>{bar.label}</span>
            <div aria-hidden>
              <em className={bar.tone} style={{ width: `${Math.max(4, Math.round((bar.value / max) * 100))}%` }} />
            </div>
            <strong>{fmtMoney(bar.value)}</strong>
          </div>
        ))}
      </div>

      {financial?.risk_projects?.length ? (
        <div className="risk-strip">
          {financial.risk_projects.slice(0, 3).map((risk) => (
            <article key={`${risk.label}-${risk.owner}`}>
              <span>{risk.owner}</span>
              <strong>{risk.label}</strong>
              <p>Margen {fmtPct(risk.margin_pct)} · WIP {fmtMoney(risk.wip_usd)}</p>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function MoneyCard({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "attention" | "critical";
}) {
  return (
    <article className={`money-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {sub ? <em>{sub}</em> : null}
    </article>
  );
}

function DomainSection({ domain, collapsed, onToggle }: { domain: Domain; collapsed: boolean; onToggle: () => void }) {
  return (
    <article className="domain-section" style={{ "--accent": domain.accent } as CSSProperties}>
      <button type="button" className="domain-header" onClick={onToggle} aria-expanded={!collapsed}>
        <span className="domain-color" aria-hidden />
        <strong>{domain.label}</strong>
        <em>{domain.cartridge_count} modulos · {domain.item_count} items</em>
        <ChevronDown aria-hidden className={collapsed ? "collapsed" : ""} />
      </button>
      {!collapsed ? (
        <div className="kpi-grid">
          {domain.modules.map((module) => (
            <KpiTile key={`${domain.id}-${module.id}`} module={module} />
          ))}
        </div>
      ) : null}
    </article>
  );
}

function KpiTile({ module }: { module: DomainModule }) {
  return (
    <article className="kpi-tile">
      <div className="kpi-tile-label">
        <strong>{module.label}</strong>
        <span className={`source-dot ${module.source_status}`} aria-hidden />
      </div>
      {module.kpis.map((kpi) => (
        <div className={`kpi-row ${kpi.bad ? "bad" : ""}`} key={kpi.label}>
          <span>{kpi.label}</span>
          <strong>{kpi.value}</strong>
          {kpi.sql ? <SqlToggle sql={kpi.sql} compact /> : null}
        </div>
      ))}
    </article>
  );
}

function AnomalyCard({ item, onOpen }: { item: ControlItem; onOpen: () => void }) {
  return (
    <article className={`anomaly-card ${item.status}`}>
      <button
        type="button"
        className="anomaly-main"
        onClick={onOpen}
        aria-label={`Investigar ${item.title} ${item.entity_label}`}
      >
        <span className={`severity-rail ${item.severity}`} aria-hidden />
        <div>
          <div className="anomaly-top">
            <span className={`severity-pill ${item.severity}`}>{severityLabels[item.severity]}</span>
            {item.threshold_state && item.threshold_state !== "default" ? (
              <span className={`threshold-chip ${item.threshold_state}`}>Umbral {item.threshold_state}</span>
            ) : null}
            <span>{item.cartridge}</span>
            <span>{item.module}</span>
            <ChevronRight aria-hidden />
          </div>
          <h3>{item.title}</h3>
          <p>{item.description}</p>
          <div className="anomaly-meta">
            <span>{item.entity_label}</span>
            <span>Prioridad {item.priority_score ?? 0}/100</span>
            <span>{statusLabels[item.status] || item.status}</span>
          </div>
        </div>
      </button>
      {item.sql ? <SqlToggle sql={item.sql} /> : null}
    </article>
  );
}

function DetailPage({
  item,
  omegaSteps,
  mode,
  manualTab,
  busyAction,
  actionError,
  activity,
  activityLoading,
  activityError,
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
  onApplyLesson,
}: {
  item: ControlItem;
  omegaSteps: Array<{ id: string; label: string }>;
  mode: DetailMode;
  manualTab: number;
  busyAction: string;
  actionError: string;
  activity?: ActivityPayload;
  activityLoading: boolean;
  activityError: string;
  onBack: () => void;
  onMode: (mode: DetailMode) => void;
  onManualTab: (index: number) => void;
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
  onApplyLesson: (item: ControlItem, lesson: Lesson) => void;
}) {
  const activeStep = mode === null
    ? "signals"
    : mode === "auto"
      ? "execution"
      : manualStepIds[manualTab] || "investigation";

  function jumpToStep(stepId: string) {
    if (stepId === "signals") {
      onRecordStep(item, "signals", "Senal abierta desde el ciclo OMEGA", undefined, true);
      onMode(null);
      onManualTab(0);
      return;
    }
    onMode("manual");
    const tabByStep: Record<string, number> = {
      investigation: 0,
      options: 1,
      decision: 2,
      execution: 3,
      control: 4,
      lessons: 5,
    };
    onRecordStep(item, stepId, `Paso ${stepId} abierto desde el ciclo OMEGA`, undefined, true);
    onManualTab(tabByStep[stepId] ?? 0);
  }

  return (
    <section className="detail-page" aria-label="Detalle OMEGA">
      <button type="button" className="btn-back" onClick={onBack}>
        <ArrowLeft aria-hidden />
        Volver
      </button>

      <header className="detail-hero">
        <div className="detail-tags">
          <span className={`severity-pill ${item.severity}`}>{severityLabels[item.severity]}</span>
          <span>{item.domain}</span>
          <span>{item.cartridge}</span>
          <span>{fmtDate(item.detected_at)}</span>
        </div>
        <h2>{item.title}</h2>
        <p>{item.description}</p>
        <div className="detail-grid">
          <InfoBlock label="Entidad" value={`${item.entity_kind}: ${item.entity_label}`} />
          <InfoBlock label="Fuente" value={item.source_dataset} />
          <InfoBlock label="Estado" value={statusLabels[item.status] || item.status} />
          <InfoBlock label="Decision" value={item.decision_id ? `Decision #${item.decision_id}` : "Pendiente"} />
          <InfoBlock label="Impacto" value={item.impact_status === "ok" ? fmtMoney(item.impact_estimate) : "No calculable"} />
          <InfoBlock label="Prioridad" value={`${item.priority_score ?? 0}/100`} />
          <InfoBlock label="Umbral" value={item.threshold_state && item.threshold_state !== "default" ? item.threshold_state : "Default"} />
          <InfoBlock label="Lecciones" value={`${item.lesson_count ?? 0} relacionadas`} />
        </div>
      </header>

      <ImpactDetail item={item} />

      <OmegaCycleBar steps={omegaSteps} activeStep={activeStep} onStep={jumpToStep} />

      <ActivityTimeline
        activity={activity}
        loading={activityLoading}
        error={activityError}
      />

      {mode === null ? (
        <section className="mode-cards" aria-label="Seleccion de modo">
          <button type="button" className="mode-card auto" onClick={() => onMode("auto")}>
            <Activity aria-hidden />
            <strong>Modo automatico</strong>
            <span>OMEGA ejecuta el recorrido recomendado y deja trazabilidad.</span>
          </button>
          <button
            type="button"
            className="mode-card manual"
            onClick={() => {
              onMode("manual");
              onRecordStep(item, "investigation", "Modo manual iniciado", undefined, true);
            }}
          >
            <Layers3 aria-hidden />
            <strong>Modo manual</strong>
            <span>Revisa investigacion, opciones, ejecucion, control y reglas paso a paso.</span>
          </button>
        </section>
      ) : null}

      {mode === "auto" ? (
        <AutoFlow
          item={item}
          busyAction={busyAction}
          actionError={actionError}
          onRunAuto={onRunAuto}
          onCreateDecision={onCreateDecision}
          onPreview={onPreview}
          onDryRun={onDryRun}
          onApprove={onApprove}
          onDismiss={onDismiss}
        />
      ) : null}

      {mode === "manual" ? (
        <ManualFlow
          item={item}
          tab={manualTab}
          busyAction={busyAction}
          actionError={actionError}
          onTab={onManualTab}
          onCreateDecision={onCreateDecision}
          onSelectOption={onSelectOption}
          onRecordStep={onRecordStep}
          onUpdateControl={onUpdateControl}
          onPreview={onPreview}
          onDryRun={onDryRun}
          onExecute={onExecute}
          onApprove={onApprove}
          onDismiss={onDismiss}
          onCreateLesson={onCreateLesson}
          onApplyLesson={onApplyLesson}
        />
      ) : null}
    </section>
  );
}

function ImpactDetail({ item }: { item: ControlItem }) {
  const confidence = item.confidence ?? 0;
  const drivers = item.impact_drivers || [];
  const thresholds = item.thresholds_applied || [];
  return (
    <section className={`impact-detail ${item.impact_status === "ok" ? "ready" : "muted"}`} aria-label="Impacto economico del item">
      <div className="impact-main">
        <CircleDollarSign aria-hidden />
        <div>
          <p className="section-kicker">Impacto economico V1</p>
          <h3>{item.impact_status === "ok" ? fmtMoney(item.impact_estimate) : "No calculable"}</h3>
          <span>{item.impact_explanation || "Sin cost basis suficiente para convertir esta senal a dinero."}</span>
        </div>
      </div>
      <div className="impact-metrics">
        <InfoBlock label="Confianza" value={`${Math.round(confidence * 100)}%`} />
        <InfoBlock label="Prioridad" value={`${item.priority_score ?? 0}/100`} />
        <InfoBlock label="Formula" value={item.impact_formula || "N/D"} />
      </div>
      {drivers.length ? (
        <div className="impact-drivers">
          {drivers.map((driver) => (
            <article key={driver.label}>
              <span>{driver.label}</span>
              <strong>
                {driver.currency ? fmtMoney(Number(driver.value || 0)) : driver.value ?? "N/D"}
                {driver.unit ? ` ${driver.unit}` : ""}
              </strong>
            </article>
          ))}
        </div>
      ) : null}
      {thresholds.length ? (
        <div className="threshold-detail">
          {thresholds.map((threshold) => (
            <article key={`${threshold.anomaly_type}-${threshold.metric}`}>
              <span>{threshold.metric}</span>
              <strong>
                W {threshold.warning_value ?? "N/D"}
                {threshold.critical_value !== null && threshold.critical_value !== undefined ? ` · C ${threshold.critical_value}` : ""}
              </strong>
              <em>{threshold.source === "workspace" ? "Workspace" : "Default"} · {threshold.currency || "USD"}</em>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ActivityTimeline({
  activity,
  loading,
  error,
}: {
  activity?: ActivityPayload;
  loading: boolean;
  error: string;
}) {
  const entries = activity?.activity ?? [];
  return (
    <section className="activity-timeline" aria-label="Bitacora operativa">
      <header>
        <div>
          <Activity aria-hidden />
          <div>
            <p className="section-kicker">Bitacora operativa</p>
            <h3>Acciones separadas y auditadas</h3>
          </div>
        </div>
        <span>{loading ? "Cargando" : `${activity?.counts.total ?? 0} eventos`}</span>
      </header>
      {error ? <p className="activity-error" role="alert">{error}</p> : null}
      {!loading && !entries.length ? (
        <p className="activity-empty">Aun no hay acciones registradas para esta senal.</p>
      ) : null}
      <div className="activity-list">
        {entries.slice(0, 8).map((entry) => (
          <article className={`activity-entry ${entry.kind}`} key={entry.id}>
            <span className="activity-marker" aria-hidden />
            <div>
              <strong>{entry.label}</strong>
              <p>{activityDescription(entry)}</p>
              <em>{fmtDate(entry.at || "")} · {entry.actor || "sistema"}</em>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function AutoFlow({
  item,
  busyAction,
  actionError,
  onRunAuto,
  onCreateDecision,
  onPreview,
  onDryRun,
  onApprove,
  onDismiss,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onRunAuto: (item: ControlItem) => void;
  onCreateDecision: (item: ControlItem) => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
}) {
  const selectedOption = item.omega.options.find((option) => option.selected) || item.omega.options[0];
  const steps = [
    { title: "Senales", text: `${item.source_dataset} · ${item.severity}`, done: true },
    { title: "Investigacion", text: item.omega.investigation.root_cause || item.root_cause || "Pendiente", done: true },
    { title: "Opciones", text: selectedOption ? `${selectedOption.action || selectedOption.label} · score ${selectedOption.score}` : "Sin opcion", done: Boolean(selectedOption) },
    { title: "Decision", text: item.decision_id ? `Decision #${item.decision_id}` : "Lista para crear decision", done: Boolean(item.decision_id) },
    { title: "Ejecucion", text: item.execution_status === "dry_run_validated" ? "Dry-run validado sin write-back" : "Pendiente de validacion segura", done: item.execution_status === "dry_run_validated" },
    { title: "Control", text: item.omega.control.status || "Seguimiento abierto", done: terminalStatuses.has(item.status) },
    { title: "Lecciones", text: item.omega.lessons.rules[0] || "Regla pendiente", done: (item.lesson_count || 0) > 0 || terminalStatuses.has(item.status) },
  ];
  return (
    <section className="timeline">
      {steps.map((step, index) => (
        <article className={`timeline-step ${step.done ? "done" : ""}`} key={step.title}>
          <span>{index + 1}</span>
          <div>
            <strong>{step.title}</strong>
            <p>{step.text}</p>
          </div>
        </article>
      ))}
      <button
        type="button"
        className="auto-run-button"
        onClick={() => onRunAuto(item)}
        disabled={terminalStatuses.has(item.status) || busyAction !== ""}
      >
        {busyAction === `auto:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <Play aria-hidden />}
        Ejecutar modo automatico seguro
      </button>
      <ActionStrip
        item={item}
        busyAction={busyAction}
        actionError={actionError}
        onCreateDecision={onCreateDecision}
        onPreview={onPreview}
        onDryRun={onDryRun}
        onApprove={onApprove}
        onDismiss={onDismiss}
      />
    </section>
  );
}

function ManualFlow({
  item,
  tab,
  busyAction,
  actionError,
  onTab,
  onCreateDecision,
  onSelectOption,
  onRecordStep,
  onUpdateControl,
  onPreview,
  onDryRun,
  onExecute,
  onApprove,
  onDismiss,
  onCreateLesson,
  onApplyLesson,
}: {
  item: ControlItem;
  tab: number;
  busyAction: string;
  actionError: string;
  onTab: (index: number) => void;
  onCreateDecision: (item: ControlItem) => void;
  onSelectOption: (item: ControlItem, optionId: string) => void;
  onRecordStep: (item: ControlItem, stepId: string, note?: string, controlId?: string, silent?: boolean) => void;
  onUpdateControl: (item: ControlItem, control: ControlChecklistItem, status: "in_progress" | "closed" | "blocked") => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
  onCreateLesson: (item: ControlItem, rule: string) => void;
  onApplyLesson: (item: ControlItem, lesson: Lesson) => void;
}) {
  function selectTab(index: number) {
    onTab(index);
    onRecordStep(item, manualStepIds[index] || "investigation", `Paso ${manualTabs[index]} abierto`, undefined, true);
  }

  return (
    <section className="manual-shell">
      <div className="tab-bar" role="tablist" aria-label="Pasos manuales">
        {manualTabs.map((label, index) => (
          <button
            type="button"
            role="tab"
            aria-selected={tab === index}
            className={tab === index ? "active" : ""}
            key={label}
            onClick={() => selectTab(index)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="panel">
        {tab === 0 ? (
          <InvestigationPanel
            item={item}
            busyAction={busyAction}
            actionError={actionError}
            onRecordStep={onRecordStep}
          />
        ) : null}
        {tab === 1 ? (
          <OptionsPanel
            item={item}
            busyAction={busyAction}
            onSelectOption={onSelectOption}
          />
        ) : null}
        {tab === 2 ? (
          <DecisionPanel
            item={item}
            busyAction={busyAction}
            actionError={actionError}
            onCreateDecision={onCreateDecision}
            onRecordStep={onRecordStep}
            onDismiss={onDismiss}
          />
        ) : null}
        {tab === 3 ? (
          <ExecutionPanel
            item={item}
            busyAction={busyAction}
            actionError={actionError}
            onPreview={onPreview}
            onDryRun={onDryRun}
            onExecute={onExecute}
            onApprove={onApprove}
          />
        ) : null}
        {tab === 4 ? (
          <ControlPanel
            item={item}
            busyAction={busyAction}
            actionError={actionError}
            onUpdateControl={onUpdateControl}
          />
        ) : null}
        {tab === 5 ? (
          <RulesPanel
            item={item}
            busyAction={busyAction}
            actionError={actionError}
            onCreateLesson={onCreateLesson}
            onApplyLesson={onApplyLesson}
          />
        ) : null}
      </div>

      <div className="bottom-nav">
        <button type="button" onClick={() => selectTab(Math.max(0, tab - 1))} disabled={tab === 0}>
          Anterior
        </button>
        <div>
          {manualTabs.map((label, index) => (
            <span className={index === tab ? "active" : ""} key={label} />
          ))}
        </div>
        <button type="button" onClick={() => selectTab(Math.min(manualTabs.length - 1, tab + 1))} disabled={tab === manualTabs.length - 1}>
          Siguiente
        </button>
      </div>
    </section>
  );
}

function InvestigationPanel({
  item,
  busyAction,
  actionError,
  onRecordStep,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onRecordStep: (item: ControlItem, stepId: string, note?: string, controlId?: string, silent?: boolean) => void;
}) {
  return (
    <>
      <div className="panel-grid">
        <PanelRow label="Causa probable" value={item.omega.investigation.root_cause || item.root_cause || "Pendiente"} />
        <PanelRow label="Impacto" value={item.omega.investigation.impact || item.impact || "Riesgo operativo"} />
        <PanelRow label="Recomendacion" value={item.recommendation} />
        {item.sql ? <SqlToggle sql={item.sql} /> : null}
      </div>
      <div className="step-action-footer">
        <button
          type="button"
          className="primary-action"
          onClick={() => onRecordStep(item, "investigation", "Investigacion revisada por usuario")}
          disabled={busyAction !== ""}
        >
          {busyAction === `step:${item.id}:investigation:` ? <Loader2 aria-hidden className="spin" /> : <CheckCircle2 aria-hidden />}
          Registrar investigacion revisada
        </button>
        {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
      </div>
    </>
  );
}

function OptionsPanel({
  item,
  busyAction,
  onSelectOption,
}: {
  item: ControlItem;
  busyAction: string;
  onSelectOption: (item: ControlItem, optionId: string) => void;
}) {
  const locked = terminalStatuses.has(item.status);
  return (
    <div className="option-grid">
      {item.omega.options.map((option) => (
        <button
          type="button"
          className={`option-card ${option.selected ? "selected" : ""}`}
          key={option.id}
          onClick={() => onSelectOption(item, option.id)}
          disabled={locked || busyAction !== ""}
          aria-pressed={option.selected}
        >
          <div>
            <strong>{option.action || option.label}</strong>
            <span>{option.selected ? "Seleccionada" : option.auto ? "Auto" : "Manual"}</span>
          </div>
          <p>{option.recommendation}</p>
          <dl>
            <div><dt>Impacto</dt><dd>{option.money || "N/D"}</dd></div>
            <div><dt>Tiempo</dt><dd>{option.time || "N/D"}</dd></div>
            <div><dt>Riesgo</dt><dd>{option.risk}</dd></div>
            <div><dt>Score</dt><dd>{option.score}</dd></div>
          </dl>
          {busyAction === `option:${item.id}:${option.id}` ? (
            <span className="option-saving"><Loader2 aria-hidden className="spin" /> Guardando</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

function DecisionPanel({
  item,
  busyAction,
  actionError,
  onCreateDecision,
  onRecordStep,
  onDismiss,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onCreateDecision: (item: ControlItem) => void;
  onRecordStep: (item: ControlItem, stepId: string, note?: string, controlId?: string, silent?: boolean) => void;
  onDismiss: (item: ControlItem) => void;
}) {
  const selectedOption = item.omega.options.find((option) => option.selected);
  const decisionReady = Boolean(item.decision_id);
  return (
    <div className="decision-panel">
      <article className="decision-card">
        <p className="section-kicker">Decision OMEGA</p>
        <h3>{decisionReady ? `Decision #${item.decision_id}` : "Decision pendiente"}</h3>
        <p>
          {selectedOption
            ? `${selectedOption.action || selectedOption.label}: ${selectedOption.recommendation}`
            : "Selecciona una opcion antes de crear la decision."}
        </p>
        <dl>
          <div><dt>Estado</dt><dd>{statusLabels[item.status] || item.status}</dd></div>
          <div><dt>Opcion</dt><dd>{selectedOption?.label || "Sin seleccionar"}</dd></div>
          <div><dt>Prioridad</dt><dd>{item.priority_score ?? 0}/100</dd></div>
          <div><dt>Impacto</dt><dd>{item.impact_status === "ok" ? fmtMoney(item.impact_estimate) : "No calculable"}</dd></div>
        </dl>
      </article>
      <div className="decision-actions">
        <button
          type="button"
          className="primary-action"
          onClick={() => onCreateDecision(item)}
          disabled={decisionReady || busyAction !== ""}
        >
          {busyAction === `decision:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <FileCheck2 aria-hidden />}
          {decisionReady ? `Decision #${item.decision_id}` : "Crear decision"}
        </button>
        <button
          type="button"
          className="secondary-action"
          onClick={() => onRecordStep(item, "decision", "Decision revisada sin cambio")}
          disabled={busyAction !== ""}
        >
          {busyAction === `step:${item.id}:decision:` ? <Loader2 aria-hidden className="spin" /> : <CheckCircle2 aria-hidden />}
          Registrar revision
        </button>
        <button
          type="button"
          className="ghost-action"
          onClick={() => onDismiss(item)}
          disabled={terminalStatuses.has(item.status) || busyAction !== ""}
        >
          {busyAction === `dismiss:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <XCircle aria-hidden />}
          Descartar
        </button>
        {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
      </div>
    </div>
  );
}

function ExecutionPanel({
  item,
  busyAction,
  actionError,
  onPreview,
  onDryRun,
  onExecute,
  onApprove,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
}) {
  return (
    <div className="execution-list">
      <ExecutionBridge
        item={item}
        busyAction={busyAction}
        onPreview={onPreview}
        onDryRun={onDryRun}
        onExecute={onExecute}
      />
      {item.omega.execution.actions.map((action) => (
        <article className="action-row" key={action.id}>
          <div>
            <span>{action.sys || item.cartridge}</span>
            <strong>{action.act || action.label}</strong>
            <p>{action.auto ? "Automatizable" : "Requiere aprobacion humana"} · {action.done || action.approved ? "listo" : "pendiente"}</p>
          </div>
          {action.id === "owner_review" ? (
            <button
              type="button"
              className="primary-action"
              onClick={() => onApprove(item)}
              disabled={item.status === "approved" || busyAction !== ""}
            >
              {busyAction === `approve:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <ClipboardCheck aria-hidden />}
              {item.status === "approved" ? "Accion aprobada" : "Aprobar accion"}
            </button>
          ) : (
            <span className={`action-state-pill ${action.done || action.approved ? "done" : ""}`}>
              {action.done || action.approved ? "Listo" : "Pendiente"}
            </span>
          )}
        </article>
      ))}
      <div className="step-action-footer">
        <button
          type="button"
          className="primary-action"
          onClick={() => onApprove(item)}
          disabled={item.status === "approved" || busyAction !== ""}
        >
          {busyAction === `approve:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <ClipboardCheck aria-hidden />}
          {item.status === "approved" ? "Recomendacion aprobada" : "Aprobar recomendacion"}
        </button>
        {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
      </div>
    </div>
  );
}

function ExecutionBridge({
  item,
  busyAction,
  onPreview,
  onDryRun,
  onExecute,
}: {
  item: ControlItem;
  busyAction: string;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
}) {
  const template = item.action_templates?.[0];
  return (
    <article className="execution-bridge">
      <div>
        <p className="section-kicker">Execution bridge V1</p>
        <h3>{template?.label || "Accion segura"}</h3>
        <span>{template?.description || "Preview y dry-run auditados antes de cualquier escritura externa."}</span>
      </div>
      <div className="execution-state">
        <strong>{item.execution_status || "not_started"}</strong>
        <em>Write-back productivo bloqueado</em>
      </div>
      <div className="execution-buttons">
        <button type="button" className="secondary-action" onClick={() => onPreview(item)} disabled={busyAction !== ""}>
          {busyAction === `preview:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <FileCheck2 aria-hidden />}
          Preview
        </button>
        <button type="button" className="primary-action" onClick={() => onDryRun(item)} disabled={busyAction !== ""}>
          {busyAction === `dryrun:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <ShieldCheck aria-hidden />}
          Dry-run
        </button>
        <button type="button" className="ghost-action" onClick={() => onExecute(item)} disabled={busyAction !== ""}>
          {busyAction === `execute:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <Play aria-hidden />}
          Ejecutar
        </button>
      </div>
    </article>
  );
}

function ControlPanel({
  item,
  busyAction,
  actionError,
  onUpdateControl,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onUpdateControl: (item: ControlItem, control: ControlChecklistItem, status: "in_progress" | "closed" | "blocked") => void;
}) {
  const controls = item.omega.control.items || [];
  return (
    <div className="control-list">
      {controls.map((control) => (
        <article className={`control-row ${control.status || ""}`} key={control.id}>
          <div className="control-row-header">
            <strong>{control.desc}</strong>
            <span className={`control-status-pill ${control.status || "open"}`}>{control.st}</span>
          </div>
          <dl>
            <div><dt>Owner</dt><dd>{control.owner}</dd></div>
            <div><dt>Estado</dt><dd>{control.st}</dd></div>
            <div><dt>Impacto</dt><dd>{control.impact}</dd></div>
            <div><dt>Vence</dt><dd>{control.due_at ? fmtDate(control.due_at) : `${control.days} dias`}</dd></div>
          </dl>
          {control.note ? <p className="control-note">{control.note}</p> : null}
          <div className="control-actions">
            <button
              type="button"
              className="secondary-action"
              onClick={() => onUpdateControl(item, control, "in_progress")}
              disabled={busyAction !== "" || control.status === "in_progress"}
            >
              {busyAction === `control:${item.id}:${control.id}:in_progress` ? <Loader2 aria-hidden className="spin" /> : <Activity aria-hidden />}
              Tomar seguimiento
            </button>
            <button
              type="button"
              className="primary-action"
              onClick={() => onUpdateControl(item, control, "closed")}
              disabled={busyAction !== ""}
            >
              {busyAction === `control:${item.id}:${control.id}:closed` ? <Loader2 aria-hidden className="spin" /> : <ClipboardCheck aria-hidden />}
              Confirmar control
            </button>
            <button
              type="button"
              className="ghost-action"
              onClick={() => onUpdateControl(item, control, "blocked")}
              disabled={busyAction !== "" || control.status === "blocked"}
            >
              {busyAction === `control:${item.id}:${control.id}:blocked` ? <Loader2 aria-hidden className="spin" /> : <XCircle aria-hidden />}
              Bloquear
            </button>
          </div>
        </article>
      ))}
      {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
    </div>
  );
}

function RulesPanel({
  item,
  busyAction,
  actionError,
  onCreateLesson,
  onApplyLesson,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onCreateLesson: (item: ControlItem, rule: string) => void;
  onApplyLesson: (item: ControlItem, lesson: Lesson) => void;
}) {
  const related = item.related_lessons || [];
  const applied = item.lesson_applications || item.omega.lessons.applied || [];
  const appliedIds = new Set(applied.map((entry) => Number(entry.lesson_id || 0)).filter(Boolean));
  const [draftRule, setDraftRule] = useState(item.omega.lessons.rules[0] || "");
  return (
    <div className="rules-list">
      {related.length ? (
        <section className="learned-history" aria-label="Historial de aprendizaje">
          <div>
            <span>{related.length} persistidas</span>
            <strong>Patron aprendido para {item.cartridge}</strong>
          </div>
          {related.slice(0, 5).map((lesson) => {
            const isApplied = Boolean(lesson.id && appliedIds.has(lesson.id));
            return (
            <article key={`${lesson.id || lesson.item_id}-${lesson.rule}`}>
              <span>Decision #{lesson.source_decision_id || "N/D"} · confianza {Math.round((lesson.confidence || 0) * 100)}%</span>
              <p>{lesson.rule}</p>
              {lesson.id ? (
                <button
                  type="button"
                  className={isApplied ? "secondary-action compact-action applied" : "primary-action compact-action"}
                  onClick={() => onApplyLesson(item, lesson)}
                  disabled={busyAction !== "" || isApplied}
                >
                  {busyAction === `applyLesson:${item.id}:${lesson.id}` ? (
                    <Loader2 aria-hidden className="spin" />
                  ) : isApplied ? (
                    <CheckCircle2 aria-hidden />
                  ) : (
                    <BookOpen aria-hidden />
                  )}
                  {isApplied ? "Leccion aplicada" : "Aplicar leccion"}
                </button>
              ) : null}
            </article>
            );
          })}
        </section>
      ) : null}
      {applied.length ? (
        <section className="applied-lessons" aria-label="Lecciones aplicadas">
          <div>
            <span>{applied.length} aplicadas</span>
            <strong>Feedback loop activo en esta senal</strong>
          </div>
          {applied.slice(0, 5).map((application) => (
            <article key={`${application.lesson_id || application.rule}-${application.applied_at || ""}`}>
              <CheckCircle2 aria-hidden />
              <div>
                <strong>{application.rule}</strong>
                <span>
                  {application.applied_at ? fmtDate(application.applied_at) : "Fecha N/D"}
                  {application.applied_by ? ` · ${application.applied_by}` : ""}
                </span>
                {application.note ? <p>{application.note}</p> : null}
              </div>
            </article>
          ))}
        </section>
      ) : null}
      {item.omega.lessons.rules.map((rule) => (
        <PanelRow key={rule} label="Regla aprendida" value={rule} />
      ))}
      <form
        className="lesson-form"
        onSubmit={(event) => {
          event.preventDefault();
          const rule = draftRule.trim();
          if (rule) onCreateLesson(item, rule);
        }}
      >
        <label htmlFor={`lesson-${item.id}`}>Nueva leccion persistida</label>
        <textarea
          id={`lesson-${item.id}`}
          value={draftRule}
          onChange={(event) => setDraftRule(event.target.value)}
          rows={3}
          placeholder="Ej. Si esta senal reaparece, validar owner y evidencia antes de aprobar."
        />
        <button
          type="submit"
          className="primary-action"
          disabled={busyAction !== "" || draftRule.trim().length < 8}
        >
          {busyAction === `lesson:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <BookOpen aria-hidden />}
          Guardar leccion
        </button>
        {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
      </form>
    </div>
  );
}

function ActionStrip({
  item,
  busyAction,
  actionError,
  onCreateDecision,
  onPreview,
  onDryRun,
  onApprove,
  onDismiss,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onCreateDecision: (item: ControlItem) => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
}) {
  const approved = item.status === "approved";
  return (
    <div className="actions-row">
      <button
        type="button"
        className="secondary-action"
        onClick={() => onCreateDecision(item)}
        disabled={Boolean(item.decision_id) || busyAction !== ""}
      >
        {busyAction === `decision:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <FileCheck2 aria-hidden />}
        {item.decision_id ? `Decision #${item.decision_id}` : "Crear decision"}
      </button>
      <button
        type="button"
        className="secondary-action"
        onClick={() => onPreview(item)}
        disabled={busyAction !== ""}
      >
        {busyAction === `preview:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <FileCheck2 aria-hidden />}
        Preview
      </button>
      <button
        type="button"
        className="secondary-action"
        onClick={() => onDryRun(item)}
        disabled={busyAction !== ""}
      >
        {busyAction === `dryrun:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <ShieldCheck aria-hidden />}
        Dry-run
      </button>
      <button
        type="button"
        className="primary-action"
        onClick={() => onApprove(item)}
        disabled={approved || busyAction !== ""}
      >
        {busyAction === `approve:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <ClipboardCheck aria-hidden />}
        {approved ? "Recomendacion aprobada" : "Aprobar recomendacion"}
        <ArrowRight aria-hidden />
      </button>
      <button
        type="button"
        className="ghost-action"
        onClick={() => onDismiss(item)}
        disabled={terminalStatuses.has(item.status) || busyAction !== ""}
      >
        {busyAction === `dismiss:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <XCircle aria-hidden />}
        Descartar
      </button>
      {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
    </div>
  );
}

function SqlToggle({ sql, compact = false }: { sql: string; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`sql-toggle ${compact ? "compact" : ""}`}>
      <button type="button" onClick={() => setOpen((current) => !current)}>
        {open ? "Ocultar fuente tecnica" : "Ver fuente tecnica"}
      </button>
      {open ? <pre>{sql}</pre> : null}
    </div>
  );
}

function PanelRow({ label, value }: { label: string; value: string }) {
  return (
    <article className="panel-row">
      <span>{label}</span>
      <p>{value || "N/D"}</p>
    </article>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-block">
      <span>{label}</span>
      <strong>{value || "N/D"}</strong>
    </div>
  );
}
