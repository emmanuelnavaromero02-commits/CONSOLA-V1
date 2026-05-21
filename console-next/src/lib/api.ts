/**
 * v1.44.3.2.2 R-Mac-4 — single axios client for both server-
 * and client-side Next.js code.
 *
 * Browser-side requests hit the SAME ORIGIN as the Next.js app
 * (typically http://localhost:3000), and the Next.js catch-all
 * proxy at /api/[...path] forwards each call to the FastAPI
 * backend over the docker network. So baseURL is empty — every
 * `api.get("/api/dashboard/kpis")` resolves to
 * `http://localhost:3000/api/dashboard/kpis`, gets handled by
 * the Next.js server-side route handler, and returns whatever
 * FastAPI emitted. No cross-origin request ever leaves the tab.
 *
 * Server-side (RSC, route handlers, server actions) needs to
 * talk to the backend directly — there's no browser to
 * intercept the relative path, and routing through our own
 * proxy would deadlock the Next.js runtime. So when window is
 * undefined we use BACKEND_INTERNAL_URL (→ http://console:8000
 * in docker).
 *
 * The browser axios instance auto-attaches the ``X-CSRF-Token``
 * header on every non-GET request by reading the ``csrf_token``
 * cookie that FastAPI seeds on GET /login (proxied via
 * /login-proxy). With ``withCredentials: true`` the cookie
 * itself round-trips automatically — the interceptor just
 * echoes the value as a header so the backend's
 * double-submit-cookie CSRF check passes.
 *
 * NEVER put API keys or secrets in NEXT_PUBLIC_* — those values
 * ship to the browser. Enforced by
 * tests/test_v1442_nextjs_scaffold.py.
 */
import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";
import { readCookie } from "@/lib/cookies";

const isServer = typeof window === "undefined";

// Server-side fetches go direct to FastAPI inside the docker
// network. Browser fetches use the empty baseURL so they resolve
// against the Next.js origin and hit the same-origin proxy.
const baseURL = isServer
  ? (process.env.BACKEND_INTERNAL_URL
      || process.env.API_INTERNAL_URL
      || "http://console:8000")
  : "";

export const api: AxiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

export { readCookie };

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
