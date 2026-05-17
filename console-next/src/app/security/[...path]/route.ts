/**
 * v1.44.4 Group 1 — catch-all proxy for /security/* → FastAPI.
 *
 * The Operations module reads /security/audit (and will later
 * consume /security/sessions, /security/permissions,
 * /security/login-attempts, /security/access-check). The
 * FastAPI router uses the bare ``/security`` prefix — NOT
 * ``/api/security`` — so the existing /api/[...path] catch-all
 * doesn't cover it. This mirror handler is the missing piece.
 *
 * Note: ``/security`` (without subpath) renders the legacy HTML
 * security page on FastAPI. We do NOT proxy GET /security
 * here — Next.js's static routing serves any /security page.tsx
 * if one exists. Catch-all routes only match SEGMENTS after
 * /security/, so this handler can't accidentally shadow a
 * future /security index page.
 */
import { type NextRequest } from "next/server";
import { BACKEND_URL, proxyTo } from "@/lib/proxy";

type Ctx = { params: { path: string[] } };

async function handler(request: NextRequest, { params }: Ctx) {
  const subpath = params.path.join("/");
  const search  = request.nextUrl.search;
  const targetUrl = `${BACKEND_URL}/security/${subpath}${search}`;
  return proxyTo(request, { targetUrl });
}

export {
  handler as GET,
  handler as POST,
  handler as PUT,
  handler as PATCH,
  handler as DELETE,
  handler as OPTIONS,
};

export const dynamic = "force-dynamic";
