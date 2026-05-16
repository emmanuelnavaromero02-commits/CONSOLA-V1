/**
 * v1.44.3.2.2 R-Mac-4 — Next.js server-side proxy helper.
 *
 * After three rounds of CORS firefighting (R-Mac, R-Mac-3, review
 * fixes) the browser was still rejecting credentialed
 * cross-origin requests on Codex's Mac. Strategy pivot: stop
 * fighting CORS. The Next.js app at :3000 now serves the API
 * surface AS ITSELF, then proxies to the FastAPI backend
 * server-to-server inside the docker network. Same-origin from
 * the browser's perspective → no preflight, no Allow-Origin
 * negotiation, no Set-Cookie domain-rewriting headaches.
 *
 * Used by:
 *   - app/api/[...path]/route.ts    → ${BACKEND}/api/...
 *   - app/auth/[...path]/route.ts   → ${BACKEND}/auth/...
 *   - app/login-proxy/route.ts      → ${BACKEND}/login (HTML)
 */
import { NextResponse, type NextRequest } from "next/server";

export const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL
  || process.env.API_INTERNAL_URL
  || "http://console:8000";

// Hop-by-hop request headers we strip before forwarding. `host`
// would point the upstream at :3000 instead of the backend;
// `content-length` / `transfer-encoding` would lie about the
// re-encoded body length.
const REQ_DROP = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-length",
  "te",
  "trailer",
  "upgrade",
  "proxy-authorization",
  "proxy-authenticate",
]);

// Response headers we strip from upstream. We re-encode the body
// via `.text()`, so framing headers from upstream are no longer
// truthful and would confuse browsers (Chrome aborts the response
// when content-length doesn't match the bytes it actually received).
const RES_DROP = new Set([
  "content-length",
  "content-encoding",
  "transfer-encoding",
  "connection",
  "keep-alive",
]);

export interface ProxyOptions {
  /** Absolute upstream URL to forward to. */
  targetUrl: string;
}

export async function proxyTo(
  request: NextRequest,
  { targetUrl }: ProxyOptions,
): Promise<NextResponse> {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!REQ_DROP.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });

  let body: BodyInit | null = null;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "Proxy error",
        detail: err instanceof Error ? err.message : String(err),
        target: targetUrl,
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!RES_DROP.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  });

  // Set-Cookie can appear multiple times in the upstream response
  // (FastAPI emits separate headers for csrf_token, mod_session,
  // refresh_token). `Headers.forEach` collapses them with ", "
  // which breaks the browser's cookie parser. Re-append each value
  // individually via `getSetCookie()` (Node ≥18.14, supported by
  // Next 14's runtime).
  const getSetCookie = (upstream.headers as { getSetCookie?: () => string[] }).getSetCookie;
  if (typeof getSetCookie === "function") {
    responseHeaders.delete("set-cookie");
    for (const cookie of getSetCookie.call(upstream.headers)) {
      responseHeaders.append("set-cookie", cookie);
    }
  }

  const responseBody = await upstream.text();
  return new NextResponse(responseBody, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
