/**
 * v1.44.3.2.2 R-Mac-4 — GET proxy for the backend's `/login`
 * HTML page.
 *
 * The Next.js console has its own client-rendered `/login` page
 * (app/login/page.tsx), so we can't shadow that path with a
 * proxy. Instead the CSRF dance asks for `/login-proxy`, which
 * we forward verbatim to the backend's `/login` route. The only
 * thing the client cares about from this round-trip is the
 * `Set-Cookie: csrf_token=…` header — the body is throwaway.
 */
import { type NextRequest } from "next/server";
import { BACKEND_URL, proxyTo } from "@/lib/proxy";

export async function GET(request: NextRequest) {
  return proxyTo(request, { targetUrl: `${BACKEND_URL}/login` });
}

export const dynamic = "force-dynamic";
