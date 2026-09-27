import { Info } from "lucide-react";

import type {
  ExperienceActionKind,
  ExperienceFactV2,
} from "@/lib/control-room/experience-contract";
import { formatMetric, formatObservedAt } from "@/lib/control-room/experience-presenter";

export const FALLBACK_READING_LABEL = "Lectura automática (sin análisis narrativo)";
export const FALLBACK_RECOMMENDATION =
  "Revisa la evidencia y decide si aprobar una excepción, ajustarlo en Estudio o crear una propuesta de decisión.";
export const NEUTRAL_RECOMMENDATION = "Revisa la evidencia antes de decidir.";

const recommendationPart: Partial<Record<ExperienceActionKind, string>> = {
  exception_approval: "aprobar una excepción",
  studio_adjustment: "ajustarlo en Estudio",
  decision_proposal: "crear una propuesta de decisión",
  followup_task: "preparar un seguimiento operativo",
};

function joinOptions(parts: readonly string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} o ${parts[parts.length - 1]}`;
}

export function fallbackRecommendation(fact: ExperienceFactV2): string {
  const kinds = new Set(fact.actions.map((action) => action.kind));
  const parts = (
    ["exception_approval", "studio_adjustment", "decision_proposal", "followup_task"] as const
  )
    .filter((kind) => kinds.has(kind))
    .map((kind) => recommendationPart[kind] as string);
  if (parts.length === 0) return NEUTRAL_RECOMMENDATION;
  return `Revisa la evidencia y decide si ${joinOptions(parts)}.`;
}

const kindLabel: Record<ExperienceFactV2["kind"], string> = {
  anomaly: "Anomalía",
  signal: "Señal",
  alert: "Alerta",
  kpi: "Indicador",
};

const severityLabel: Record<ExperienceFactV2["severity"], string> = {
  critical: "crítica",
  high: "alta",
  medium: "media",
  low: "baja",
};

export function composeFallbackReading(
  fact: ExperienceFactV2,
  sectionTitle: string | null,
): string[] {
  const section = sectionTitle?.trim() ? ` en ${sectionTitle.trim()}` : "";
  const entity = fact.entity_label?.trim() ? ` para ${fact.entity_label.trim()}` : "";
  const lines = [
    `${kindLabel[fact.kind]} de severidad ${severityLabel[fact.severity]}${section}${entity}.`,
  ];
  if (fact.metric) lines.push(`${fact.metric.name}: ${formatMetric(fact.metric)}.`);
  lines.push(`Dato del ${formatObservedAt(fact.observed_at)}.`);
  return lines;
}

export function FactFallbackReading({
  fact,
  sectionTitle,
}: {
  fact: ExperienceFactV2;
  sectionTitle: string | null;
}) {
  return (
    <div
      role="group"
      aria-label={FALLBACK_READING_LABEL}
      className="mt-5 space-y-3 border-t pt-4"
    >
      <p className="text-xs font-medium text-muted-foreground">{FALLBACK_READING_LABEL}</p>
      <p className="break-words text-sm leading-6 text-card-foreground">
        {composeFallbackReading(fact, sectionTitle).join(" ")}
      </p>
      <div>
        <p className="text-xs font-medium text-muted-foreground">Recomendación</p>
        <p className="mt-1 break-words text-sm leading-6 text-card-foreground">
          {fallbackRecommendation(fact)}
        </p>
      </div>
      <p className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="h-4 w-4 shrink-0" />
        Solo recomendación: nada se aplica automáticamente.
      </p>
    </div>
  );
}
