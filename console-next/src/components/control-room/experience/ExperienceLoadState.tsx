import { AlertCircle, Loader2, RefreshCcw } from "lucide-react";

import type { ExperienceErrorKind } from "@/lib/control-room/experience-presenter";

type LoadState = "loading" | "empty" | ExperienceErrorKind;

const stateCopy: Record<Exclude<LoadState, "loading">, string> = {
  empty: "No hay observaciones empresariales para mostrar.",
  forbidden: "No tienes acceso a esta vista.",
  "not-found": "Esta vista no está disponible.",
  unavailable: "No se pudo cargar la información empresarial.",
};

export function ExperienceLoadState({
  state,
  onRetry,
}: {
  state: LoadState;
  onRetry?: () => void;
}) {
  if (state === "loading") {
    return (
      <div className="flex min-h-64 items-center justify-center" role="status">
        <Loader2 aria-hidden className="mr-3 h-5 w-5 animate-spin text-primary" />
        <span className="text-sm text-muted-foreground">Cargando información empresarial</span>
      </div>
    );
  }

  return (
    <div
      className="flex min-h-64 flex-col items-center justify-center px-4 text-center"
      role={state === "empty" ? "status" : "alert"}
    >
      <AlertCircle aria-hidden className="mb-3 h-6 w-6 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">{stateCopy[state]}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-md border bg-card px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCcw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      ) : null}
    </div>
  );
}
