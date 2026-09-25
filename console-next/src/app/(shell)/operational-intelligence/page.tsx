"use client";

import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ClipboardCheck,
  FileClock,
  History,
  Loader2,
  RefreshCw,
  Route,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import {
  createDecisionPlan,
  getConfidenceHistory,
  listDecisionPlans,
  listHistoricalValidations,
  listOperationalHistory,
  listOperationalRuns,
  listScenarioAnalyses,
  runHistoricalValidation,
} from "@/lib/operational-intelligence/client";
import type {
  DecisionPlanSummary,
  HistoricalValidationSummary,
  OperationalRecord,
  OperationalRunSummary,
  ScenarioSummary,
} from "@/lib/operational-intelligence/types";
import { cn } from "@/lib/utils";

import { OiTablist, OiTabPanel, type OiTabMeta } from "./tablist-a11y";

type TabId = "scenarios" | "confidence" | "plans" | "validations" | "runs";

const TABS: Array<OiTabMeta<TabId>> = [
  { id: "scenarios", label: "Escenarios", icon: Sparkles },
  { id: "confidence", label: "Confianza e Historial", icon: History },
  { id: "plans", label: "Planes de decisión", icon: Route },
  { id: "validations", label: "Validación histórica", icon: ClipboardCheck },
  { id: "runs", label: "Ejecuciones", icon: Activity },
];

function shortDate(value?: string | null): string {
  if (!value) return "Sin fecha";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sin fecha";
  return parsed.toLocaleString("es", { dateStyle: "short", timeStyle: "short" });
}

function pickText(row: OperationalRecord, keys: string[], fallback = "Sin título"): string {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number") return String(value);
  }
  return fallback;
}

function statusCopy(status?: string | null): string {
  switch (status) {
    case "completed":
    case "success":
    case "ready":
      return "Listo";
    case "running":
    case "queued":
      return "Preparando";
    case "partial":
      return "Datos parciales";
    case "failed":
    case "error":
      return "Requiere revisión";
    case "blocked":
      return "En espera de datos";
    default:
      return status || "Sin estado";
  }
}

function extractCount(value: unknown, keys: string[]): number {
  if (!value || typeof value !== "object") return 0;
  const record = value as Record<string, unknown>;
  for (const key of keys) {
    const item = record[key];
    if (typeof item === "number" && Number.isFinite(item)) return item;
    if (Array.isArray(item)) return item.length;
  }
  return 0;
}

export default function OperationalIntelligencePage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabId>("scenarios");
  const [planTitle, setPlanTitle] = useState("");
  const [planDescription, setPlanDescription] = useState("");
  const [validationDataset, setValidationDataset] = useState("");
  const [validationMetric, setValidationMetric] = useState("");

  const scenarios = useQuery({
    queryKey: ["operational-intelligence", "scenarios"],
    queryFn: () => listScenarioAnalyses(50),
    staleTime: 30_000,
  });
  const confidence = useQuery({
    queryKey: ["operational-intelligence", "confidence"],
    queryFn: getConfidenceHistory,
    staleTime: 30_000,
  });
  const history = useQuery({
    queryKey: ["operational-intelligence", "history"],
    queryFn: () => listOperationalHistory(100),
    staleTime: 30_000,
  });
  const plans = useQuery({
    queryKey: ["operational-intelligence", "plans"],
    queryFn: () => listDecisionPlans(50),
    staleTime: 30_000,
  });
  const validations = useQuery({
    queryKey: ["operational-intelligence", "validations"],
    queryFn: () => listHistoricalValidations(50),
    staleTime: 30_000,
  });
  const runs = useQuery({
    queryKey: ["operational-intelligence", "runs"],
    queryFn: () => listOperationalRuns(50),
    staleTime: 30_000,
  });

  const refreshAll = async () => {
    await Promise.all([
      scenarios.refetch(),
      confidence.refetch(),
      history.refetch(),
      plans.refetch(),
      validations.refetch(),
      runs.refetch(),
    ]);
  };

  const createPlan = useMutation({
    mutationFn: () => createDecisionPlan({
      source_type: "manual_fixture",
      source_id: `ui-${Date.now()}`,
      title: planTitle.trim() || "Plan supervisado",
      description: planDescription.trim() || undefined,
      metrics: {},
      constraints: {},
      evidence_refs: [],
    }),
    onSuccess: async () => {
      setPlanTitle("");
      setPlanDescription("");
      await queryClient.invalidateQueries({ queryKey: ["operational-intelligence", "plans"] });
      toast.success("Plan preparado.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo preparar el plan.");
    },
  });

  const runValidation = useMutation({
    mutationFn: () => runHistoricalValidation({
      source_dataset: validationDataset.trim() || undefined,
      metric: validationMetric.trim(),
      mode: "historical_replay",
      labels_required: 10,
      result_limit: 100,
    }),
    onSuccess: async () => {
      setValidationDataset("");
      setValidationMetric("");
      await queryClient.invalidateQueries({ queryKey: ["operational-intelligence", "validations"] });
      toast.success("Validación iniciada.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo iniciar la validación.");
    },
  });

  const loading = scenarios.isLoading || confidence.isLoading || history.isLoading || plans.isLoading || validations.isLoading || runs.isLoading;
  const summary = useMemo(() => ({
    scenarios: scenarios.data ? scenarios.data.items.length : "—",
    plans: plans.data ? plans.data.items.length : "—",
    history: history.data ? history.data.items.length : "—",
    runs: runs.data ? runs.data.items.length : "—",
  }), [history.data, plans.data, runs.data, scenarios.data]);

  return (
    <main className="min-h-screen bg-background p-4 md:p-6" data-testid="operational-intelligence-page">
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <header className="flex flex-col gap-3 border-b pb-4 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase text-primary">OMEGA</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Inteligencia Operativa</h1>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              Escenarios simulados, confianza, planes y validaciones históricas de la consola.
            </p>
          </div>
          <button
            type="button"
            onClick={() => refreshAll()}
            className="inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw aria-hidden className={cn("h-4 w-4", loading && "animate-spin")} />
            Actualizar
          </button>
        </header>

        <section className="grid gap-3 md:grid-cols-4" aria-label="Resumen">
          <SummaryTile label="Escenarios" value={summary.scenarios} />
          <SummaryTile label="Planes" value={summary.plans} />
          <SummaryTile label="Eventos históricos" value={summary.history} />
          <SummaryTile label="Ejecuciones" value={summary.runs} />
        </section>

        <OiTablist tabs={TABS} activeId={activeTab} onChange={setActiveTab} label="Inteligencia Operativa" />

        <OiTabPanel id="scenarios" active={activeTab === "scenarios"}>
          <ScenariosPanel rows={scenarios.data?.items ?? []} loading={scenarios.isLoading} error={scenarios.error} />
        </OiTabPanel>
        <OiTabPanel id="confidence" active={activeTab === "confidence"}>
          <ConfidencePanel
            confidence={confidence.data}
            history={history.data?.items ?? []}
            loading={confidence.isLoading || history.isLoading}
            error={confidence.error || history.error}
          />
        </OiTabPanel>
        <OiTabPanel id="plans" active={activeTab === "plans"}>
          <PlansPanel
            rows={plans.data?.items ?? []}
            loading={plans.isLoading}
            error={plans.error}
            title={planTitle}
            description={planDescription}
            saving={createPlan.isPending}
            onTitle={setPlanTitle}
            onDescription={setPlanDescription}
            onCreate={() => createPlan.mutate()}
          />
        </OiTabPanel>
        <OiTabPanel id="validations" active={activeTab === "validations"}>
          <ValidationPanel
            rows={validations.data?.items ?? []}
            loading={validations.isLoading}
            error={validations.error}
            dataset={validationDataset}
            metric={validationMetric}
            saving={runValidation.isPending}
            onDataset={setValidationDataset}
            onMetric={setValidationMetric}
            onRun={() => runValidation.mutate()}
          />
        </OiTabPanel>
        <OiTabPanel id="runs" active={activeTab === "runs"}>
          <RunsPanel rows={runs.data?.items ?? []} loading={runs.isLoading} error={runs.error} />
        </OiTabPanel>
      </div>
    </main>
  );
}

function SummaryTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border bg-card p-3">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function PanelShell({
  title,
  description,
  loading,
  error,
  empty,
  isEmpty,
  children,
}: {
  title: string;
  description: string;
  loading?: boolean;
  error?: unknown;
  empty: string;
  isEmpty: boolean;
  children: ReactNode;
}) {
  return (
    <section className="rounded-md border bg-card p-4">
      <div className="mb-4 flex flex-col gap-1">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {loading ? (
        <div role="status" aria-busy="true" className="flex min-h-[180px] items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Cargando
        </div>
      ) : error ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          No se pudo cargar la información.
        </div>
      ) : isEmpty ? (
        <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          {empty}
        </div>
      ) : children}
    </section>
  );
}

function ScenariosPanel({ rows, loading, error }: { rows: ScenarioSummary[]; loading: boolean; error: unknown }) {
  return (
    <PanelShell
      title="Escenarios"
      description="Lectura de escenarios preparados desde señales, decisiones o WisdomBits."
      loading={loading}
      error={error}
      empty="Sin escenarios todavía."
      isEmpty={rows.length === 0}
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((row, index) => (
          <InfoRow
            key={String(row.id || row.simulation_id || row.source_id || index)}
            title={pickText(row, ["label", "title", "source_id"], "Escenario")}
            subtitle={`${statusCopy(String(row.status || ""))} · ${shortDate(row.created_at)}`}
            meta="Simulación de escenarios"
          />
        ))}
      </div>
    </PanelShell>
  );
}

function ConfidencePanel({
  confidence,
  history,
  loading,
  error,
}: {
  confidence?: OperationalRecord;
  history: OperationalRecord[];
  loading: boolean;
  error: unknown;
}) {
  const observations = extractCount(confidence, ["outcomes_count", "observations_count", "total"]);
  return (
    <PanelShell
      title="Confianza e Historial"
      description="Evidencia histórica disponible para explicar qué tan estable es el análisis."
      loading={loading}
      error={error}
      empty="Sin historial suficiente."
      isEmpty={!confidence && history.length === 0}
    >
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="rounded-md border bg-background p-3">
          <div className="text-xs font-medium uppercase text-muted-foreground">Estado</div>
          <div className="mt-2 text-xl font-semibold">{statusCopy(String(confidence?.status || ""))}</div>
          <div className="mt-1 text-sm text-muted-foreground">{observations} observaciones disponibles</div>
        </div>
        <div className="space-y-2">
          {history.slice(0, 8).map((row, index) => (
            <InfoRow
              key={String(row.id || row.run_id || index)}
              title={pickText(row, ["title", "event_type", "source_id"], "Evento histórico")}
              subtitle={shortDate(pickText(row, ["created_at", "observed_at", "finished_at"], ""))}
              meta={statusCopy(pickText(row, ["status", "actual_status"], ""))}
            />
          ))}
        </div>
      </div>
    </PanelShell>
  );
}

function PlansPanel({
  rows,
  loading,
  error,
  title,
  description,
  saving,
  onTitle,
  onDescription,
  onCreate,
}: {
  rows: DecisionPlanSummary[];
  loading: boolean;
  error: unknown;
  title: string;
  description: string;
  saving: boolean;
  onTitle: (value: string) => void;
  onDescription: (value: string) => void;
  onCreate: () => void;
}) {
  return (
    <PanelShell
      title="Planes de decisión"
      description="Preparación supervisada de alternativas con evidencia y restricciones."
      loading={loading}
      error={error}
      empty="Sin planes preparados."
      isEmpty={false}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-2">
          {rows.map((row, index) => (
            <InfoRow
              key={String(row.id || row.orchestration_id || index)}
              title={pickText(row, ["title", "source_id"], "Plan")}
              subtitle={`${statusCopy(String(row.status || ""))} · ${shortDate(row.created_at)}`}
              meta="Preparación supervisada"
            />
          ))}
          {rows.length === 0 ? (
            <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
              Sin planes preparados.
            </div>
          ) : null}
        </div>
        <form
          className="space-y-3 rounded-md border bg-background p-3"
          onSubmit={(event) => {
            event.preventDefault();
            onCreate();
          }}
        >
          <div>
            <label className="text-xs font-medium uppercase text-muted-foreground" htmlFor="plan-title">Nuevo plan</label>
            <input
              id="plan-title"
              value={title}
              onChange={(event) => onTitle(event.target.value)}
              className="mt-1 min-h-[40px] w-full rounded-md border bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Título"
            />
          </div>
          <textarea
            value={description}
            onChange={(event) => onDescription(event.target.value)}
            className="min-h-[110px] w-full resize-y rounded-md border bg-card px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="Describe el objetivo y restricciones."
          />
          <button
            type="submit"
            disabled={saving || title.trim().length < 3}
            className="inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {saving ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <FileClock aria-hidden className="h-4 w-4" />}
            Preparar plan
          </button>
        </form>
      </div>
    </PanelShell>
  );
}

function ValidationPanel({
  rows,
  loading,
  error,
  dataset,
  metric,
  saving,
  onDataset,
  onMetric,
  onRun,
}: {
  rows: HistoricalValidationSummary[];
  loading: boolean;
  error: unknown;
  dataset: string;
  metric: string;
  saving: boolean;
  onDataset: (value: string) => void;
  onMetric: (value: string) => void;
  onRun: () => void;
}) {
  return (
    <PanelShell
      title="Validación histórica"
      description="Pruebas contra datos históricos cuando hay suficientes resultados observados."
      loading={loading}
      error={error}
      empty="Sin validaciones históricas."
      isEmpty={false}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-2">
          {rows.map((row, index) => (
            <InfoRow
              key={String(row.id || row.backtest_id || index)}
              title={pickText(row, ["metric", "title"], "Validación")}
              subtitle={`${statusCopy(String(row.status || ""))} · ${shortDate(row.created_at)}`}
              meta={row.result_count == null ? "Resultados: N/D" : `${row.result_count} resultados`}
            />
          ))}
          {rows.length === 0 ? (
            <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
              Sin validaciones históricas.
            </div>
          ) : null}
        </div>
        <form
          className="space-y-3 rounded-md border bg-background p-3"
          onSubmit={(event) => {
            event.preventDefault();
            onRun();
          }}
        >
          <div>
            <label className="text-xs font-medium uppercase text-muted-foreground" htmlFor="validation-metric">Métrica</label>
            <input
              id="validation-metric"
              value={metric}
              onChange={(event) => onMetric(event.target.value)}
              className="mt-1 min-h-[40px] w-full rounded-md border bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="metric_name"
            />
          </div>
          <input
            value={dataset}
            onChange={(event) => onDataset(event.target.value)}
            className="min-h-[40px] w-full rounded-md border bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="Dataset opcional"
          />
          <button
            type="submit"
            disabled={saving || metric.trim().length < 2}
            className="inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {saving ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <ClipboardCheck aria-hidden className="h-4 w-4" />}
            Iniciar validación
          </button>
        </form>
      </div>
    </PanelShell>
  );
}

function RunsPanel({ rows, loading, error }: { rows: OperationalRunSummary[]; loading: boolean; error: unknown }) {
  return (
    <PanelShell
      title="Ejecuciones"
      description="Historial operativo de análisis ejecutados desde la consola."
      loading={loading}
      error={error}
      empty="Sin ejecuciones registradas."
      isEmpty={rows.length === 0}
    >
      <div className="space-y-2">
        {rows.map((row, index) => (
          <InfoRow
            key={String(row.id || row.run_id || index)}
            title={pickText(row, ["title", "run_id", "id"], "Ejecución")}
            subtitle={`${statusCopy(String(row.status || ""))} · ${shortDate(row.started_at)}`}
            meta={shortDate(row.finished_at)}
          />
        ))}
      </div>
    </PanelShell>
  );
}

function InfoRow({ title, subtitle, meta }: { title: string; subtitle: string; meta: string }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="font-medium">{title}</div>
      <div className="mt-1 text-sm text-muted-foreground">{subtitle}</div>
      <div className="mt-2 text-xs uppercase text-muted-foreground">{meta}</div>
    </div>
  );
}
