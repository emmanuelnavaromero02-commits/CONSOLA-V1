import { basisLabel, confidenceLabel } from "@/lib/catalog/classifications";
import { cn } from "@/lib/utils";

export const COPILOT_BADGE_TEXT = "✨ Autocatalogado por Copiloto";

interface CopilotBadgeProps {
  confidence?: number | null;
  basis?: readonly string[] | null;
  status?: string | null;
  className?: string;
}

export function copilotTooltip({ confidence, basis, status }: Omit<CopilotBadgeProps, "className">): string {
  const lines = [
    "Inferido por reglas deterministas del Copiloto; revisable por un DBA.",
    confidenceLabel(confidence),
    ...(basis ?? []).map(basisLabel),
    status === "partial" ? "Perfil parcial: no se pudieron leer todos los datos." : null,
  ].filter((line): line is string => Boolean(line));
  return [...new Set(lines)].join("\n");
}

export function CopilotBadge({ confidence, basis, status, className }: CopilotBadgeProps) {
  const tooltip = copilotTooltip({ confidence, basis, status });
  return (
    <span
      title={tooltip}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-violet-300 bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-800 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-200",
        className,
      )}
    >
      {COPILOT_BADGE_TEXT}
      <span className="sr-only">. {tooltip.replace(/\n/g, ". ")}</span>
    </span>
  );
}
