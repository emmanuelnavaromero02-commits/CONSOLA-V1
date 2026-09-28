import { pipelineReasonCopy } from "./pipeline-error-copy";

const STATUS_COPY: Record<string, string> = {
  queued: "En cola",
  running: "En curso",
  success: "Completada",
  partial: "Completada con advertencias",
  failed: "Fallida",
  blocked: "Bloqueada",
  skipped: "Omitida",
  skipped_explicit: "Omitida por configuración",
};

const STEP_STATUS_COPY: Record<string, string> = {
  queued: "En cola",
  running: "En curso",
  success: "Completado",
  partial: "Con advertencias",
  failed: "Falló",
  blocked: "Bloqueado",
  skipped: "Omitido",
  skipped_explicit: "Omitido por configuración",
};

const SYNC_REASON_COPY: Record<string, string> = {
  connection_check_failed: "Credenciales no válidas o incompletas en la Bóveda de Accesos.",
  connection_probe_failed: "No se pudo validar la conexión con el origen.",
  airflow_trigger_failed: "No se pudo iniciar la extracción en el orquestador.",
  sync_stale_timeout: "La sincronización agotó el tiempo de espera en el orquestador; inicia una nueva.",
};

export function syncStatusCopy(status: string | null | undefined): string {
  if (!status) return "Sin información";
  return Object.prototype.hasOwnProperty.call(STATUS_COPY, status) ? STATUS_COPY[status] : "Sin información";
}

export function syncStepStatusCopy(status: string | null | undefined): string {
  if (!status) return "Sin información";
  return Object.prototype.hasOwnProperty.call(STEP_STATUS_COPY, status) ? STEP_STATUS_COPY[status] : "Sin información";
}

export function syncCardTitle(status: string | null | undefined): string {
  switch (status) {
    case "success":
      return "Sincronización completa";
    case "partial":
      return "Completada con advertencias";
    case "failed":
      return "La sincronización falló";
    case "blocked":
      return "Sincronización bloqueada";
    case "skipped":
    case "skipped_explicit":
      return "Sincronización omitida";
    case "queued":
    case "running":
      return "Sincronización en curso";
    default:
      return "Estado de la sincronización";
  }
}

export function syncReasonCopy(reason: unknown): string | null {
  if (typeof reason !== "string" || !reason) return null;
  if (Object.prototype.hasOwnProperty.call(SYNC_REASON_COPY, reason)) return SYNC_REASON_COPY[reason];
  return pipelineReasonCopy(reason);
}
