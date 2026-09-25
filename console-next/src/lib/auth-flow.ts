import { apiFetch } from "@/lib/api";
import { deleteCookie, readCookie } from "@/lib/cookies";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

export interface LoginError extends Error {
  status?: number;
  detail?: string;
}

function makeError(message: string, status?: number, detail?: string): LoginError {
  const e = new Error(message) as LoginError;
  e.status = status;
  e.detail = detail;
  return e;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function timeoutSignal(): { signal: AbortSignal; clear: () => void } {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 10_000);
  return {
    signal: controller.signal,
    clear: () => window.clearTimeout(timeout),
  };
}

async function readCookieEventually(name: string): Promise<string | null> {
  for (let i = 0; i < 8; i++) {
    const value = readCookie(name);
    if (value) return value;
    await sleep(25);
  }
  return readCookie(name);
}

export async function loginUser(email: string, password: string): Promise<unknown> {
  deleteCookie(ACTIVE_WORKSPACE_COOKIE);
  let csrfResponse: Response;
  const csrfTimeout = timeoutSignal();
  try {
    csrfResponse = await apiFetch("/login", {
      method: "GET",
      signal: csrfTimeout.signal,
    });
  } catch {
    throw makeError(
      "No se pudo contactar al backend. Verifica que la consola esté arriba.",
    );
  } finally {
    csrfTimeout.clear();
  }

  if (!csrfResponse.ok) {
    throw makeError(
      `El backend no pudo preparar CSRF (HTTP ${csrfResponse.status}).`,
      csrfResponse.status,
    );
  }

  const csrfToken = await readCookieEventually("csrf_token");
  if (!csrfToken) {
    throw makeError(
      "El backend no devolvió el token CSRF. Verifica que /login responde 200.",
    );
  }

  let response: Response;
  const loginTimeout = timeoutSignal();
  try {
    response = await apiFetch("/auth/login", {
      method: "POST",
      headers: {
        "X-CSRF-Token":  csrfToken,
      },
      json: { email, password },
      signal: loginTimeout.signal,
    });
  } catch {
    throw makeError(
      "Error de red contactando al backend.",
    );
  } finally {
    loginTimeout.clear();
  }

  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = await response.json();
      detail = body?.detail;
    } catch { /* ignore — body wasn't JSON */ }

    const message =
      response.status === 401 ? "Email o contraseña incorrectos." :
      response.status === 403 ? "CSRF rechazado. Recarga la página y vuelve a intentar." :
      response.status === 429 ? "Demasiados intentos. Espera un momento." :
      detail || `Error de autenticación (HTTP ${response.status}).`;
    throw makeError(message, response.status, detail);
  }

  deleteCookie(ACTIVE_WORKSPACE_COOKIE);
  return response.json().catch(() => ({ ok: true }));
}
