/**
 * v1.44.3.2.2 R-Mac — explicit login flow for the Next.js
 * console, mirroring the CSRF dance Codex's diagnostic
 * documented:
 *
 *   1. GET  /login     → backend sets the csrf_token cookie
 *   2. POST /auth/login with:
 *        - Content-Type: application/json
 *        - X-CSRF-Token: <csrf_token cookie value>
 *        - credentials: 'include'  (so the cookie round-trips)
 *        - body: { email, password }
 *   3. Response 200 + mod_session + refresh_token cookies.
 *
 * NOT using the axios instance from lib/api.ts because:
 *   - the login flow is the ONLY non-mutation request that needs
 *     a tight 2-step sequence (GET → POST), so plumbing it through
 *     the interceptor would obscure the discovered contract;
 *   - axios's `withCredentials` doesn't always survive Next.js
 *     route handlers / SSR transitions; fetch with `credentials:
 *     "include"` is the documented browser-side default.
 */
import { readCookie } from "@/lib/api";

const BACKEND_URL =
  (typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_BASE
      || process.env.NEXT_PUBLIC_BACKEND_URL
    : process.env.API_INTERNAL_URL)
  || "http://localhost:8000";

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

/**
 * Run the 2-step CSRF login flow. Throws a LoginError with a
 * useful message on any failure path (no CSRF cookie, 401, 5xx,
 * network error). On success returns the parsed JSON body.
 */
export async function loginUser(email: string, password: string): Promise<unknown> {
  // Step 1: GET /login to seed the csrf_token cookie. The response
  // body is the HTML page; we only care about the Set-Cookie side
  // effect, but the browser handles that for us.
  try {
    await fetch(`${BACKEND_URL}/login`, {
      method: "GET",
      credentials: "include",
    });
  } catch (err) {
    throw makeError(
      "No se pudo contactar al backend. Verifica que la consola esté arriba.",
    );
  }

  const csrfToken = readCookie("csrf_token");
  if (!csrfToken) {
    throw makeError(
      "El backend no devolvió el token CSRF. Verifica que /login responde 200.",
    );
  }

  // Step 2: POST /auth/login with the CSRF header.
  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token":  csrfToken,
      },
      body: JSON.stringify({ email, password }),
    });
  } catch (err) {
    throw makeError(
      "Error de red contactando al backend.",
    );
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
  return response.json().catch(() => ({ ok: true }));
}
