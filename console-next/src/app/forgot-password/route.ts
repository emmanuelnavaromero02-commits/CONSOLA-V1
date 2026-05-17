/**
 * v1.44.3.3 R-Mac-Round-3 Task D — forgot-password page proxy.
 *
 * The backend serves the HTML form at GET ``/forgot-password``
 * (FastAPI route that ALSO seeds the csrf_token cookie so the
 * form's POST /auth/forgot-password call passes the CSRF
 * double-submit-cookie gate). The Next.js console proxies the
 * same path so the "¿Olvidaste tu contraseña?" link from the
 * login page resolves to a real page on the :3000 origin
 * instead of bouncing through the auth middleware.
 *
 * The POST /auth/forgot-password action is already covered by
 * the /auth/[...path] catch-all (it forwards to FastAPI's
 * POST /auth/forgot-password).
 */
import { type NextRequest } from "next/server";
import { BACKEND_URL, proxyTo } from "@/lib/proxy";

export async function GET(request: NextRequest) {
  return proxyTo(request, { targetUrl: `${BACKEND_URL}/forgot-password` });
}

export const dynamic = "force-dynamic";
