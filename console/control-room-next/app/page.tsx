"use client";

import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  ClipboardCheck,
  Database,
  FileCheck2,
  Filter,
  Gauge,
  Layers3,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";

type Severity = "critical" | "high" | "medium" | "low";
type SourceState = "ok" | "empty" | "missing" | "unavailable" | "invalid_schema";
type LoadState = "loading" | "ready" | "error";

interface SourceStatus {
  dataset: string;
  cartridge: string;
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
}

interface DomainModule {
  id: string;
  label: string;
  domain: string;
  accent: string;
  item_count: number;
  critical_count: number;
  source_status: "ok" | "empty" | "attention" | "inactive" | "no_sources";
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
  label: string;
  domain: string;
  accent: string;
  status: string;
  current_step?: string;
  active: boolean;
  operational: boolean;
  item_count: number;
  critical_count: number;
  source_status: "ok" | "empty" | "attention" | "inactive" | "no_sources";
  datasets: SourceStatus[];
}

interface OmegaOption {
  id: string;
  label: string;
  score: number;
  risk: string;
  recommendation: string;
  selected: boolean;
}

interface OmegaAction {
  id: string;
  label: string;
  approved: boolean;
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
  cartridge: string;
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
  status: string;
  decision_id?: number | null;
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
  };
  domains: Domain[];
  cartridges: Cartridge[];
  sources: SourceStatus[];
  items: ControlItem[];
}

const severityLabels: Record<Severity, string> = {
  critical: "Critica",
  high: "Alta",
  medium: "Media",
  low: "Baja",
};

const sourceStateLabels: Record<string, string> = {
  ok: "OK",
  empty: "Vacio",
  missing: "Faltante",
  unavailable: "Caido",
  invalid_schema: "Contrato",
  attention: "Atencion",
  inactive: "Inactivo",
  no_sources: "Sin fuente",
};

const statusLabels: Record<string, string> = {
  open: "Abierto",
  in_review: "En revision",
  decision_created: "Decision",
  approved: "Aprobado",
  dismissed: "Descartado",
  resolved: "Resuelto",
};

const terminalStatuses = new Set(["approved", "dismissed", "resolved"]);

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

function activeOpen(item: ControlItem): boolean {
  return !terminalStatuses.has(item.status);
}

export default function ControlRoomPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [domain, setDomain] = useState("all");
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [cartridge, setCartridge] = useState("all");
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
    && (cartridge === "all" || item.cartridge === cartridge)
  )), [cartridge, domain, items, severity]);

  const selected = filtered.find((item) => item.id === selectedId)
    || items.find((item) => item.id === selectedId)
    || filtered[0]
    || null;

  async function createDecision(item: ControlItem) {
    setBusyAction(`decision:${item.id}`);
    setActionError("");
    try {
      const payload = await apiJson<{ decision: { id: number }; item: ControlItem }>(
        `/api/control-room/items/${encodeURIComponent(item.id)}/decision`,
        { method: "POST", body: JSON.stringify({}) },
      );
      await load(payload.item.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo crear la decision");
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
      await load(payload.item.id);
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
      await load(payload.item.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo descartar el item");
    } finally {
      setBusyAction("");
    }
  }

  return (
    <main className="control-app">
      <aside className="control-sidebar" aria-label="Dominios y cartuchos">
        <div className="brand-lockup">
          <strong>OMEGA</strong>
          <span>Control Room</span>
        </div>
        <div className="sidebar-section">
          <p className="section-kicker">Dominios</p>
          <button
            type="button"
            className={`domain-link ${domain === "all" ? "active" : ""}`}
            onClick={() => setDomain("all")}
          >
            <CircleDot aria-hidden />
            <span>Todos</span>
            <em>{dashboard?.summary.total_items ?? 0}</em>
          </button>
          {domains.map((item) => (
            <button
              type="button"
              key={item.label}
              className={`domain-link ${domain === item.label ? "active" : ""}`}
              onClick={() => setDomain(item.label)}
              style={{ "--accent": item.accent } as CSSProperties}
            >
              <CircleDot aria-hidden />
              <span>{item.label}</span>
              <em>{item.item_count}</em>
            </button>
          ))}
        </div>
        <div className="sidebar-section">
          <p className="section-kicker">Cartuchos activos</p>
          <div className="cartridge-stack">
            {cartridges.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`cartridge-chip ${cartridge === item.id ? "active" : ""}`}
                onClick={() => setCartridge((current) => (current === item.id ? "all" : item.id))}
              >
                <span className={`source-dot ${item.source_status}`} aria-hidden />
                <span>{item.label}</span>
                <em>{item.item_count}</em>
              </button>
            ))}
          </div>
        </div>
      </aside>

      <section className="control-main">
        <header className="topbar">
          <div>
            <p className="section-kicker">Dashboard Operativo</p>
            <h1>Sala de Control</h1>
          </div>
          <div className="topbar-actions">
            <span className="period-pill">{dashboard?.period || "Periodo operativo"}</span>
            <span className="period-pill">{activeCartridges.length} cartuchos</span>
            <button className="tool-button" type="button" onClick={() => void load()} disabled={state === "loading"}>
              {state === "loading" ? <Loader2 aria-hidden className="spin" /> : <RefreshCcw aria-hidden />}
              Refrescar
            </button>
          </div>
        </header>

        {state === "error" ? (
          <section className="state-panel error-state" role="alert">
            <AlertTriangle aria-hidden />
            <div>
              <h2>No se pudo cargar la Sala de Control</h2>
              <p>{error}</p>
            </div>
          </section>
        ) : null}

        <section className="summary-grid" aria-label="Resumen ejecutivo">
          <Metric icon={Gauge} label="Items activos" value={dashboard?.summary.total_items ?? "..."} />
          <Metric icon={AlertTriangle} label="Criticos" value={dashboard?.summary.critical ?? 0} tone="danger" />
          <Metric icon={FileCheck2} label="Decisiones" value={dashboard?.summary.open_decisions ?? "..."} />
          <Metric icon={Database} label="Fuentes OK" value={dashboard?.summary.source_states.ok ?? "..."} tone="success" />
        </section>

        <section className="workspace-layout">
          <section className="operations-column" aria-label="Indicadores por dominio">
            <div className="filters-row">
              <div className="segmented" role="group" aria-label="Filtro por dominio">
                <button type="button" className={domain === "all" ? "active" : ""} onClick={() => setDomain("all")}>
                  Todos
                </button>
                {domains.filter((item) => item.item_count > 0 || item.cartridge_count > 0).map((item) => (
                  <button type="button" key={item.label} className={domain === item.label ? "active" : ""} onClick={() => setDomain(item.label)}>
                    {item.label}
                  </button>
                ))}
              </div>
              <label className="inline-filter">
                <Filter aria-hidden />
                <select value={severity} onChange={(event) => setSeverity(event.target.value as Severity | "all")}>
                  <option value="all">Todas</option>
                  <option value="critical">Critica</option>
                  <option value="high">Alta</option>
                  <option value="medium">Media</option>
                  <option value="low">Baja</option>
                </select>
              </label>
            </div>

            <DomainMatrix domains={domains} domain={domain} />
            <SourceReadiness sources={dashboard?.sources || []} />

            <section className="items-panel" aria-label="Items OMEGA">
              <header className="panel-heading">
                <div>
                  <p className="section-kicker">Senales detectadas</p>
                  <h2>{filtered.length} items priorizados</h2>
                </div>
                <span>{filtered.filter(activeOpen).length} abiertos</span>
              </header>

              {state === "loading" ? (
                <div className="state-panel">
                  <Loader2 aria-hidden className="spin" />
                  <span>Cargando datos operativos...</span>
                </div>
              ) : null}

              {state === "ready" && filtered.length === 0 ? (
                <div className="state-panel">
                  <CheckCircle2 aria-hidden />
                  <span>No hay items para estos filtros.</span>
                </div>
              ) : null}

              <div className="items-stack">
                {filtered.map((item) => (
                  <ItemCard
                    key={item.id}
                    item={item}
                    selected={selected?.id === item.id}
                    onSelect={() => setSelectedId(item.id)}
                  />
                ))}
              </div>
            </section>
          </section>

          <DetailPanel
            item={selected}
            busyAction={busyAction}
            actionError={actionError}
            onCreateDecision={createDecision}
            onApprove={approve}
            onDismiss={dismiss}
          />
        </section>
      </section>
    </main>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  tone?: "neutral" | "danger" | "success";
}) {
  return (
    <div className={`metric-card ${tone}`}>
      <span className="metric-icon"><Icon aria-hidden /></span>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function DomainMatrix({ domains, domain }: { domains: Domain[]; domain: string }) {
  const visible = domain === "all" ? domains : domains.filter((item) => item.label === domain);
  return (
    <section className="domain-matrix" aria-label="Indicadores por dominio">
      {visible.filter((item) => item.modules.length > 0).map((item) => (
        <article className="domain-band" key={item.label} style={{ "--accent": item.accent } as CSSProperties}>
          <header>
            <div>
              <span className="accent-square" aria-hidden />
              <h2>{item.label}</h2>
            </div>
            <p>{item.cartridge_count} cartuchos · {item.item_count} items</p>
          </header>
          <div className="module-grid">
            {item.modules.map((module) => (
              <div className="module-cell" key={`${item.label}-${module.id}`}>
                <div className="module-title">
                  <span>{module.label}</span>
                  <em>{sourceStateLabels[module.source_status] || module.source_status}</em>
                </div>
                {module.kpis.map((kpi) => (
                  <div className="module-kpi" key={kpi.label}>
                    <span>{kpi.label}</span>
                    <strong>{kpi.value}</strong>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}

function SourceReadiness({ sources }: { sources: SourceStatus[] }) {
  return (
    <section className="source-panel" aria-label="Fuentes">
      <header className="panel-heading compact">
        <div>
          <p className="section-kicker">Fuentes</p>
          <h2>Readiness</h2>
        </div>
        <Database aria-hidden />
      </header>
      <div className="source-grid">
        {sources.map((source) => (
          <div className="source-row" key={source.dataset}>
            <span className={`source-dot ${source.status}`} aria-hidden />
            <div>
              <strong>{source.module}</strong>
              <p>{source.dataset}</p>
            </div>
            <em>{source.count}</em>
          </div>
        ))}
      </div>
    </section>
  );
}

function ItemCard({ item, selected, onSelect }: { item: ControlItem; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      className={`item-card ${selected ? "active" : ""} ${item.status}`}
      onClick={onSelect}
      aria-label={`Investigar ${item.title}: ${item.entity_label}`}
    >
      <span className={`severity-rail ${item.severity}`} aria-hidden />
      <div className="item-card-main">
        <div className="item-card-top">
          <span className={`severity-pill ${item.severity}`}>{severityLabels[item.severity]}</span>
          <span>{item.module}</span>
          <ChevronRight aria-hidden />
        </div>
        <h3>{item.title}</h3>
        <p>{item.description}</p>
        <div className="item-card-meta">
          <span>{item.domain}</span>
          <span>{item.entity_label}</span>
          <span>{statusLabels[item.status] || item.status}</span>
        </div>
      </div>
    </button>
  );
}

function DetailPanel({
  item,
  busyAction,
  actionError,
  onCreateDecision,
  onApprove,
  onDismiss,
}: {
  item: ControlItem | null;
  busyAction: string;
  actionError: string;
  onCreateDecision: (item: ControlItem) => void;
  onApprove: (item: ControlItem) => void;
  onDismiss: (item: ControlItem) => void;
}) {
  if (!item) {
    return (
      <section className="detail-panel" aria-label="Detalle">
        <div className="state-panel centered">
          <Layers3 aria-hidden />
          <span>Selecciona una senal para investigar.</span>
        </div>
      </section>
    );
  }

  const decisionLabel = item.decision_id ? `Decision #${item.decision_id}` : "Crear decision";
  const approved = item.status === "approved";

  return (
    <section className="detail-panel" aria-label="Detalle OMEGA">
      <div className="detail-topline">
        <span className={`severity-pill ${item.severity}`}>{severityLabels[item.severity]}</span>
        <span>{fmtDate(item.detected_at)}</span>
      </div>
      <h2>{item.title}</h2>
      <p className="detail-lead">{item.description}</p>

      <div className="detail-grid">
        <InfoBlock label="Dominio" value={item.domain} />
        <InfoBlock label="Cartucho" value={item.cartridge} />
        <InfoBlock label="Entidad" value={`${item.entity_kind}: ${item.entity_label}`} />
        <InfoBlock label="Fuente" value={item.source_dataset} />
      </div>

      <OmegaTimeline item={item} />

      <div className="recommendation-box">
        <div className="box-title">
          <Activity aria-hidden />
          <span>Recomendacion</span>
        </div>
        <p>{item.recommendation}</p>
      </div>

      <div className="actions-row">
        <button
          type="button"
          className="secondary-action"
          onClick={() => onCreateDecision(item)}
          disabled={Boolean(item.decision_id) || busyAction !== ""}
        >
          {busyAction === `decision:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <FileCheck2 aria-hidden />}
          {decisionLabel}
        </button>
        <button
          type="button"
          className="primary-action"
          onClick={() => onApprove(item)}
          disabled={approved || busyAction !== ""}
        >
          {busyAction === `approve:${item.id}` ? <Loader2 aria-hidden className="spin" /> : <ClipboardCheck aria-hidden />}
          {approved ? "Aprobada" : "Aprobar recomendacion"}
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
      </div>
      {actionError ? (
        <p className="action-error" role="alert">{actionError}</p>
      ) : null}
    </section>
  );
}

function OmegaTimeline({ item }: { item: ControlItem }) {
  const option = item.omega.options.find((candidate) => candidate.selected) || item.omega.options[0];
  const steps = [
    {
      label: "Senales",
      icon: AlertTriangle,
      value: `${severityLabels[item.severity]} · ${item.source_dataset}`,
      done: true,
    },
    {
      label: "Investigacion",
      icon: ShieldCheck,
      value: item.omega.investigation.root_cause || item.root_cause || "Pendiente",
      done: true,
    },
    {
      label: "Opciones",
      icon: Layers3,
      value: option ? `${option.label} · score ${option.score}` : "Pendiente",
      done: Boolean(option),
    },
    {
      label: "Decision",
      icon: FileCheck2,
      value: item.omega.decision.label,
      done: Boolean(item.decision_id),
    },
    {
      label: "Ejecucion",
      icon: Activity,
      value: item.omega.execution.actions.filter((action) => action.approved).length ? "Accion aprobada" : "Pendiente",
      done: item.status === "approved",
    },
    {
      label: "Control",
      icon: Gauge,
      value: item.omega.control.status || "abierto",
      done: terminalStatuses.has(item.status),
    },
    {
      label: "Lecciones",
      icon: CheckCircle2,
      value: item.omega.lessons.rules[0] || "Regla pendiente",
      done: terminalStatuses.has(item.status),
    },
  ];

  return (
    <section className="omega-panel" aria-label="Ciclo OMEGA">
      {steps.map(({ label, icon: Icon, value, done }) => (
        <div className={`omega-step ${done ? "done" : ""}`} key={label}>
          <span><Icon aria-hidden /></span>
          <div>
            <strong>{label}</strong>
            <p>{value}</p>
          </div>
        </div>
      ))}
    </section>
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
