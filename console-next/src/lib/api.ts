/**
 * v1.44.3.2.2 R-Mac follow-up — single axios client for both
 * server- and client-side Next.js code, with the REAL CSRF flow
 * Codex's diagnostic uncovered baked in.
 *
 * Two base URLs:
 *   - Server (RSC / route handlers): API_INTERNAL_URL → http://console:8000
 *   - Browser:                         NEXT_PUBLIC_API_BASE → http://localhost:8000
 *
 * The browser axios instance auto-attaches the ``X-CSRF-Token``
 * header on every non-GET request by reading the ``csrf_token``
 * cookie that FastAPI seeds on GET /login. With ``withCredentials:
 * true`` the cookie itself round-trips automatically — the
 * interceptor just echoes the value as a header so the backend's
 * double-submit-cookie CSRF check passes.
 *
 * NEVER put API keys or secrets in NEXT_PUBLIC_* — those values
 * ship to the browser. Enforced by
 * tests/test_v1442_nextjs_scaffold.py.
 */
import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";

const isServer = typeof window === "undefined";

const baseURL = isServer
  ? process.env.API_INTERNAL_URL || "http://console:8000"
  : process.env.NEXT_PUBLIC_API_BASE
    || process.env.NEXT_PUBLIC_BACKEND_URL
    || "http://localhost:8000";

export const api: AxiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

/**
 * Read a browser cookie by name. Returns null when:
 *   - we're on the server (no document)
 *   - the cookie isn't present
 *
 * The CSRF cookie is set by FastAPI on GET /login (the page render
 * AND the auth.cookie middleware seed). Helper used by both the
 * interceptor below and the explicit loginUser flow.
 */
export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  for (const raw of document.cookie.split(";")) {
    const c = raw.trim();
    if (c.startsWith(prefix)) {
      return decodeURIComponent(c.slice(prefix.length));
    }
  }
  return null;
}

// v1.44.3.2.2 R-Mac: every non-GET request that goes through this
// axios instance carries the X-CSRF-Token header. The backend's
// double-submit-cookie check compares this header against the
// csrf_token cookie value; without it every POST/PUT/DELETE 403s.
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const method = (config.method || "get").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
    const token = readCookie("csrf_token");
    if (token) {
      config.headers = config.headers || {};
      // axios v1 InternalAxiosRequestConfig.headers is a
      // AxiosHeaders proxy; bracket assignment is the supported
      // way to add a custom header that survives the request build.
      (config.headers as Record<string, string>)["X-CSRF-Token"] = token;
    }
  }
  return config;
});

/** Cheap discriminator for axios errors. */
export function isApiError(value: unknown): value is { response?: { status?: number; data?: unknown }; message: string } {
  return typeof value === "object" && value !== null && "message" in value;
}
