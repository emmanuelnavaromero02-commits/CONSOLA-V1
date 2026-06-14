/**
 * v1.44.3.2.2 R-Mac-4 — same-origin login flow.
 *
 * Earlier R-Mac iterations fired credentialed XHRs straight at
 * the FastAPI backend on :8000, and Chrome's CORS preflight
 * dance kept stripping Allow-Origin even after R-Mac-3 moved
 * CORSMiddleware to OUTERMOST. The R-Mac-4 pivot drops CORS
 * from the picture entirely: FastAPI serves the static Next export
 * and the browser stays on FastAPI's same origin. This static build
 * no longer relies on Next.js route handlers or a proxy layer.
 *
 * Discovered CSRF dance — unchanged on the wire, just same-origin
 * now:
 *
 *   1. GET  /login         → FastAPI /login → sets
 *                             csrf_token cookie on this domain
 *                             (browser stores it because the
 *                             response came from the same origin).
 *   2. POST /auth/login    → FastAPI /auth/login
 *                             with X-CSRF-Token + JSON body.
 *   3. Response 200        → mod_session + refresh_token cookies
 *                             land on this domain, browser
 *                             retains them without any CORS
 *                             credentialed-request negotiation.
 *
 * Uses the central same-origin fetch helper so login emits the same
 * request-id and credential semantics as the authenticated modules.
 */
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

/**
 * Run the 2-step CSRF login flow. Throws a LoginError with a
 * useful message on any failure path (no CSRF cookie, 401, 5xx,
 * network error). On success returns the parsed JSON body.
 *
 * Both URLs are relative to the FastAPI origin serving the static app.
 */
export async function loginUser(email: string, password: string): Promise<unknown> {
  deleteCookie(ACTIVE_WORKSPACE_COOKIE);
  // Step 1: GET /login to seed the csrf_token cookie.
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

  // Some browsers do not expose the Set-Cookie value through
  // document.cookie on the exact same tick the fetch resolves.
  // Retry briefly before reporting the hard CSRF failure.
  const csrfToken = await readCookieEventually("csrf_token");
  if (!csrfToken) {
    throw makeError(
      "El backend no devolvió el token CSRF. Verifica que /login responde 200.",
    );
  }

  // Step 2: POST /auth/login with the CSRF header. Goes through
  // FastAPI receives this directly on the same origin.
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
    // Try to extract a useful detail from the JSON body — FastAPI
    // emits `{detail: "..."}` for HTTPException paths. Fall back to
    // a generic message keyed off status.
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

  // 200 OK — the backend has set mod_session + refresh_token
  // cookies on the response. Return the parsed body so the caller
  // can use any payload the backend emits (currently {ok, user}).
  deleteCookie(ACTIVE_WORKSPACE_COOKIE);
  return response.json().catch(() => ({ ok: true }));
}
