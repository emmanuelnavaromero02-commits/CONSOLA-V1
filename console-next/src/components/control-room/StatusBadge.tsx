import type { LucideIcon } from "lucide-react";
import { AlertTriangle, CheckCircle2, Clock3, LockKeyhole, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import type { DataReadiness, SourceRollup, SourceState } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

export type ControlRoomStatus = DataReadiness | SourceState | SourceRollup | "error";

export const readinessLabels: Record<string, string> = {
  ready: "Operativa",
  ok: "Operativa",
  partial: "Parcial",
  stub: "Stub",
  empty: "Sin datos",
  missing: "Faltante",
  unavailable: "No disponible",
  invalid_schema: "Schema invalido",
  blocked: "Bloqueada",
  no_permission: "Sin permiso",
  attention: "Atencion",
  inactive: "Inactiva",
  no_sources: "Sin fuentes",
  error: "Error",
};

export function readinessTone(status?: ControlRoomStatus): string {
  if (status === "ready" || status === "ok") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  }
  if (status === "partial" || status === "attention") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  }
  if (status === "stub" || status === "empty" || status === "inactive" || status === "no_sources") {
    return "border-slate-500/40 bg-slate-500/10 text-slate-700 dark:text-slate-300";
  }
  if (status === "blocked" || status === "no_permission") {
    return "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300";
  }
  return "border-destructive/40 bg-destructive/10 text-destructive";
}

export function readinessIcon(status?: ControlRoomStatus): LucideIcon {
  if (status === "ready" || status === "ok") return CheckCircle2;
  if (status === "partial" || status === "attention") return Clock3;
  if (status === "blocked" || status === "no_permission") return LockKeyhole;
  if (status === "stub" || status === "empty" || status === "inactive" || status === "no_sources") return AlertTriangle;
  return XCircle;
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
  const Icon = readinessIcon(status);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-medium",
        compact ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
        readinessTone(status),
        className,
      )}
    >
      <Icon aria-hidden className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
      {label || readinessLabels[status || "missing"] || String(status || "missing")}
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
    <article className="min-h-[112px] rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-semibold uppercase text-muted-foreground">{label}</span>
        {Icon ? (
          <span
            className={cn(
              "grid h-9 w-9 place-items-center rounded-md border",
              tone === "good" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600" : "",
              tone === "warning" ? "border-amber-500/30 bg-amber-500/10 text-amber-600" : "",
              tone === "danger" ? "border-destructive/30 bg-destructive/10 text-destructive" : "",
              tone === "neutral" ? "bg-background text-muted-foreground" : "",
            )}
          >
            <Icon aria-hidden className="h-4 w-4" />
          </span>
        ) : null}
      </div>
      <strong className="mt-3 block text-3xl font-semibold tracking-tight">{value}</strong>
      {detail ? <p className="mt-1 text-sm text-muted-foreground">{detail}</p> : null}
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
      <div className="h-2 overflow-hidden rounded-full bg-muted">
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
