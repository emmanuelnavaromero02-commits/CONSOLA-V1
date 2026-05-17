/**
 * v1.44.3.3 R-Mac-Round-3 Task D — reset-password page proxy.
 *
 * Pairs with /forgot-password — the reset-email link the
 * backend sends points at ``/reset-password?token=...``; that
 * URL needs to resolve on the :3000 origin so the Next.js
 * middleware doesn't bounce it through /login when the user
 * isn't authenticated yet.
 */
import { type NextRequest } from "next/server";
import { BACKEND_URL, proxyTo } from "@/lib/proxy";

export async function GET(request: NextRequest) {
  const search = request.nextUrl.search;
  return proxyTo(request, { targetUrl: `${BACKEND_URL}/reset-password${search}` });
}

export const dynamic = "force-dynamic";
