"use client";

import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  CircleDollarSign,
  ClipboardCheck,
  FileCheck2,
  Filter,
  Gauge,
  Layers3,
  Loader2,
  Play,
  RefreshCcw,
  ShieldCheck,
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
  cartridge_id: string;
  anomaly_type: string;
  metric: string;
  warning_value?: number | null;
  critical_value?: number | null;
  currency?: string;
  source?: "workspace" | "default" | string;
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

interface ControlChecklistItem {
  id: string;
  desc: string;
  owner: string;
  st: string;
  impact: string;
  days: number;
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
  impact_drivers?: ImpactDriver[];
  impact_formula?: string;
  impact_explanation?: string;
  thresholds_applied?: DetectionThreshold[];
  threshold_state?: "critical" | "warning" | "default" | string;
  related_lessons?: Lesson[];
  lesson_count?: number;
  action_templates?: ActionTemplate[];
  omega: Omega;
}

interface Dashboard {
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
  };
  domains: Domain[];
  cartridges: Cartridge[];
  sources: SourceStatus[];
  items: ControlItem[];
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

const manualTabs = ["Investigacion", "Opciones", "Ejecucion", "Control", "Reglas"];
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

  const load = useCallback(async (preferredId?: string) => {
    setError("");
    setState((current) => (current === "ready" ? current : "loading"));
    try {
      const nextDashboard = await apiJson<Dashboard>("/api/control-room/dashboard");
      setDashboard(nextDashboard);
      setSelectedId((current) => preferredId || current || nextDashboard.items[0]?.id || "");
      setState("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar la sala de control");
      setState("error");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function hydrate() {
      setError("");
      try {
        const nextDashboard = await apiJson<Dashboard>("/api/control-room/dashboard");
        if (cancelled) return;
        setDashboard(nextDashboard);
        setSelectedId((current) => current || nextDashboard.items[0]?.id || "");
        setState("ready");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "No se pudo cargar la sala de control");
        setState("error");
      }
    }
    void hydrate();
    return () => {
      cancelled = true;
    };
  }, []);

  const cartridges = useMemo(() => dashboard?.cartridges ?? [], [dashboard]);
  const domains = useMemo(() => dashboard?.domains ?? [], [dashboard]);
  const items = useMemo(() => dashboard?.items ?? [], [dashboard]);
  const activeCartridges = cartridges.filter((item) => item.active && !item.operational);

  const filtered = useMemo(() => items.filter((item) => (
    (domain === "all" || item.domain === domain)
    && (severity === "all" || item.severity === severity)
    && (cartridge === "all" || (item.module_id || item.cartridge) === cartridge)
  )), [cartridge, domain, items, severity]);

  const groupedItems = useMemo(() => domains.map((group) => ({
    domain: group,
    items: filtered.filter((item) => item.domain === group.label),
  })).filter((group) => group.items.length > 0), [domains, filtered]);

  const selected = filtered.find((item) => item.id === selectedId)
    || items.find((item) => item.id === selectedId)
    || filtered[0]
    || null;

  function openItem(item: ControlItem) {
    setSelectedId(item.id);
    setDetailOpen(true);
    setDetailMode(null);
    setManualTab(0);
    setActionError("");
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

  async function createDecision(item: ControlItem) {
    setBusyAction(`decision:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ decision: { id: number }; item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/decision`,
        { method: "POST", body: JSON.stringify({}) },
      );
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
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
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
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
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
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
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
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
      let working = item;
      if (!terminalStatuses.has(working.status) && working.selected_option_id !== "remediate") {
        const selectedPayload = await apiJson<{ item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(working.id)}/option`,
          { method: "POST", body: JSON.stringify({ option_id: "remediate" }) },
        );
        working = selectedPayload.item;
        mergeDashboardItem(working);
      }
      if (!working.decision_id) {
        const decisionPayload = await apiJson<{ decision: { id: number }; item: ControlItem }>(
          `/api/control-room/items/${encodeURIComponent(working.id)}/decision`,
          { method: "POST", body: JSON.stringify({}) },
        );
        working = decisionPayload.item;
        mergeDashboardItem(working);
      }
      const previewPayload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(working.id)}/action-preview`,
        { method: "POST", body: JSON.stringify({}) },
      );
      working = previewPayload.item;
      mergeDashboardItem(working);
      const dryRunPayload = await apiJson<{ item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(working.id)}/action-dry-run`,
        { method: "POST", body: JSON.stringify({}) },
      );
      mergeDashboardItem(dryRunPayload.item);
      void load(dryRunPayload.item.id);
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
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
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
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
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
      mergeDashboardItem(payload.item);
      void load(payload.item.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo descartar el item");
    } finally {
      setBusyAction("");
    }
  }

  return (
    <main className="app-shell">
      <Sidebar
        domains={domains}
        cartridges={cartridges}
        totalItems={dashboard?.summary.total_items ?? 0}
        activeCartridges={activeCartridges.length}
        domain={domain}
        cartridge={cartridge}
        onAll={() => {
          setDomain("all");
          setCartridge("all");
          setDetailOpen(false);
        }}
        onDomain={(nextDomain) => {
          setDomain(nextDomain);
          setCartridge("all");
          setDetailOpen(false);
        }}
        onCartridge={(nextCartridge, nextDomain) => {
          setDomain(nextDomain);
          setCartridge((current) => (current === nextCartridge ? "all" : nextCartridge));
          setDetailOpen(false);
        }}
      />

      <section className="main-surface">
        <Header
          period={dashboard?.period || "Periodo operativo"}
          activeCartridges={activeCartridges.length}
          loading={state === "loading"}
          onRefresh={() => void load()}
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
            onBack={() => {
              setDetailOpen(false);
              setDetailMode(null);
              setManualTab(0);
            }}
            onMode={setDetailMode}
            onManualTab={setManualTab}
            onCreateDecision={createDecision}
            onSelectOption={selectOption}
            onPreview={previewAction}
            onDryRun={dryRunAction}
            onExecute={executeLive}
            onRunAuto={runAuto}
            onApprove={approve}
            onDismiss={dismiss}
          />
        ) : (
          <DashboardView
            dashboard={dashboard}
            state={state}
            domains={domains}
            domain={domain}
            severity={severity}
            groupedItems={groupedItems}
            filteredCount={filtered.length}
            openCount={filtered.filter(activeOpen).length}
            collapsed={collapsed}
            onDomain={setDomain}
            onSeverity={setSeverity}
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
  activeCartridges,
  domain,
  cartridge,
  onAll,
  onDomain,
  onCartridge,
}: {
  domains: Domain[];
  cartridges: Cartridge[];
  totalItems: number;
  activeCartridges: number;
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
        {activeCartridges} cartuchos · DuckDB + Parquet
      </footer>
    </aside>
  );
}

function Header({
  period,
  activeCartridges,
  loading,
  onRefresh,
}: {
  period: string;
  activeCartridges: number;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <header className="header">
      <div>
        <h1>Dashboard Operativo</h1>
        <p>{period}</p>
      </div>
      <div className="header-actions">
        <span>{activeCartridges} cartuchos</span>
        <button className="tool-button" type="button" onClick={onRefresh} disabled={loading}>
          {loading ? <Loader2 aria-hidden className="spin" /> : <RefreshCcw aria-hidden />}
          Refrescar
        </button>
      </div>
    </header>
  );
}

function DashboardView({
  dashboard,
  state,
  domains,
  domain,
  severity,
  groupedItems,
  filteredCount,
  openCount,
  collapsed,
  onDomain,
  onSeverity,
  onToggleDomain,
  onOpenItem,
}: {
  dashboard: Dashboard | null;
  state: LoadState;
  domains: Domain[];
  domain: string;
  severity: Severity | "all";
  groupedItems: Array<{ domain: Domain; items: ControlItem[] }>;
  filteredCount: number;
  openCount: number;
  collapsed: Set<string>;
  onDomain: (domain: string) => void;
  onSeverity: (severity: Severity | "all") => void;
  onToggleDomain: (domainId: string) => void;
  onOpenItem: (item: ControlItem) => void;
}) {
  return (
    <div className="content">
      <div className="tabs" role="group" aria-label="Filtro por dominio">
        <button type="button" className={domain === "all" ? "active" : ""} onClick={() => onDomain("all")}>
          Todos
        </button>
        {domains.filter((item) => item.modules.length > 0).map((item) => (
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

      <section className="summary-row" aria-label="Resumen ejecutivo">
        <SummaryCard icon={Gauge} label="Senales" value={dashboard?.summary.total_items ?? "..."} />
        <SummaryCard icon={AlertTriangle} label="Criticas" value={dashboard?.summary.critical ?? 0} tone="critical" />
        <SummaryCard icon={Activity} label="Atencion" value={dashboard?.summary.attention ?? 0} tone="attention" />
        <SummaryCard icon={FileCheck2} label="Decisiones" value={dashboard?.summary.open_decisions ?? 0} />
      </section>

      <section className="operations-strip" aria-label="Ciclo y salud operativa">
        <OmegaCycleBar
          steps={dashboard?.omega_steps ?? defaultOmegaSteps}
          counts={dashboard?.summary.cycle_counts}
        />
        <SourceHealthPanel
          sourceStates={dashboard?.summary.source_states}
          totalSources={dashboard?.sources.length ?? 0}
        />
        <ThresholdPanel thresholds={dashboard?.summary.thresholds} />
        <LearningPanel lessons={dashboard?.summary.lessons} />
      </section>

      <FinancialPanel financial={dashboard?.summary.financial} />

      <section className="section-block" aria-label="Estado por dominio">
        <p className="section-kicker">Estado por dominio</p>
        <div className="domain-list">
          {domains.filter((item) => item.modules.length > 0).map((item) => (
            <DomainSection
              key={item.id}
              domain={item}
              collapsed={collapsed.has(item.id)}
              onToggle={() => onToggleDomain(item.id)}
            />
          ))}
        </div>
      </section>

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
}: {
  lessons?: Dashboard["summary"]["lessons"];
}) {
  const total = lessons?.total ?? 0;
  const topPattern = lessons?.top_patterns?.[0];
  const recent = lessons?.recent?.[0];
  return (
    <section className="learning-panel" aria-label="Lecciones aprendidas">
      <div className="learning-title">
        <p className="section-kicker">Aprendizaje</p>
        <strong><BookOpen aria-hidden /> {total}</strong>
      </div>
      <p>
        {topPattern
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
        <em>{domain.cartridge_count} cartuchos · {domain.item_count} items</em>
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
  onBack,
  onMode,
  onManualTab,
  onCreateDecision,
  onSelectOption,
  onPreview,
  onDryRun,
  onExecute,
  onRunAuto,
  onApprove,
  onDismiss,
}: {
  item: ControlItem;
  omegaSteps: Array<{ id: string; label: string }>;
  mode: DetailMode;
  manualTab: number;
  busyAction: string;
  actionError: string;
  onBack: () => void;
  onMode: (mode: DetailMode) => void;
  onManualTab: (index: number) => void;
  onCreateDecision: (item: ControlItem) => void;
  onSelectOption: (item: ControlItem, optionId: string) => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onRunAuto: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
}) {
  const activeStep = mode === null
    ? "signals"
    : mode === "auto"
      ? "execution"
      : ["investigation", "options", item.decision_id ? "execution" : "decision", "control", "lessons"][manualTab] || "investigation";

  function jumpToStep(stepId: string) {
    if (stepId === "signals") {
      onMode(null);
      onManualTab(0);
      return;
    }
    onMode("manual");
    const tabByStep: Record<string, number> = {
      investigation: 0,
      options: 1,
      decision: 2,
      execution: 2,
      control: 3,
      lessons: 4,
    };
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

      {mode === null ? (
        <section className="mode-cards" aria-label="Seleccion de modo">
          <button type="button" className="mode-card auto" onClick={() => onMode("auto")}>
            <Activity aria-hidden />
            <strong>Modo automatico</strong>
            <span>OMEGA ejecuta el recorrido recomendado y deja trazabilidad.</span>
          </button>
          <button type="button" className="mode-card manual" onClick={() => onMode("manual")}>
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
          onPreview={onPreview}
          onDryRun={onDryRun}
          onExecute={onExecute}
          onApprove={onApprove}
          onDismiss={onDismiss}
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
    { title: "Investigacion", text: item.omega.investigation.root_cause || item.root_cause || "Pendiente", done: true },
    { title: "Opciones evaluadas", text: selectedOption ? `${selectedOption.action || selectedOption.label} · score ${selectedOption.score}` : "Sin opcion", done: Boolean(selectedOption) },
    { title: "Decision", text: item.decision_id ? `Decision #${item.decision_id}` : "Lista para crear decision", done: Boolean(item.decision_id) },
    { title: "Preview / dry-run", text: item.execution_status === "dry_run_validated" ? "Dry-run validado sin write-back" : "Pendiente de validacion segura", done: item.execution_status === "dry_run_validated" },
    { title: "Reglas aprendidas", text: item.omega.lessons.rules[0] || "Regla pendiente", done: terminalStatuses.has(item.status) },
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
  onPreview,
  onDryRun,
  onExecute,
  onApprove,
  onDismiss,
}: {
  item: ControlItem;
  tab: number;
  busyAction: string;
  actionError: string;
  onTab: (index: number) => void;
  onCreateDecision: (item: ControlItem) => void;
  onSelectOption: (item: ControlItem, optionId: string) => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
}) {
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
            onClick={() => onTab(index)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="panel">
        {tab === 0 ? <InvestigationPanel item={item} /> : null}
        {tab === 1 ? (
          <OptionsPanel
            item={item}
            busyAction={busyAction}
            onSelectOption={onSelectOption}
          />
        ) : null}
        {tab === 2 ? (
          <ExecutionPanel
            item={item}
            busyAction={busyAction}
            actionError={actionError}
            onCreateDecision={onCreateDecision}
            onPreview={onPreview}
            onDryRun={onDryRun}
            onExecute={onExecute}
            onApprove={onApprove}
            onDismiss={onDismiss}
          />
        ) : null}
        {tab === 3 ? <ControlPanel item={item} /> : null}
        {tab === 4 ? <RulesPanel item={item} /> : null}
      </div>

      <div className="bottom-nav">
        <button type="button" onClick={() => onTab(Math.max(0, tab - 1))} disabled={tab === 0}>
          Anterior
        </button>
        <div>
          {manualTabs.map((label, index) => (
            <span className={index === tab ? "active" : ""} key={label} />
          ))}
        </div>
        <button type="button" onClick={() => onTab(Math.min(manualTabs.length - 1, tab + 1))} disabled={tab === manualTabs.length - 1}>
          Siguiente
        </button>
      </div>
    </section>
  );
}

function InvestigationPanel({ item }: { item: ControlItem }) {
  return (
    <div className="panel-grid">
      <PanelRow label="Causa probable" value={item.omega.investigation.root_cause || item.root_cause || "Pendiente"} />
      <PanelRow label="Impacto" value={item.omega.investigation.impact || item.impact || "Riesgo operativo"} />
      <PanelRow label="Recomendacion" value={item.recommendation} />
      {item.sql ? <SqlToggle sql={item.sql} /> : null}
    </div>
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

function ExecutionPanel({
  item,
  busyAction,
  actionError,
  onCreateDecision,
  onPreview,
  onDryRun,
  onExecute,
  onApprove,
  onDismiss,
}: {
  item: ControlItem;
  busyAction: string;
  actionError: string;
  onCreateDecision: (item: ControlItem) => void;
  onPreview: (item: ControlItem) => void;
  onDryRun: (item: ControlItem) => void;
  onExecute: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
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

function ControlPanel({ item }: { item: ControlItem }) {
  const controls = item.omega.control.items || [];
  return (
    <div className="control-list">
      {controls.map((control) => (
        <article className="control-row" key={control.id}>
          <strong>{control.desc}</strong>
          <dl>
            <div><dt>Owner</dt><dd>{control.owner}</dd></div>
            <div><dt>Estado</dt><dd>{control.st}</dd></div>
            <div><dt>Impacto</dt><dd>{control.impact}</dd></div>
            <div><dt>Dias</dt><dd>{control.days}</dd></div>
          </dl>
        </article>
      ))}
    </div>
  );
}

function RulesPanel({ item }: { item: ControlItem }) {
  const related = item.related_lessons || [];
  return (
    <div className="rules-list">
      {related.length ? (
        <section className="learned-history" aria-label="Historial de aprendizaje">
          <div>
            <span>{related.length} persistidas</span>
            <strong>Patron aprendido para {item.cartridge}</strong>
          </div>
          {related.slice(0, 3).map((lesson) => (
            <article key={`${lesson.id || lesson.item_id}-${lesson.rule}`}>
              <span>Decision #{lesson.source_decision_id || "N/D"} · confianza {Math.round((lesson.confidence || 0) * 100)}%</span>
              <p>{lesson.rule}</p>
            </article>
          ))}
        </section>
      ) : null}
      {item.omega.lessons.rules.map((rule) => (
        <PanelRow key={rule} label="Regla aprendida" value={rule} />
      ))}
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
