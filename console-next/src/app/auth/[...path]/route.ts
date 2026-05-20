/**
 * v1.44.3.2.2 R-Mac-4 — catch-all proxy for /auth/* → FastAPI.
 *
 * Mirrors the /api/* proxy but for the `/auth/...` family
 * (login, logout, refresh). The Next.js login form posts to
 * `/auth/login` on the SAME origin (:3000); this handler runs
 * server-side and forwards to FastAPI inside the docker
 * network, so the browser never sees a cross-origin request.
 */
import { type NextRequest } from "next/server";
import { BACKEND_URL, proxyTo } from "@/lib/proxy";

type Ctx = { params: Promise<{ path: string[] }> };

async function handler(request: NextRequest, { params }: Ctx) {
  const { path } = await params;
  const subpath = path.join("/");
  const search = request.nextUrl.search;
  const targetUrl = `${BACKEND_URL}/auth/${subpath}${search}`;
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
