/**
 * v1.44.2 — single axios client for both server- and client-side
 * Next.js code.
 *
 * Two base URLs:
 *   - On the server (RSC / route handlers): API_INTERNAL_URL points at
 *     the FastAPI service over the docker network (http://console:8000).
 *   - In the browser: NEXT_PUBLIC_API_BASE points at the public origin
 *     (http://localhost:8000 in dev, the CDN/LB in prod).
 *
 * Cookies are forwarded automatically by axios `withCredentials: true`;
 * the JWT lives in an httpOnly cookie set by FastAPI's /api/auth/login.
 *
 * NEVER put API keys or secrets in NEXT_PUBLIC_* — those values ship to
 * the browser. The R1 Security review of v1.44.2 makes this an explicit
 * test (tests/test_v1442_nextjs_scaffold.py).
 */
import axios, { type AxiosInstance } from "axios";

const isServer = typeof window === "undefined";

const baseURL = isServer
  ? process.env.API_INTERNAL_URL || "http://console:8000"
  : process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export const api: AxiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

/** Cheap discriminator for axios errors. */
export function isApiError(value: unknown): value is { response?: { status?: number; data?: unknown }; message: string } {
  return typeof value === "object" && value !== null && "message" in value;
}
