import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Bot, BrainCircuit, CheckCircle2, Clock3, Cpu, Database, LockKeyhole, SlidersHorizontal, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import type { DataReadiness, SourceRollup, SourceState } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

export type ControlRoomStatus = DataReadiness | SourceState | SourceRollup | "error";
export type ControlOrigin =
  | "rule"
  | "generic_gold_signal"
  | "gold_generic"
  | "intelligence_signal"
  | "intelligence"
  | "bayes"
  | "bayesian_calibration"
  | "monte_carlo"
  | "agent_alert"
  | "agent"
  | "source_health"
  | "source_state";

export const readinessLabels: Record<string, string> = {
  ready: "Listo",
  ok: "Listo",
  partial: "Datos parciales",
  stub: "Fuera de alcance actual",
  empty: "Sin datos configurados",
  missing: "Dataset no materializado",
  unavailable: "Dependencia no configurada",
  invalid_schema: "Dataset no materializado",
  blocked: "Bloqueado",
  no_permission: "Requiere permisos OData",
  attention: "Requiere atención",
  inactive: "Actualización pendiente",
  no_sources: "Sin datos configurados",
  benchmark_internal: "Referencia interna",
  insufficient_data: "Datos insuficientes",
  blocked_by_sap: "Entidad no expuesta en SAP",
  blocked_by_permission: "Falta permiso SAP",
  pending_approval: "Requiere confirmación",
  partial_fields: "Campos parciales",
  error: "Error operativo",
};

export function readinessTone(status?: ControlRoomStatus): string {
  if (status === "ready" || status === "ok" || status === "benchmark_internal") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  }
  if (status === "partial" || status === "attention" || status === "partial_fields" || status === "pending_approval" || status === "insufficient_data") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  }
  if (status === "stub" || status === "empty" || status === "inactive" || status === "no_sources") {
    return "border-slate-500/40 bg-slate-500/10 text-slate-700 dark:text-slate-300";
  }
  if (status === "blocked" || status === "no_permission" || status === "blocked_by_sap" || status === "blocked_by_permission") {
    return "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300";
  }
  return "border-destructive/40 bg-destructive/10 text-destructive";
}

function ReadinessStatusIcon({ status, className }: { status?: ControlRoomStatus; className: string }) {
  if (status === "ready" || status === "ok" || status === "benchmark_internal") return <CheckCircle2 aria-hidden className={className} />;
  if (status === "partial" || status === "attention" || status === "partial_fields" || status === "pending_approval" || status === "insufficient_data") return <Clock3 aria-hidden className={className} />;
  if (status === "blocked" || status === "no_permission" || status === "blocked_by_sap" || status === "blocked_by_permission") return <LockKeyhole aria-hidden className={className} />;
  if (status === "stub" || status === "empty" || status === "inactive" || status === "no_sources") return <AlertTriangle aria-hidden className={className} />;
  return <XCircle aria-hidden className={className} />;
}

export function ReadinessBadge({
  status,
  label,
  compact = false,
  className,
}: {
  status?: ControlRoomStatus;
  label?: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-medium",
        compact ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
        readinessTone(status),
        className,
      )}
    >
      <ReadinessStatusIcon status={status} className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
      {label || readinessLabels[status || "missing"] || String(status || "missing")}
    </span>
  );
}

export const originLabels: Record<string, string> = {
  rule: "Regla",
  generic_gold_signal: "Gold generico",
  gold_generic: "Gold generico",
  intelligence_signal: "Intelligence",
  intelligence: "Intelligence",
  bayes: "Historial operativo",
  bayesian_calibration: "Historial operativo",
  monte_carlo: "Análisis operativo",
  agent_alert: "Agent",
  agent: "Agent",
  source_health: "Salud fuente",
  source_state: "Salud fuente",
};

function originTone(origin?: string): string {
  if (origin === "generic_gold_signal" || origin === "gold_generic") {
    return "border-cyan-500/40 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300";
  }
  if (origin === "intelligence_signal" || origin === "intelligence") {
    return "border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300";
  }
  if (origin === "bayes" || origin === "bayesian_calibration") {
    return "border-indigo-500/40 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300";
  }
  if (origin === "monte_carlo") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  }
  if (origin === "agent_alert" || origin === "agent") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  }
  if (origin === "source_health" || origin === "source_state") {
    return "border-slate-500/40 bg-slate-500/10 text-slate-700 dark:text-slate-300";
  }
  return "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300";
}

function OriginIcon({ origin, className }: { origin?: string; className: string }) {
  if (origin === "generic_gold_signal" || origin === "gold_generic") return <Database aria-hidden className={className} />;
  if (origin === "intelligence_signal" || origin === "intelligence") return <BrainCircuit aria-hidden className={className} />;
  if (origin === "bayes" || origin === "bayesian_calibration") return <SlidersHorizontal aria-hidden className={className} />;
  if (origin === "monte_carlo") return <Cpu aria-hidden className={className} />;
  if (origin === "agent_alert" || origin === "agent") return <Bot aria-hidden className={className} />;
  return <CheckCircle2 aria-hidden className={className} />;
}

export function OriginBadge({
  origin,
  label,
  compact = false,
  className,
}: {
  origin?: ControlOrigin | string | null;
  label?: string;
  compact?: boolean;
  className?: string;
}) {
  const key = String(origin || "rule");
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-medium",
        compact ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
        originTone(key),
        className,
      )}
    >
      <OriginIcon origin={key} className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
      {label || originLabels[key] || key}
    </span>
  );
}

export function OperationalNotice({
  tone,
  title,
  children,
  className,
}: {
  tone: "error" | "warning" | "info";
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  const Icon = tone === "error" ? XCircle : tone === "warning" ? AlertTriangle : Clock3;
  return (
    <div
      className={cn(
        "rounded-md border p-3 text-sm",
        tone === "error" ? "border-destructive/40 bg-destructive/10 text-destructive" : "",
        tone === "warning" ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300" : "",
        tone === "info" ? "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300" : "",
        className,
      )}
      role={tone === "error" ? "alert" : "status"}
    >
      <div className="flex gap-2">
        <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          <strong>{title}</strong>
          {children ? <div className="mt-1 text-current/85">{children}</div> : null}
        </div>
      </div>
    </div>
  );
}

export function CommandMetric({
  label,
  value,
  detail,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  detail?: string;
  icon?: LucideIcon;
  tone?: "neutral" | "good" | "warning" | "danger";
}) {
  return (
    <article className="min-h-[112px] rounded-xl border bg-card p-4 shadow-sm transition-colors dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_24px_rgba(14,165,233,0.08)]">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
        {Icon ? (
          <span
            className={cn(
              "grid h-9 w-9 place-items-center rounded-md border",
              tone === "good" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "",
              tone === "warning" ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300" : "",
              tone === "danger" ? "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300" : "",
              tone === "neutral" ? "border-cyan-500/20 bg-cyan-500/10 text-cyan-700 dark:text-cyan-200" : "",
            )}
          >
            <Icon aria-hidden className="h-4 w-4" />
          </span>
        ) : null}
      </div>
      <strong className="mt-3 block text-3xl font-semibold tracking-tight text-foreground dark:text-white">{value}</strong>
      {detail ? <p className="mt-1 truncate text-sm text-muted-foreground">{detail}</p> : null}
    </article>
  );
}

export function MiniBar({
  value,
  max,
  label,
  tone = "good",
}: {
  value: number;
  max: number;
  label?: string;
  tone?: "good" | "warning" | "danger" | "neutral";
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div className="space-y-1">
      {label ? (
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{label}</span>
          <span>{value}/{max}</span>
        </div>
      ) : null}
      <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <span
          className={cn(
            "block h-full rounded-full",
            tone === "good" ? "bg-emerald-500" : "",
            tone === "warning" ? "bg-amber-500" : "",
            tone === "danger" ? "bg-destructive" : "",
            tone === "neutral" ? "bg-primary" : "",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/**
 * Sparkline: mini tendencia (SVG inline, sin librerias ni fetch). Renderiza una
 * serie numerica ya calculada por el backend (fuente unica); ignora huecos null.
 */
export function Sparkline({
  values,
  label,
  format,
  tone = "good",
}: {
  values: Array<number | null>;
  label?: string;
  format?: (value: number) => string;
  tone?: "good" | "warning" | "danger" | "neutral";
}) {
  const points = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  const width = 120;
  const height = 28;
  const stroke =
    tone === "warning"
      ? "#f59e0b"
      : tone === "danger"
        ? "#ef4444"
        : tone === "neutral"
          ? "#6366f1"
          : "#10b981";
  const min = points.length ? Math.min(...points) : 0;
  const max = points.length ? Math.max(...points) : 0;
  const span = max - min || 1;
  const step = points.length > 1 ? width / (points.length - 1) : 0;
  const polyline = points
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / span) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = points.length ? points[points.length - 1] : null;
  return (
    <div className="space-y-1">
      {label ? (
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{label}</span>
          <span className="tabular-nums text-foreground/80">
            {last == null ? "—" : format ? format(last) : String(last)}
          </span>
        </div>
      ) : null}
      {points.length >= 2 ? (
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-8 w-full"
          preserveAspectRatio="none"
          role="img"
          aria-label={label ? `Tendencia de ${label}` : "Tendencia"}
        >
          <polyline
            points={polyline}
            fill="none"
            stroke={stroke}
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      ) : (
        <div className="flex h-8 items-center text-xs text-muted-foreground">
          Sin historia suficiente
        </div>
      )}
    </div>
  );
}
