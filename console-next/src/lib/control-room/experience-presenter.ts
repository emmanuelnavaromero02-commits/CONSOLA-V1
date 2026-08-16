import { isApiError } from "@/lib/api";
import type {
  ControlRoomExperienceV2,
  ExperienceDecision,
  ExperienceMetric,
} from "@/lib/control-room/experience-contract";

export type ExperienceErrorKind = "forbidden" | "not-found" | "unavailable";

const decisionLabels: Record<ExperienceDecision["status"], string> = {
  decision_created: "Decisión registrada",
  approved: "Decisión aprobada",
  resolved: "Decisión resuelta",
};

export function experienceErrorKind(error: unknown): ExperienceErrorKind {
  if (isApiError(error) && error.status === 403) return "forbidden";
  if (isApiError(error) && error.status === 404) return "not-found";
  return "unavailable";
}

export function decisionLabel(decision: ExperienceDecision): string {
  return decisionLabels[decision.status];
}

export function latestObservedAt(
  experience: ControlRoomExperienceV2,
): string | null {
  let latest: string | null = null;
  let latestMs = Number.NEGATIVE_INFINITY;
  for (const section of experience.sections) {
    for (const fact of section.facts) {
      const ms = Date.parse(fact.observed_at);
      if (!Number.isNaN(ms) && ms > latestMs) {
        latestMs = ms;
        latest = fact.observed_at;
      }
    }
  }
  return latest;
}

export function formatRelativeFromNow(value: string): string {
  const then = Date.parse(value);
  if (Number.isNaN(then)) return "";
  const rtf = new Intl.RelativeTimeFormat("es-MX", { numeric: "auto" });
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "hace un momento";
  if (minutes < 60) return rtf.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours < 24) return rtf.format(-hours, "hour");
  return rtf.format(-Math.round(hours / 24), "day");
}

export function formatObservedAt(value: string): string {
  return new Intl.DateTimeFormat("es-MX", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

export function formatMetric(metric: ExperienceMetric): string {
  const value = new Intl.NumberFormat("es-MX", {
    maximumFractionDigits: 20,
    useGrouping: true,
  }).format(metric.value);
  if (metric.unit === "%") return `${value}%`;
  if (metric.unit) return `${value} ${metric.unit}`;
  if (metric.kind === "percentage") return `${value}%`;
  return value;
}
