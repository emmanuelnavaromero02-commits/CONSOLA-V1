import { isApiError } from "@/lib/api";
import type {
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
