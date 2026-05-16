import { cn } from "@/lib/utils";

export type ConnectionStatus = "connected" | "untested" | "unconfigured" | "failed";

const LABEL: Record<ConnectionStatus, string> = {
  connected:    "Conectado",
  untested:     "Sin probar",
  unconfigured: "Sin configurar",
  failed:       "Falló",
};

const ICON: Record<ConnectionStatus, string> = {
  connected:    "🟢",
  untested:     "🟡",
  unconfigured: "⚪",
  failed:       "🔴",
};

const TONE: Record<ConnectionStatus, string> = {
  connected:    "bg-success/15 text-success",
  untested:     "bg-warning/15 text-warning",
  unconfigured: "bg-muted     text-muted-foreground",
  failed:       "bg-destructive/15 text-destructive",
};

/**
 * v1.44.3 — visual status pill for a cartridge.
 *
 * The 4 states map 1:1 to the brief:
 *   connected    — recent test_connection returned ok
 *   untested     — credentials exist, no test recorded
 *   unconfigured — credentials absent
 *   failed       — last test_connection returned !ok
 */
export function StatusBadge({ status }: { status: ConnectionStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        TONE[status],
      )}
      aria-label={`Estado: ${LABEL[status]}`}
    >
      <span aria-hidden>{ICON[status]}</span>
      {LABEL[status]}
    </span>
  );
}
