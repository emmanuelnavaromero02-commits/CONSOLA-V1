/**
 * v1.44.3.2.2 R-Mac-4 — catch-all proxy for /api/* → FastAPI.
 *
 * The Next.js console at :3000 used to fire cross-origin XHRs
 * directly at the FastAPI backend on :8000. Codex's Mac kept
 * showing CORS preflight failures even after three rounds of
 * middleware-order surgery; the pivot is to make every browser
 * call same-origin and have Next.js's server-side runtime proxy
 * to the backend over the docker network instead.
 *
 * /api/health stays on its own route.ts (Next picks static
 * segments over catch-alls), so this handler only kicks in for
 * the dynamic API surface (dashboard kpis, cartridges, copilot
 * workflows, etc.).
 */
import { type NextRequest } from "next/server";
import { BACKEND_URL, proxyTo } from "@/lib/proxy";

type Ctx = { params: Promise<{ path: string[] }> };

async function handler(request: NextRequest, { params }: Ctx) {
  // [...path] strips the literal `/api/` segment, so we put it
  // back when targeting the upstream — the FastAPI routes are
  // mounted under `/api/...`.
  const { path } = await params;
  const subpath = path.join("/");
  const search = request.nextUrl.search;
  const targetUrl = `${BACKEND_URL}/api/${subpath}${search}`;
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
