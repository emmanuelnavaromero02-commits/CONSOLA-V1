import { Loader2, Sparkles } from "lucide-react";

import type { AutoProfileStatus } from "@/lib/data/types";

function tables(count: number): string {
  return count === 1 ? "1 tabla" : `${count} tablas`;
}

export function AutoCatalogStatus({ status }: { status: AutoProfileStatus | null }) {
  if (!status) return null;
  if (status.status === "working" && status.pending > 0) {
    return (
      <p role="status" aria-live="polite" className="inline-flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
        El Copiloto está documentando {tables(status.pending)}…
      </p>
    );
  }
  if (status.processed > 0) {
    return (
      <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
        <Sparkles aria-hidden className="h-4 w-4 text-violet-600" />
        El Copiloto documentó {tables(status.processed)} en esta visita.
      </p>
    );
  }
  return null;
}
