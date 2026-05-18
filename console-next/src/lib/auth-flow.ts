/**
 * v1.44.3.2.2 R-Mac-4 — same-origin login flow.
 *
 * Earlier R-Mac iterations fired credentialed XHRs straight at
 * the FastAPI backend on :8000, and Chrome's CORS preflight
 * dance kept stripping Allow-Origin even after R-Mac-3 moved
 * CORSMiddleware to OUTERMOST. The R-Mac-4 pivot drops CORS
 * from the picture entirely: the Next.js app serves
 * `/login-proxy`, `/auth/login`, `/api/*` AS ITSELF and
 * forwards to FastAPI inside the docker network.
 *
 * Discovered CSRF dance — unchanged on the wire, just same-origin
 * now:
 *
 *   1. GET  /login-proxy   → Next proxy → FastAPI /login → sets
 *                             csrf_token cookie on this domain
 *                             (browser stores it because the
 *                             response came from :3000, not
 *                             cross-origin from :8000).
 *   2. POST /auth/login    → Next proxy → FastAPI /auth/login
 *                             with X-CSRF-Token + JSON body.
 *   3. Response 200        → mod_session + refresh_token cookies
 *                             land on this domain, browser
 *                             retains them without any CORS
 *                             credentialed-request negotiation.
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
 * Both URLs are RELATIVE — the browser resolves them against the
 * Next.js origin (typically http://localhost:3000), and the
 * Next.js server-side proxy forwards to FastAPI. No cross-origin
 * request ever leaves the tab.
 */
export async function loginUser(email: string, password: string): Promise<unknown> {
  // Step 1: GET /login-proxy to seed the csrf_token cookie. We
  // hit `/login-proxy` instead of `/login` because the Next.js
  // app has its own client-rendered /login page; the proxy
  // route lives under a different path so they don't collide.
  let csrfResponse: Response;
  try {
    csrfResponse = await fetch("/login-proxy", {
      method: "GET",
      credentials: "include",
    });
  } catch (err) {
    throw makeError(
      "No se pudo contactar al backend. Verifica que la consola esté arriba.",
    );
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
  // the /auth/[...path] catch-all proxy.
  let response: Response;
  try {
    response = await fetch("/auth/login", {
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
