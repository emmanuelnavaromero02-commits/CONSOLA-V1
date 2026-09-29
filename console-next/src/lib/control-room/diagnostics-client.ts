import { api, isApiError } from "@/lib/api";

export const CONTROL_ROOM_DIAGNOSTICS_ENDPOINT = "/api/control-room/diagnostics";

export interface DiagnosticSourceView {
  domain: string | null;
  status: string;
  reason: string | null;
  count: number | null;
  operationallyReady: boolean;
}

export interface DiagnosticInstallationView {
  label: string | null;
  category: string | null;
  status: string;
}

export interface ControlRoomDiagnosticsView {
  sources: DiagnosticSourceView[];
  installations: DiagnosticInstallationView[];
}

const SOURCE_STATUS_LABELS: Record<string, string> = {
  ok: "Con datos",
  empty: "Sin datos",
  missing: "Sin datos",
  unavailable: "No disponible",
  invalid_schema: "Requiere revisión",
  blocked: "Bloqueada",
  no_permission: "Sin acceso",
};

const INSTALLATION_STATUS_LABELS: Record<string, string> = {
  requested: "Solicitada",
  installing: "Instalándose",
  pending_connection: "Pendiente de conexión",
  waiting_credentials: "Esperando credenciales",
  ready: "Lista",
  failed: "Con fallo",
  paused: "Pausada",
  revoked: "Revocada",
  expired: "Expirada",
  suspended: "Suspendida",
  disabled: "Deshabilitada",
};

export function diagnosticSourceStatusLabel(status: string): string {
  return SOURCE_STATUS_LABELS[status] ?? "Sin información";
}

export function diagnosticInstallationStatusLabel(status: string): string {
  return INSTALLATION_STATUS_LABELS[status] ?? "Sin información";
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function parseSources(value: unknown): DiagnosticSourceView[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const status = asText(record.status);
    if (!status) return [];
    return [{
      domain: asText(record.domain),
      status,
      reason: asText(record.reason),
      count: asCount(record.count),
      operationallyReady: record.operationally_ready === true,
    }];
  });
}

function parseInstallations(value: unknown): DiagnosticInstallationView[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const status = asText(record.status);
    if (!status) return [];
    return [{
      label: asText(record.label),
      category: asText(record.category),
      status,
    }];
  });
}

export function isForbiddenDiagnostics(error: unknown): boolean {
  return isApiError(error) && (error.status === 401 || error.status === 403);
}

export async function getControlRoomDiagnostics(): Promise<ControlRoomDiagnosticsView> {
  const { data } = await api.get<unknown>(CONTROL_ROOM_DIAGNOSTICS_ENDPOINT);
  const record = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  return {
    sources: parseSources(record.sources),
    installations: parseInstallations(record.installations),
  };
}
