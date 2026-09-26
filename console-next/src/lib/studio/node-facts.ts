import type { AnalyticsApp } from "@/lib/admin-surfaces";
import { dailyParts } from "@/lib/control-room/wisdom-bit-monitors";
import type { DatasetDetail, DatasetSummary, PipelineEntity } from "@/lib/monitor/types";

export type NodeStatus = "sincronizado" | "en_proceso" | "requiere_revision";

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  sincronizado: "Sincronizado",
  en_proceso: "En proceso",
  requiere_revision: "Requiere revisión",
};

export const NODE_STATUS_TONE: Record<NodeStatus, string> = {
  sincronizado: "border-success/30 bg-success/10 text-success",
  en_proceso: "border-warning/30 bg-warning/10 text-warning",
  requiere_revision: "border-destructive/30 bg-destructive/10 text-destructive",
};

export interface StatusFact {
  status: NodeStatus;
  reason: string | null;
}

const IN_PROGRESS = new Set(["running", "queued", "scheduled", "up_for_retry", "restarting", "deferred"]);
const SYNCED = new Set(["success", "done"]);
const NEEDS_REVIEW = new Set(["failed", "error", "upstream_failed", "partial"]);

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function runStatus(status: string | null | undefined): NodeStatus | null {
  const value = String(status ?? "").trim().toLowerCase();
  if (IN_PROGRESS.has(value)) return "en_proceso";
  if (SYNCED.has(value)) return "sincronizado";
  if (NEEDS_REVIEW.has(value)) return "requiere_revision";
  return null;
}

export function datasetStatus(
  summary: DatasetSummary | null | undefined,
  detail: DatasetDetail | null | undefined,
): StatusFact | null {
  if (detail?.status === "unavailable") {
    return { status: "requiere_revision", reason: text(detail.error) };
  }
  const stale = summary?.is_stale ?? detail?.is_stale;
  if (stale === true) {
    return { status: "requiere_revision", reason: text(summary?.staleness_reason ?? detail?.staleness_reason) };
  }
  const lastRefresh = text(summary?.last_refresh ?? detail?.last_refresh);
  if (lastRefresh && stale === false) return { status: "sincronizado", reason: null };
  return null;
}

export function entityStatus(entry: PipelineEntity | null | undefined): StatusFact | null {
  const run = entry?.last_run ?? entry?.last_job;
  const status = runStatus(run?.status);
  if (!status) return null;
  return { status, reason: status === "requiere_revision" ? text(run?.error) ?? text(run?.message) : null };
}

export function aggregateStatus(facts: Array<StatusFact | null>): StatusFact | null {
  if (!facts.length) return null;
  const review = facts.find((fact) => fact?.status === "requiere_revision");
  if (review) return review;
  const running = facts.find((fact) => fact?.status === "en_proceso");
  if (running) return running;
  return facts.every((fact) => fact?.status === "sincronizado") ? { status: "sincronizado", reason: null } : null;
}

export function scheduleText(entity: { trigger_type?: string | null; cron_expression?: string | null } | null | undefined): string | null {
  if (!entity) return null;
  const cron = text(entity.cron_expression);
  if (!cron) return String(entity.trigger_type ?? "").trim().toLowerCase() === "manual" ? "Manual (sin programación)" : null;
  const daily = dailyParts(cron);
  if (daily) {
    const time = `${String(daily.hour).padStart(2, "0")}:${String(daily.minute).padStart(2, "0")}`;
    return `Diaria a las ${time} UTC (cron ${cron})`;
  }
  return `Programada en UTC (cron ${cron})`;
}

export function appsUsing(names: Iterable<string>, apps: AnalyticsApp[] | null | undefined): AnalyticsApp[] {
  const wanted = new Set([...names].filter(Boolean));
  if (!wanted.size) return [];
  return (apps ?? [])
    .filter((app) => (app.datasets_used ?? []).some((name) => wanted.has(name)))
    .sort((a, b) => String(a.title || a.name).localeCompare(String(b.title || b.name), "es"));
}
