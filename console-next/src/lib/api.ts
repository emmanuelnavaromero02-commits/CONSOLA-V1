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

function toApiError(message: string, status?: number, data?: unknown, requestId?: string): ApiError {
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

  let body = init.body ?? undefined;
  if (init.json !== undefined) {
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }

  if (method !== "GET" && method !== "HEAD") {
    const csrf = csrfToken();
    if (csrf && !headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", csrf);
  }

  // Scope every request to the workspace the user picked in the shell
  // switcher (persisted as a cookie). The backend auth middleware reads
  // this header to resolve the active workspace/tenant.
  if (!headers.has("X-Workspace-Id")) {
    const activeWorkspace = readCookie(ACTIVE_WORKSPACE_COOKIE);
    if (activeWorkspace) headers.set("X-Workspace-Id", activeWorkspace);
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

function errorMessage(status: number, payload: unknown): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  if (typeof payload === "string" && payload.trim()) return payload;
  if (status === 401) return "Sesión expirada o no autenticada.";
  if (status === 403) return "No tienes permisos para esta acción.";
  if (status === 404) return "Recurso no encontrado.";
  if (status >= 500) return "El backend no pudo completar la solicitud.";
  return `HTTP ${status}`;
}

async function request<T>(
  method: string,
  path: string,
  body?: JsonBody,
): Promise<ApiResponse<T>> {
  const requestId = makeRequestId();
  let response: Response;
  try {
    response = await apiFetch(path, { method, headers: { "X-Request-ID": requestId }, json: body });
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
    throw toApiError(errorMessage(response.status, parsed), response.status, parsed, responseRequestId);
  }

  return {
    data: parsed as T,
    status: response.status,
    headers: response.headers,
    requestId: responseRequestId,
  };
}

export const api = {
  get: <T = unknown>(path: string) => request<T>("GET", path),
  post: <T = unknown>(path: string, body?: JsonBody) => request<T>("POST", path, body),
  put: <T = unknown>(path: string, body?: JsonBody) => request<T>("PUT", path, body),
  patch: <T = unknown>(path: string, body?: JsonBody) => request<T>("PATCH", path, body),
  delete: <T = unknown>(path: string, body?: JsonBody) => request<T>("DELETE", path, body),
};

export function isApiError(value: unknown): value is ApiError {
  return typeof value === "object" && value !== null && "message" in value;
}
