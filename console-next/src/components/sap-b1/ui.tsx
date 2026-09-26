"use client";

import { AlertTriangle, CheckCircle2, CircleHelp, Clock3, Loader2, RefreshCcw } from "lucide-react";
import type { ReactNode } from "react";

import { isApiError } from "@/lib/api";
import type { AreaColor, CheckState, MetricState } from "@/lib/sap-b1/present";
import { AREA_COLOR_LABELS, METRIC_STATE_LABELS } from "@/lib/sap-b1/present";
import { cn } from "@/lib/utils";

export function errorText(error: unknown): string {
  if (isApiError(error)) {
    if (error.status === 401) return "Tu sesión expiró; vuelve a iniciar sesión.";
    if (error.status === 403) return "Sin permiso para esta sección o el cartucho no está habilitado en este workspace.";
    if (error.status === 404) return "Sin datos todavía.";
    if (error.status === 503) return "El servicio no está disponible por ahora.";
    return error.message;
  }
  return error instanceof Error && error.message ? error.message : "No se pudo completar la consulta.";
}

export function Panel({
  title,
  eyebrow,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  eyebrow?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-xl border bg-card p-4 shadow-sm dark:border-sky-400/20 dark:bg-[#081423]", className)}>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          {eyebrow ? (
            <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700 dark:text-cyan-300/80">{eyebrow}</p>
          ) : null}
          <h2 className="text-lg font-semibold text-foreground dark:text-white">{title}</h2>
          {description ? <div className="mt-1 text-sm text-muted-foreground">{description}</div> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function ActionButton({
  children,
  onClick,
  disabled,
  busy,
  variant = "secondary",
  type = "button",
  ariaLabel,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: "primary" | "secondary" | "danger";
  type?: "button" | "submit";
  ariaLabel?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      aria-label={ariaLabel}
      className={cn(
        "inline-flex min-h-[36px] items-center justify-center gap-1.5 whitespace-nowrap rounded-md border px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60",
        variant === "primary" && "border-primary bg-primary text-primary-foreground hover:opacity-90",
        variant === "secondary" && "bg-background text-foreground hover:bg-muted dark:border-sky-400/20 dark:bg-[#06111f] dark:hover:bg-white/5",
        variant === "danger" && "border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/20",
      )}
    >
      {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
      {children}
    </button>
  );
}

export function RefreshButton({ onClick, busy, label = "Actualizar" }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <ActionButton onClick={onClick} busy={busy} ariaLabel={label}>
      {busy ? null : <RefreshCcw aria-hidden className="h-4 w-4" />}
      <span>{label}</span>
    </ActionButton>
  );
}

export function LoadingBlock({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-dashed p-4 text-sm text-muted-foreground" role="status" aria-busy="true">
      <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

export function Notice({
  tone,
  title,
  children,
  action,
}: {
  tone: "error" | "warning" | "info" | "empty";
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex flex-col gap-2 rounded-md border p-3 text-sm sm:flex-row sm:items-start sm:justify-between",
        tone === "error" && "border-destructive/40 bg-destructive/10 text-destructive",
        tone === "warning" && "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200",
        tone === "info" && "border-blue-500/40 bg-blue-500/10 text-blue-800 dark:text-blue-200",
        tone === "empty" && "border-dashed text-muted-foreground",
      )}
    >
      <div className="flex gap-2">
        {tone === "empty" ? <CircleHelp aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />}
        <div className="min-w-0">
          <strong className="font-semibold">{title}</strong>
          {children ? <div className="mt-1 break-words opacity-90">{children}</div> : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function QueryError({ error, onRetry, title = "No se pudo cargar" }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <Notice tone="error" title={title} action={onRetry ? <ActionButton onClick={onRetry}>Reintentar</ActionButton> : undefined}>
      {errorText(error)}
    </Notice>
  );
}

const PILL_TONES = {
  good: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  danger: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  neutral: "border-slate-400/40 bg-slate-500/10 text-slate-700 dark:text-slate-300",
} as const;

export type PillTone = keyof typeof PILL_TONES;

export function Pill({ tone, children, className }: { tone: PillTone; children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", PILL_TONES[tone], className)}>
      {children}
    </span>
  );
}

export function CheckPill({ state }: { state: CheckState }) {
  if (state === "ok") return <Pill tone="good"><CheckCircle2 aria-hidden className="h-3.5 w-3.5" />Listo</Pill>;
  if (state === "pendiente") return <Pill tone="warning"><Clock3 aria-hidden className="h-3.5 w-3.5" />Pendiente</Pill>;
  return <Pill tone="neutral"><CircleHelp aria-hidden className="h-3.5 w-3.5" />Sin dato</Pill>;
}

export function MetricStatePill({ state }: { state: MetricState }) {
  const tone: PillTone = state === "ready" ? "good" : state === "degraded" ? "warning" : "neutral";
  return <Pill tone={tone}>{METRIC_STATE_LABELS[state]}</Pill>;
}

export const AREA_DOT: Record<AreaColor, string> = {
  rojo: "bg-red-500",
  amarillo: "bg-amber-400",
  verde: "bg-emerald-500",
  sin_datos: "bg-slate-400",
};

export function AreaPill({ color }: { color: AreaColor }) {
  const tone: PillTone = color === "rojo" ? "danger" : color === "amarillo" ? "warning" : color === "verde" ? "good" : "neutral";
  return (
    <Pill tone={tone}>
      <span aria-hidden className={cn("h-2 w-2 rounded-full", AREA_DOT[color])} />
      {AREA_COLOR_LABELS[color]}
    </Pill>
  );
}

export function Fact({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-sm font-medium text-foreground dark:text-white">{value}</dd>
    </div>
  );
}

export function TableShell({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0" role="region" aria-label={label} tabIndex={0}>
      <table className="w-full min-w-[640px] border-collapse text-left text-sm">{children}</table>
    </div>
  );
}

export const TH = "border-b px-2 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground dark:border-sky-400/15";
export const TD = "border-b px-2 py-2 align-top dark:border-sky-400/10";
export const INPUT = "min-h-[36px] w-full rounded-md border bg-background px-2.5 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:border-sky-400/20 dark:bg-[#06111f]";
