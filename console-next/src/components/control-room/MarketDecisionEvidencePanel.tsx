"use client";

import {
  Activity,
  BarChart3,
  BrainCircuit,
  CircleCheck,
  CircleDashed,
  Loader2,
  Play,
  ShieldCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  getMarketDecisionValidation,
  runMarketDecisionValidation,
} from "@/lib/control-room/client";
import type { MarketDecisionValidationPayload } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

type ViewProps = {
  payload: MarketDecisionValidationPayload | null;
  loading: boolean;
  running: boolean;
  error: string;
  onRun: () => void;
};

function number(value: number | string | null | undefined, digits = 2): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "N/D";
  return new Intl.NumberFormat("es-MX", { maximumFractionDigits: digits }).format(parsed);
}

function statusLabel(value: string): string {
  if (value === "ready") return "Listo";
  if (value === "partial") return "Parcial";
  if (value === "cpa_real") return "Datos C/P/A reales";
  if (value === "benchmark_internal") return "Referencia interna";
  if (value === "insufficient_data") return "Sin evidencia histórica";
  return value ? value.replaceAll("_", " ") : "Pendiente";
}

function StageRow({
  icon: Icon,
  title,
  detail,
  status,
}: {
  icon: LucideIcon;
  title: string;
  detail: string;
  status: string;
}) {
  const ready = status === "ready";
  return (
    <div className="grid min-h-[72px] grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 px-3 py-2.5">
      <span className={cn(
        "flex h-8 w-8 items-center justify-center rounded-md",
        ready ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" : "bg-amber-500/10 text-amber-700 dark:text-amber-300",
      )}>
        <Icon aria-hidden className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground dark:text-slate-100">{title}</p>
        <p className="break-words text-xs text-muted-foreground">{detail}</p>
      </div>
      <span className={cn(
        "whitespace-nowrap text-xs font-medium",
        ready ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300",
      )}>
        {ready ? <CircleCheck aria-hidden className="mr-1 inline h-3.5 w-3.5" /> : <CircleDashed aria-hidden className="mr-1 inline h-3.5 w-3.5" />}
        {statusLabel(status)}
      </span>
    </div>
  );
}

export function MarketDecisionEvidenceView({ payload, loading, running, error, onRun }: ViewProps) {
  const source = payload?.source;
  const market = payload?.market_context;
  const simulation = payload?.simulation;
  const bayes = payload?.bayes;
  const orchestration = payload?.orchestration;
  const sourceStatus = source?.input_status === "ready" ? "ready" : source?.input_status || "insufficient_data";
  const marketStatus = market?.freshness_status === "ready" && simulation?.market_evidence_count ? "ready" : "insufficient_data";
  const simulationStatus = simulation?.available ? "ready" : "insufficient_data";
  const orchestrationStatus = orchestration?.available ? "ready" : "insufficient_data";

  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423]" aria-label="Validación de contexto externo">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Evidencia externa gobernada</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">Validación macro de talento</h2>
          <p className="mt-1 text-sm text-muted-foreground">WB-TALENTO · escenario normalizado de costo por demora</p>
        </div>
        <button
          type="button"
          onClick={onRun}
          disabled={loading || running}
          className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-sky-600 px-3 text-sm font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {running ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Play aria-hidden className="h-4 w-4" />}
          {running ? "Validando" : "Recalcular validación"}
        </button>
      </div>

      {error ? (
        <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-300">{error}</p>
      ) : null}

      {payload === null && !error ? (
        <div role="status" aria-live="polite" className="mt-4 rounded-md border p-4 dark:border-sky-400/20">
          <p className="text-sm text-muted-foreground">Cargando validación…</p>
          <div aria-hidden className="mt-3 space-y-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <span key={index} className="block h-10 animate-pulse rounded-md bg-muted/60 dark:bg-slate-800/60" />
            ))}
          </div>
        </div>
      ) : null}

      {payload ? (
      <div className="mt-4 divide-y rounded-md border dark:divide-sky-400/10 dark:border-sky-400/20">
        <StageRow
          icon={Activity}
          title="SuccessFactors"
          detail={`${number(source?.employee_count, 0)} empleados · ${statusLabel(source?.source_mode || "missing")}`}
          status={sourceStatus}
        />
        <StageRow
          icon={BarChart3}
          title="Contexto Banxico"
          detail={market?.as_of ? `USD/MXN al ${market.as_of} · confianza ${number(market.confidence)}` : "Sin contexto usado todavía"}
          status={marketStatus}
        />
        <StageRow
          icon={BrainCircuit}
          title="Análisis de escenarios"
          detail={simulation?.available ? `Banda P10–P90: ${number(simulation.p10)}–${number(simulation.p90)}` : "Pendiente de ejecución manual"}
          status={simulationStatus}
        />
        <StageRow
          icon={ShieldCheck}
          title="Ajuste por historial"
          detail={`${number(bayes?.sample_count, 0)} muestras · evidencia externa sin ajuste automático`}
          status={bayes?.status || "insufficient_data"}
        />
        <StageRow
          icon={ShieldCheck}
          title="Decisión supervisada"
          detail="Sin causalidad declarada, acción automática ni write-back"
          status={orchestrationStatus}
        />
      </div>
      ) : null}

      {payload?.status === "partial" ? (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
          Resultado parcial: SuccessFactors usa referencia interna o el ajuste por historial aún no tiene resultados observados.
        </p>
      ) : null}
      {payload?.status === "insufficient_data" ? (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
          Datos insuficientes: la validación no cuenta con evidencia mínima y no se muestra un resultado simulado.
        </p>
      ) : null}
    </section>
  );
}

export function MarketDecisionEvidencePanel() {
  const [payload, setPayload] = useState<MarketDecisionValidationPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    getMarketDecisionValidation()
      .then((result) => {
        if (!cancelled) setPayload(result);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "No se pudo cargar la validación");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const run = useCallback(async () => {
    setRunning(true);
    setError("");
    try {
      setPayload(await runMarketDecisionValidation());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo ejecutar la validación");
    } finally {
      setRunning(false);
    }
  }, []);

  return (
    <MarketDecisionEvidenceView
      payload={payload}
      loading={loading}
      running={running}
      error={error}
      onRun={() => void run()}
    />
  );
}
