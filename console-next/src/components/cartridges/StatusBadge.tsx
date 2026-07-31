import { cn } from "@/lib/utils";

export type ConnectionStatus =
  | "connected"
  | "untested"
  | "unconfigured"
  | "failed"
  | "stale"
  | "very_stale";

const LABEL: Record<ConnectionStatus, string> = {
  connected:    "Conectado",
  untested:     "Sin probar",
  unconfigured: "Sin configurar",
  failed:       "Falló",
  stale:        "Datos antiguos",
  very_stale:   "Datos muy antiguos",
};

const ICON: Record<ConnectionStatus, string> = {
  connected:    "🟢",
  untested:     "🟡",
  unconfigured: "⚪",
  failed:       "🔴",
  stale:        "🟠",
  very_stale:   "🟠",
};

const TONE: Record<ConnectionStatus, string> = {
  connected:    "bg-success/15 text-success",
  untested:     "bg-warning/15 text-warning",
  unconfigured: "bg-muted     text-muted-foreground",
  failed:       "bg-destructive/15 text-destructive",
  stale:        "bg-warning/15 text-warning",
  very_stale:   "bg-destructive/15 text-destructive",
};

function formatAge(ageHours: number): string {
  if (ageHours < 1) return "hace < 1 h";
  if (ageHours < 48) return `hace ~${Math.round(ageHours)} h`;
  return `hace ~${Math.round(ageHours / 24)} d`;
}

/**
 * Visual status pill for a cartridge.
 *
 * States:
 *   connected    — recent test_connection returned ok / fresh data
 *   untested     — credentials exist, no test recorded
 *   unconfigured — credentials absent
 *   failed       — last test_connection returned !ok
 *   stale        — data exists but is old (freshness, NOT a connection failure)
 *   very_stale   — data exists but is very old (freshness, NOT a connection failure)
 *
 * `ageHours` (optional) surfaces how old the data is ("hace ~N h")
 * so age is communicated with text, never colour alone.
 */
export function StatusBadge({
  status,
  ageHours,
}: {
  status: ConnectionStatus;
  ageHours?: number | null;
}) {
  const age = typeof ageHours === "number" && Number.isFinite(ageHours) ? formatAge(ageHours) : null;
  const label = age ? `${LABEL[status]} (${age})` : LABEL[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        TONE[status],
      )}
      aria-label={`Estado: ${label}`}
      title={`Estado: ${label}`}
    >
      <span aria-hidden>{ICON[status]}</span>
      {label}
    </span>
  );
}
