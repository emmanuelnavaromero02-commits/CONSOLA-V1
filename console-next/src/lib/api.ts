import { readCookie } from "@/lib/cookies";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

export { readCookie };

export interface ApiResponse<T> {
  data: T;
  status: number;
  headers: Headers;
  requestId: string;
}

export interface ApiError extends Error {
  status?: number;
  data?: unknown;
  requestId?: string;
}

type JsonBody = unknown;

export interface ApiRequestOptions {
  headers?: HeadersInit;
}

export type ApiFetchInit = Omit<RequestInit, "body"> & {
  body?: BodyInit | null;
  json?: JsonBody;
  timeoutMs?: number;
};

const DEFAULT_API_TIMEOUT_MS = 30_000;

function makeRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

function csrfToken(): string | null {
  return readCookie("csrf_token");
}

export function toApiError(message: string, status?: number, data?: unknown, requestId?: string): ApiError {
  const error = new Error(message) as ApiError;
  error.status = status;
  error.data = data;
  error.requestId = requestId;
  return error;
}

async function parsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => null);
  }
  const text = await response.text().catch(() => "");
  return text || null;
}

export async function apiFetch(path: string, init: ApiFetchInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  const requestId = headers.get("X-Request-ID") || makeRequestId();
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (!headers.has("X-Request-ID")) headers.set("X-Request-ID", requestId);
  const activeWorkspaceId = readCookie(ACTIVE_WORKSPACE_COOKIE);
  if (activeWorkspaceId && !headers.has("X-Workspace-Id")) {
    headers.set("X-Workspace-Id", activeWorkspaceId);
  }

  let body = init.body ?? undefined;
  if (init.json !== undefined) {
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }

  if (method !== "GET" && method !== "HEAD") {
    const csrf = csrfToken();
    if (csrf && !headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", csrf);
  }

  const timeoutMs = init.timeoutMs === undefined ? DEFAULT_API_TIMEOUT_MS : init.timeoutMs;
  const controller = timeoutMs === 0 ? null : new AbortController();
  let timedOut = false;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const externalSignal = init.signal;
  const onExternalAbort = () => controller?.abort(externalSignal?.reason);
  if (controller && externalSignal) {
    if (externalSignal.aborted) {
      controller.abort(externalSignal.reason);
    } else {
      externalSignal.addEventListener("abort", onExternalAbort, { once: true });
    }
  }
  if (controller && timeoutMs > 0) {
    timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }

  try {
    return await fetch(path, {
      ...init,
      method,
      credentials: init.credentials ?? "include",
      headers,
      body,
      signal: controller?.signal ?? externalSignal,
    });
  } catch (error) {
    if (timedOut) {
      throw toApiError("La consulta tardó demasiado", 408, { timeout_ms: timeoutMs }, requestId);
    }
    throw error;
  } finally {
    if (timeout) clearTimeout(timeout);
    if (controller && externalSignal) {
      externalSignal.removeEventListener("abort", onExternalAbort);
    }
  }
}

const UNSAFE_DETAIL_MARKERS: RegExp[] = [
  /traceback/i,
  /exception/i,
  /stacktrace/i,
  /sqlstate/i,
  /select /i,
  /insert /i,
  /update /i,
  /delete from/i,
  /psycopg/i,
  /sqlalchemy/i,
  /\.py["':]/i,
  /(?:\/home\/|\/usr\/|\/var\/|\/app\/)/i,
  /0x[0-9a-f]{6,}/i,
  /[0-9a-f]{32,}/i,
];

function isSafeUserDetail(detail: string): boolean {
  const trimmed = detail.trim();
  if (!trimmed) return false;
  if (trimmed.length > 240) return false;
  if (/[\r\n]/.test(trimmed)) return false;
  return !UNSAFE_DETAIL_MARKERS.some((marker) => marker.test(trimmed));
}

export function publicErrorMessage(status: number, payload: unknown, requestId?: string): string {
  if (status < 500 && payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && isSafeUserDetail(detail)) return detail.trim();
  }
  if (status === 401) return "Sesión expirada o no autenticada.";
  if (status === 403) return "No tienes permisos para esta acción.";
  if (status === 404) return "Recurso no encontrado.";
  if (status >= 500) {
    return requestId
      ? `El backend no pudo completar la solicitud. Ref: ${requestId}`
      : "El backend no pudo completar la solicitud.";
  }
  return requestId
    ? `No se pudo completar la solicitud (HTTP ${status}). Ref: ${requestId}`
    : `No se pudo completar la solicitud (HTTP ${status}).`;
}

async function request<T>(
  method: string,
  path: string,
  body?: JsonBody,
  options: ApiRequestOptions = {},
): Promise<ApiResponse<T>> {
  const headers = new Headers(options.headers);
  const requestId = headers.get("X-Request-ID") || makeRequestId();
  if (!headers.has("X-Request-ID")) headers.set("X-Request-ID", requestId);
  let response: Response;
  try {
    response = await apiFetch(path, { method, headers, json: body });
  } catch (error) {
    if (isApiError(error)) throw error;
    throw toApiError(
      error instanceof Error ? error.message : "Error de red",
      undefined,
      undefined,
      requestId,
    );
  }

  const responseRequestId = response.headers.get("x-request-id") || requestId;
  const parsed = await parsePayload(response);
  if (!response.ok) {
    throw toApiError(publicErrorMessage(response.status, parsed, responseRequestId), response.status, parsed, responseRequestId);
  }

  return {
    data: parsed as T,
    status: response.status,
    headers: response.headers,
    requestId: responseRequestId,
  };
}

export const api = {
  get: <T = unknown>(path: string, options?: ApiRequestOptions) => request<T>("GET", path, undefined, options),
  post: <T = unknown>(path: string, body?: JsonBody, options?: ApiRequestOptions) => request<T>("POST", path, body, options),
  put: <T = unknown>(path: string, body?: JsonBody, options?: ApiRequestOptions) => request<T>("PUT", path, body, options),
  patch: <T = unknown>(path: string, body?: JsonBody, options?: ApiRequestOptions) => request<T>("PATCH", path, body, options),
  delete: <T = unknown>(path: string, body?: JsonBody, options?: ApiRequestOptions) => request<T>("DELETE", path, body, options),
};

export function isApiError(value: unknown): value is ApiError {
  return typeof value === "object" && value !== null && "message" in value;
}
