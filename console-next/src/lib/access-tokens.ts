import { api } from "@/lib/api";

export type AccessTokenScope = "lectura" | "acciones";

export interface AccessTokenWorkspace {
  id: string;
  nombre: string | null;
}

export interface AccessToken {
  id: string;
  nombre: string;
  espacio_de_trabajo: AccessTokenWorkspace;
  token_prefix: string;
  alcances: string[];
  creado_en: string | null;
  vence_en: string | null;
  ultimo_uso_en: string | null;
  revocado_en: string | null;
  motivo_revocacion: string | null;
  estado: string;
}

export interface AccessTokensResponse {
  tokens: AccessToken[];
  puede_crear: boolean;
  alcances_permitidos: AccessTokenScope[];
  limite_activos: number;
  tokens_activos: number;
  dias_permitidos: number[];
  dias_predeterminados: number;
}

export interface CreateAccessTokenPayload {
  nombre: string;
  alcance: AccessTokenScope;
  dias: number;
}

export interface CreatedAccessToken {
  token: string;
  token_info: AccessToken;
}

export const ACCESS_TOKENS_PATH = "/api/me/access-tokens";
export const SIN_INFORMACION = "Sin información";

const STATUS_LABELS: Record<string, string> = {
  activo: "Activo",
  vencido: "Vencido",
  revocado: "Revocado",
  sin_acceso: "Sin acceso al espacio de trabajo",
};

const REVOCABLE_STATUSES = new Set(["activo", "sin_acceso"]);

const REVOCATION_LABELS: Record<string, string> = {
  usuario: "revocado por ti",
  administrador: "revocado por un administrador",
  credenciales_restablecidas: "revocado al restablecer la contraseña",
};

export async function listAccessTokens(): Promise<AccessTokensResponse> {
  const { data } = await api.get<AccessTokensResponse>(ACCESS_TOKENS_PATH);
  return data;
}

export async function createAccessToken(payload: CreateAccessTokenPayload): Promise<CreatedAccessToken> {
  const { data } = await api.post<CreatedAccessToken>(ACCESS_TOKENS_PATH, payload);
  return data;
}

export async function revokeAccessToken(id: string): Promise<{ revocado: boolean; id: string }> {
  const { data } = await api.delete<{ revocado: boolean; id: string }>(
    `${ACCESS_TOKENS_PATH}/${encodeURIComponent(id)}`,
  );
  return data;
}

export function scopeLabel(alcances: readonly string[] | null | undefined): string {
  if (!alcances || alcances.length === 0) return SIN_INFORMACION;
  return alcances.includes("acciones") ? "Lectura y acciones" : "Solo lectura";
}

export function statusLabel(token: Pick<AccessToken, "estado" | "motivo_revocacion">): string {
  const base = STATUS_LABELS[token.estado] ?? SIN_INFORMACION;
  if (token.estado === "revocado" && token.motivo_revocacion) {
    const reason = REVOCATION_LABELS[token.motivo_revocacion];
    return reason ? `${base} (${reason})` : base;
  }
  return base;
}

export function canRevoke(token: Pick<AccessToken, "estado">): boolean {
  return REVOCABLE_STATUSES.has(token.estado);
}

export function formatDate(value: string | null | undefined, empty = SIN_INFORMACION): string {
  if (!value) return empty;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return SIN_INFORMACION;
  return parsed.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

export function gatewayUrls(origin: string): { esquema: string; conectorRemoto: string; base: string } {
  const base = origin.replace(/\/+$/, "");
  return {
    base,
    esquema: `${base}/api/ia/v1/openapi.json`,
    conectorRemoto: `${base}/api/ia/v1/mcp`,
  };
}
