export const NO_STATUS_COPY = "Sin información";

const BASE_STATUS_COPY: Record<string, string> = {
  ok: "Completado",
  success: "Completado",
  succeeded: "Completado",
  done: "Completado",
  complete: "Completado",
  completed: "Completado",
  executed: "Ejecutado",
  error: "Con error",
  failed: "Falló",
  failure: "Falló",
  running: "En curso",
  in_progress: "En curso",
  queued: "En cola",
  pending: "Pendiente",
  planning: "Planeando",
  waiting_approval: "Requiere aprobación",
  awaiting_approval: "Requiere aprobación",
  requires_approval: "Requiere aprobación",
  pending_approval: "Requiere aprobación",
  approved: "Aprobado",
  cancelled: "Cancelado",
  canceled: "Cancelado",
  partial: "Parcial",
  skipped: "Omitido",
  active: "Activo",
  inactive: "Inactivo",
  paused: "Pausado",
  ready: "Listo",
  available: "Disponible",
  blocked: "Bloqueado",
  degraded: "Degradado",
  fresh: "Al día",
  stale: "Desactualizado",
  expired: "Expirado",
  suspended: "Suspendido",
  revoked: "Revocado",
  requested: "Solicitado",
};

export function statusCopy(status: string | null | undefined, overrides?: Record<string, string>): string {
  const key = String(status ?? "").trim().toLowerCase();
  if (!key) return NO_STATUS_COPY;
  if (overrides && Object.prototype.hasOwnProperty.call(overrides, key)) return overrides[key];
  return Object.prototype.hasOwnProperty.call(BASE_STATUS_COPY, key) ? BASE_STATUS_COPY[key] : NO_STATUS_COPY;
}

export const RUN_STATUS_COPY: Record<string, string> = {
  ok: "Completada",
  success: "Completada",
  succeeded: "Completada",
  done: "Completada",
  completed: "Completada",
  error: "Falló",
  failed: "Falló",
  failure: "Falló",
  running: "En curso",
  in_progress: "En curso",
  queued: "En cola",
  pending: "En cola",
  cancelled: "Cancelada",
  canceled: "Cancelada",
  partial: "Completada con advertencias",
};

export function runStatusCopy(status: string | null | undefined): string {
  return statusCopy(status, RUN_STATUS_COPY);
}

export const INSTALLATION_STATUS_COPY: Record<string, string> = {
  available: "Disponible",
  active: "Activa",
  ready: "Activa",
  pending_approval: "Pendiente",
  requested: "Solicitada",
  pending_connection: "Pendiente de conexión",
  waiting_credentials: "Requiere credenciales",
  failed: "Falló",
  paused: "Pausada",
  revoked: "Revocada",
  expired: "Expirada",
  suspended: "Suspendida",
};

export function installationStatusCopy(status: string | null | undefined): string {
  return statusCopy(status, INSTALLATION_STATUS_COPY);
}
